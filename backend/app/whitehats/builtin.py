"""Built-in WhiteHats. Each is small on purpose: one area, plain rules, signals a person can act on."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from typing import ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..models import Organization
from .base import Context, WhiteHat
from .signals import ADMIN_GRANTED, CLOUD_RISKY, ENDPOINT_SUSPICIOUS, IDENTITY_BURST, INTERNET_OPEN, LOG_SHAPE_PREFIX

FAILED_AUTH = ("authentication failure", "authentication failed", "failed password", "invalid user", "login failed",
               "failed login", "logon failure", "status=401")
SUCCESS_AUTH = ("accepted password", "accepted publickey", "session opened for user", "login succeeded",
                "successful login", "consolelogin")


class Identity(WhiteHat):
    key, name = "identity", "Identity"
    domain = "Sign-ins: log lines and the AWS console"
    description = ("Flags failed sign-in bursts in log lines, AWS console sign-ins without multi-factor, and, from the "
                   "company's sign-in provider, people and administrators without multi-factor and unused accounts.")
    data_sources = ("logs", "cloud_audit", "identity")
    source_labels: ClassVar[dict[str, str]] = {"cloud_audit": "AWS console sign-ins from CloudTrail",
                                               "identity": "Okta, Entra ID or Google Workspace people"}
    BURST = IDENTITY_BURST

    def on_log_batch(self, ctx: Context, source_host: str, lines: list[dict]) -> None:
        self._console(ctx, lines)
        per_source = Counter()
        sample = {}
        for ln in lines:
            low = str(ln.get("message", "")).lower()
            if any(p in low for p in FAILED_AUTH):
                src = ln.get("source", "")
                per_source[src] += 1
                sample.setdefault(src, ln.get("message", "")[:300])
        for src, n in per_source.items():
            if n >= self.BURST:
                ctx.signal("high" if n >= 4 * self.BURST else "medium",
                           f"{n} failed sign-ins in one batch on {source_host or 'a host'}",
                           f"Source {src}. Example: {sample[src]}", entity=source_host, mitre="T1110")

    def _console(self, ctx: Context, lines: list[dict]) -> None:
        """AWS console sign-ins (13A): a person who signed in with a password alone, and one who failed BURST times or
        more in a batch. Root sign-ins and single failures are Cloud Posture's."""
        failed: Counter = Counter()
        for ln in lines:
            msg = str(ln.get("message", ""))
            if '"ConsoleLogin"' not in msg:
                continue
            try:
                ev = json.loads(msg)
            except ValueError:
                continue
            if ev.get("eventName") != "ConsoleLogin":
                continue
            who = ev.get("userIdentity") or {}
            name = who.get("arn") or who.get("userName") or who.get("type", "someone")
            where = f"{ev.get('awsRegion', '')} {ev.get('recipientAccountId', '')}".strip()
            result = (ev.get("responseElements") or {}).get("ConsoleLogin")
            if result == "Failure":
                failed[name] += 1
            elif result == "Success" and who.get("type") == "IAMUser" and \
                    (ev.get("additionalEventData") or {}).get("MFAUsed") == "No":
                ctx.signal("medium", "Signed in to the AWS console without multi-factor",
                           f"{name} signed in with a password alone, from {ev.get('sourceIPAddress', '?')} in {where}. "
                           "Turn on multi-factor sign-in for this user, or remove their console password.",
                           entity=where, mitre="T1078.004", resource=str(who.get("userName") or name))
        for name, n in failed.items():
            if n >= self.BURST:
                ctx.signal("high", f"{n} failed AWS console sign-ins for {name}",
                           "Someone may be guessing this user's password.", entity=name, mitre="T1110",
                           resource=name)


class Endpoint(WhiteHat):
    key, name = "endpoint", "Endpoint"
    domain = "Security log shapes and suspicious processes"
    description = ("Raises new, spiking or error log shapes that look security-related, "
                   "and a short list of suspicious process names.")
    data_sources = ("logs", "processes")
    SUSPICIOUS: ClassVar[dict[str, str]] = ENDPOINT_SUSPICIOUS

    def on_log_window(self, ctx: Context, items: list[dict]) -> None:
        for it in items:
            # Security shapes alert when new, spiking, or errors (a burst of failures on day one counts).
            if not (it["security"] and (it["new"] or it["spike"] or it["level"] == "error")):
                continue
            ctx.signal("high" if it["level"] == "error" else "medium",
                       f"{LOG_SHAPE_PREFIX}{it['pattern'][:120]}",
                       f"{it['count']} lines in the window ({', '.join(it['reasons'])}). Example: {it['sample'][:300]}",
                       entity=it.get("source", "")[:160])

    def on_processes(self, ctx: Context, host: str, processes: list[dict]) -> None:
        for p in processes:
            name = str(p.get("name", "")).lower()
            if name in self.SUSPICIOUS:
                ctx.signal("high", f"{p['name']} running on {host}",
                           f"{self.SUSPICIOUS[name].capitalize()} (pid {p.get('pid')}, user {p.get('user') or '?'}). "
                           "Confirm it is expected; if not, isolate the host.", entity=host, mitre="T1059")


class Network(WhiteHat):
    key, name = "network", "Network"
    domain = "Network findings and network traffic"
    description = ("Raises high and critical findings from network collectors, and reads each network's AWS traffic "
                   "log: the internet reaching a database or admin port, unusual outgoing traffic, and large "
                   "transfers out.")
    data_sources = ("network",)
    source_labels: ClassVar[dict[str, str]] = {"network": "Network collectors and AWS traffic logs"}
    BIG_OUT = 1_000_000_000  # bytes to the internet from one address in one batch that count as a large transfer

    def on_log_batch(self, ctx: Context, source_host: str, lines: list[dict]) -> None:
        """Each network's AWS traffic log (NET.2), one connection a line: version account interface from to
        from-port to-port protocol packets bytes start end ACCEPT|REJECT status."""
        from ..services.network import COMMON_OUTBOUND, RISKY_PORTS, _is_private

        out_bytes: Counter = Counter()
        for ln in lines:
            vpc = str(ln.get("source", ""))
            if not vpc.startswith("vpcflow:"):
                continue
            vpc = vpc.split(":", 1)[1]
            parts = str(ln.get("message", "")).split()
            if len(parts) < 14 or parts[12] != "ACCEPT" or not parts[5].isdigit() or not parts[6].isdigit():
                continue
            src, dst, sport, dport = parts[3], parts[4], int(parts[5]), int(parts[6])
            inbound, outbound = not _is_private(src) and _is_private(dst), _is_private(src) and not _is_private(dst)
            if inbound and dport in RISKY_PORTS:
                ctx.signal("high", f"Traffic from the internet reached {RISKY_PORTS[dport]} (port {dport})",
                           f"{src} connected to {dst} on port {dport} in network {vpc}, and the firewall let it "
                           "through. Close this port to the internet, or limit it to known addresses.",
                           entity=dst, mitre="T1190", resource=vpc)
            if outbound:
                if parts[9].isdigit():
                    out_bytes[(vpc, src)] += int(parts[9])
                # A reply to a connection made to us comes from a well-known port to a high one: not ours to judge.
                if dport not in COMMON_OUTBOUND and dport < 32768 and sport >= 1024:
                    ctx.signal("medium", f"Unusual outgoing traffic to port {dport}",
                               f"{src} in network {vpc} connected to {dst} on port {dport}, which servers rarely "
                               "use. Check which program made it; malware often calls home on odd ports.",
                               entity=src, mitre="T1571", resource=vpc)
        for (vpc, src), sent in out_bytes.items():
            if sent >= self.BIG_OUT:
                ctx.signal("medium", "Large data transfer out to the internet",
                           f"{src} in network {vpc} sent {sent / 1e9:.1f} GB to the internet in a few minutes. "
                           "Check this was expected; it is how stolen data leaves.",
                           entity=src, mitre="T1048", resource=vpc)

    def on_network_findings(self, ctx: Context, host: str, new_findings: list) -> None:
        for f in new_findings:
            if f.severity in ("high", "critical"):
                ctx.signal(f.severity, f.title, f.detail, entity=host, mitre="T1046", dedupe_hours=24)


# Where a CloudTrail event names the resource it changed: the first of these request parameters it carries. A trail's
# name may be its ARN, and a queue is named by its URL: both are cut to their last part, the name the map shows.
RESOURCE_KEYS = ("bucketName", "name", "trailName", "groupId", "userName", "keyId", "functionName", "restApiId",
                 "apiId", "tableName", "queueUrl", "roleName")

# The event that undoes a risky change, the signal it closes, and when it counts (None: always).
UNDONE_BY: dict[str, tuple[str, object]] = {
    "StartLogging": ("Cloud audit logging was switched off", None),
    "PutBucketPublicAccessBlock": ("Public access settings on a bucket changed",
                                   lambda p: all((p.get("PublicAccessBlockConfiguration") or {}).get(k) is True for k in (
                                       "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy",
                                       "RestrictPublicBuckets"))),
    "DeleteBucketPolicy": ("A storage bucket policy changed", None),
    "RevokeSecurityGroupIngress": ("A firewall rule now allows traffic from the whole internet", None),
    "DeleteSecurityGroup": ("A firewall rule now allows traffic from the whole internet", None),
    "DetachUserPolicy": (("A policy was attached directly to a user", ADMIN_GRANTED), None),
    "DetachRolePolicy": (("A policy was attached to a role", ADMIN_GRANTED), None),
    "DeleteAccessKey": ("A new access key was created", None),
    "EnableKey": ("An encryption key was disabled", None),
    "CancelKeyDeletion": ("An encryption key was scheduled for deletion", None),
    "DeleteLoginProfile": ("A cloud user was given a console password", None),
    "DeleteUserPolicy": ("A policy was written directly on a user", None),
    "PutBucketEncryption": ("Encryption was switched off on a storage bucket", None),
    "StartConfigurationRecorder": ("AWS Config recording was stopped", None),
    # The user itself is gone: everything raised about it is moot.
    "DeleteUser": (
        (
            "A new cloud user was created",
            "A new access key was created",
            "A policy was attached directly to a user",
            ADMIN_GRANTED,
            "A cloud user was given a console password",
            "A policy was written directly on a user",
        ),
        None,
    ),
}


def resource_of(ev: dict) -> str:
    """The name of the resource a CloudTrail event changed, as the map names it, or empty."""
    params = ev.get("requestParameters") or {}
    for key in RESOURCE_KEYS:
        value = params.get(key)
        if isinstance(value, str) and value:
            return value.rstrip("/").rsplit("/", 1)[-1] if key in ("name", "queueUrl") else value
    for item in ev.get("resources") or []:
        if isinstance(item, dict) and item.get("ARN"):
            return str(item["ARN"])
    return ""


class CloudPosture(WhiteHat):
    key, name = "cloud", "Cloud Posture"
    domain = "AWS CloudTrail changes"
    description = "Reads AWS CloudTrail events delivered through CloudWatch and raises risky changes."
    data_sources = ("cloud_audit",)
    source_labels: ClassVar[dict[str, str]] = {"cloud_audit": "AWS CloudTrail via CloudWatch"}
    RISKY: ClassVar[dict[str, tuple[str, str, str]]] = CLOUD_RISKY

    def on_log_batch(self, ctx: Context, source_host: str, lines: list[dict]) -> None:
        for ln in lines:
            msg = str(ln.get("message", ""))
            if not msg.startswith("{") or "eventName" not in msg:
                continue
            try:
                ev = json.loads(msg)
            except ValueError:
                continue
            name = ev.get("eventName", "")
            who = (ev.get("userIdentity") or {}).get("arn") or (ev.get("userIdentity") or {}).get("type", "unknown")
            where = f"{ev.get('awsRegion', '')} {ev.get('recipientAccountId', '')}".strip()
            what = resource_of(ev)
            on = f" on {what}" if what else ""
            if (ev.get("userIdentity") or {}).get("type") == "Root" and name != "ConsoleLogin":
                ctx.signal("high", f"Root account used for {name}", f"{who} in {where}. Use named roles instead.",
                           entity=where, mitre="T1078.004")
            if name in UNDONE_BY and not ev.get("errorCode"):
                titles, counts = UNDONE_BY[name]
                if counts is None or counts(ev.get("requestParameters") or {}):
                    for title in (titles if isinstance(titles, tuple) else (titles,)):
                        ctx.close(title, what, f"undone in AWS: {name}")
                if name != "PutBucketPublicAccessBlock":
                    continue
            if name == "ConsoleLogin":
                result = (ev.get("responseElements") or {}).get("ConsoleLogin")
                if result == "Failure":
                    ctx.signal("medium", f"Failed cloud console sign-in by {who}",
                               f"From {ev.get('sourceIPAddress', '?')} in {where}.", entity=where, mitre="T1110")
                elif (ev.get("userIdentity") or {}).get("type") == "Root":
                    ctx.signal("high", "Root account signed in to the cloud console",
                               f"From {ev.get('sourceIPAddress', '?')} in {where}.", entity=where, mitre="T1078.004")
            elif name == "AuthorizeSecurityGroupIngress" and ('"0.0.0.0/0"' in msg or '"::/0"' in msg):
                # The whole internet over IPv4 or IPv6: either one is every address there is.
                opened = "0.0.0.0/0" if '"0.0.0.0/0"' in msg else "::/0 (every IPv6 address)"
                ctx.signal("high", INTERNET_OPEN, f"{who} opened a security group{on} to {opened} in {where}.",
                           entity=where, mitre="T1686.001", resource=what)
            elif name in ("AttachUserPolicy", "AttachRolePolicy", "AttachGroupPolicy") and str(
                    (ev.get("requestParameters") or {}).get("policyArn", "")).endswith("/AdministratorAccess") \
                    and not ev.get("errorCode"):
                ctx.signal("critical", ADMIN_GRANTED, f"{name}: AdministratorAccess{on} by {who} in {where}. "
                           "Whoever holds it can do anything in the account, including turning WhiteHats' "
                           "read access off.", entity=where, mitre="T1098.003", resource=what)
            elif name in self.RISKY:
                if name == "PutBucketPublicAccessBlock" and UNDONE_BY[name][1](ev.get("requestParameters") or {}) \
                        and not ev.get("errorCode"):
                    continue  # every block switched on: the bucket was made safer, not riskier
                sev, title, mitre = self.RISKY[name]
                detail = f"{name}{on} by {who} in {where}."
                block = (ev.get("requestParameters") or {}).get("PublicAccessBlockConfiguration") or {}
                if name == "DeleteBucketPublicAccessBlock" or (name == "PutBucketPublicAccessBlock" and block and not any(
                        block.get(k) for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy",
                                               "RestrictPublicBuckets"))):
                    # every block off: one public policy or ACL away from the whole internet reading the bucket
                    sev = "high"
                    detail += (" Every public access block on it is now off: a single public bucket policy or ACL "
                               "would let anyone on the internet read it. Switch Block Public Access back on.")
                ctx.signal(sev, title, detail, entity=where, mitre=mitre, resource=what)


# Company working hours, when recorded, live on Organization.contract:
# timezone (IANA name, "UTC", or an offset such as +05:30), work_start and work_end ("HH:MM").
# With nothing recorded, the window is 09:00-17:00 UTC. A stamp with no zone is read as UTC.
_DEFAULT_START = time(9, 0)
_DEFAULT_END = time(17, 0)
_JSON_TIME_KEYS = ("eventTime", "TimeGenerated", "timestamp", "time")
_ISO_IN_TEXT = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
_SYSLOG = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\b")
_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def _clock(value: object, fallback: time) -> time:
    if isinstance(value, int) and 0 <= value <= 23:
        return time(value, 0)
    if not isinstance(value, str) or ":" not in value:
        return fallback
    hour, minute, *_rest = value.strip().split(":")
    try:
        h, m = int(hour), int(minute)
    except ValueError:
        return fallback
    if 0 <= h <= 23 and 0 <= m <= 59:
        return time(h, m)
    return fallback


# Offsets in use run from -12:00 to +14:00, so more than 14 hours either way is a typing mistake.
_MAX_OFFSET = timedelta(hours=14)


def read_zone(name: object) -> tzinfo | None:
    """A company time zone: UTC, an IANA name, or an offset such as +05:30. None when it cannot be read."""
    text = str(name or "").strip()
    if text.upper() in ("UTC", "Z", "GMT"):
        return UTC
    offset = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", text)
    if offset:
        size = timedelta(hours=int(offset.group(2)), minutes=int(offset.group(3)))
        if int(offset.group(3)) > 59 or size > _MAX_OFFSET:
            return None
        return timezone(size if offset.group(1) == "+" else -size)
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _zone(name: object) -> tzinfo:
    return read_zone(name) or UTC


def _window(ctx: Context) -> tuple[time, time, tzinfo]:
    org = ctx.db.get(Organization, ctx.org_id)
    contract = org.contract if org is not None and isinstance(org.contract, dict) else {}
    start = _clock(contract.get("work_start"), _DEFAULT_START)
    end = _clock(contract.get("work_end"), _DEFAULT_END)
    if start == end:
        start, end = _DEFAULT_START, _DEFAULT_END
    return start, end, _zone(contract.get("timezone"))


def _in_window(local: time, start: time, end: time) -> bool:
    if start < end:
        return start <= local < end
    if start > end:
        return local >= start or local < end
    return False


def _parse_stamp(value: object) -> datetime | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _from_json(msg: str) -> datetime | None:
    if not msg.startswith("{"):
        return None
    try:
        ev = json.loads(msg)
    except ValueError:
        return None
    if not isinstance(ev, dict):
        return None
    for key in _JSON_TIME_KEYS:
        parsed = _parse_stamp(ev.get(key))
        if parsed is not None:
            return parsed
    return None


def _syslog_as_utc(match: re.Match) -> datetime | None:
    """Month-day-time with no zone and no year. The year comes from UTC; the clock does not."""
    now = datetime.now(UTC)
    try:
        stamp = datetime(now.year, _MONTHS[match.group(1)], int(match.group(2)), int(match.group(3)),
                         int(match.group(4)), int(match.group(5)), tzinfo=UTC)
    except ValueError:
        return None
    if stamp - now > timedelta(days=2):
        try:
            stamp = stamp.replace(year=now.year - 1)
        except ValueError:
            return None
    return stamp


def _from_text(msg: str) -> datetime | None:
    iso = _ISO_IN_TEXT.search(msg)
    if iso:
        parsed = _parse_stamp(iso.group(1))
        if parsed is not None:
            return parsed
    syslog = _SYSLOG.search(msg)
    if syslog:
        return _syslog_as_utc(syslog)
    return None


def event_when(line: dict) -> datetime | None:
    """The line's own time. A missing zone means UTC. Never the server's local clock."""
    if not isinstance(line, dict):
        return None
    for key in ("ts", "timestamp", "time"):
        parsed = _parse_stamp(line.get(key))
        if parsed is not None:
            return parsed
    msg = str(line.get("message", ""))
    return _from_json(msg) or _from_text(msg)


def _tz_name(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    return str(key) if key else str(tz)


class Insider(WhiteHat):
    key, name = "insider", "Insider Threat"
    domain = "Off-hours sign-ins"
    description = "Flags successful sign-ins outside company working hours, using each event's time zone or UTC."
    data_sources = ("logs",)

    def on_log_batch(self, ctx: Context, source_host: str, lines: list[dict]) -> None:
        start, end, tz = _window(ctx)
        hits: list[tuple[datetime, dict]] = []
        skipped = 0
        for ln in lines:
            low = str(ln.get("message", "")).lower()
            if not any(p in low for p in SUCCESS_AUTH):
                continue
            when = event_when(ln)
            if when is None:
                skipped += 1
                continue
            if _in_window(when.astimezone(tz).time(), start, end):
                continue
            hits.append((when, ln))
        if skipped:
            health = dict(ctx.agent.health or {})
            health["unreadable_times"] = int(health.get("unreadable_times") or 0) + skipped
            ctx.agent.health = health
        if not hits:
            return
        when, sample = hits[0]
        shown = when.astimezone(tz)
        ctx.signal("low", f"{len(hits)} sign-in(s) outside working hours on {source_host or 'a host'}",
                   f"Working hours {start.strftime('%H:%M')}-{end.strftime('%H:%M')} {_tz_name(tz)}. "
                   f"Event time {shown.strftime('%Y-%m-%d %H:%M:%S%z')}. Example: {sample.get('message', '')[:300]}",
                   entity=source_host, mitre="T1078")


def _planned(key: str, name: str, domain: str, sources: tuple[str, ...], why: str) -> WhiteHat:
    cls = type(name.replace(" ", "").replace("/", ""), (WhiteHat,), {
        "key": key, "name": name, "domain": domain, "data_sources": sources, "available": False,
        "unavailable_reason": why, "description": domain + "."})
    return cls()


BUILTIN = [Identity(), Endpoint(), Network(), CloudPosture(), Insider()]
PLANNED = [
    _planned("appsec", "Application Security", "Weaknesses in the code your teams ship", ("code",),
             "Fix Factory covers this today; a scanner-fed detector is planned."),
    _planned("supply", "Software Supply Chain", "Risky open-source packages and build integrity", ("code",),
             "Needs a dependency scanner connector."),
    _planned("database", "Database", "Query patterns, access anomalies and data exfiltration", ("logs",),
             "Needs database audit log parsing."),
    _planned("deploy", "Deployment", "CI/CD pipelines and release integrity", ("code",), "Needs a CI connector."),
    _planned("config", "Configuration", "Drift and policy violations", ("cloud_audit",), "Needs a config snapshot source."),
    _planned("comms", "Communications", "Email and chat social engineering", ("identity",),
             "Needs a mail security connector."),
    _planned("intel", "Threat Intelligence", "New vulnerabilities and attacker campaigns", (),
             "Needs a threat feed subscription."),
    _planned("ot", "Physical / OT", "Building systems and operational technology", ("network",),
             "Needs an OT protocol analyzer."),
]

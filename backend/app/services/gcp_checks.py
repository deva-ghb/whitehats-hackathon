"""The live settings and admin activity of a connected Google Cloud project: the Google Cloud twin of cloud_settings.

Every read (each minute, or "Read now"):
- The settings checks run on the project's resources and their permissions. Each failed check is an open signal on the
  resource it names, in the projects the connection belongs to; a check that passes again closes its signal, because
  the setting was put right. The first read marks what it finds as baseline: backlog to review, not something new.
- The admin activity log since the last read is checked for risky changes (access opened, a firewall rule added, a
  key created, logging switched off). Each is a signal naming who did it and when: the deviation, as it happened.

Rules decide every result. No AI runs here.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors import routing
from ..connectors.base import SourceError
from ..connectors.gcp import Gcp
from ..db import utcnow
from ..models import Connector, Signal
from . import finding_decisions

log = logging.getLogger("whitehats.gcp_checks")

SOURCE = "whitehat:cloud"  # the same WhiteHat as the AWS settings checks: Cloud Posture
CHECK_PREFIX = "gcp-check-"
CHANGE_PREFIX = "gcp-change-"
SYNC_EVERY_S = 60
PUBLIC = ("allUsers", "allAuthenticatedUsers")
OPEN_RANGES = ("0.0.0.0/0", "::/0")
ADMIN_PORTS = {22: "SSH", 3389: "remote desktop"}
DATABASE_PORTS = {3306: "MySQL", 5432: "PostgreSQL", 1433: "SQL Server", 6379: "Redis", 27017: "MongoDB"}
KIND = {
    "storage.googleapis.com/Bucket": "Storage bucket",
    "bigquery.googleapis.com/Dataset": "BigQuery dataset",
    "pubsub.googleapis.com/Topic": "Pub/Sub topic",
    "run.googleapis.com/Service": "Cloud Run service",
    "cloudresourcemanager.googleapis.com/Project": "Project",
}


def short(name: str) -> str:
    return name.rstrip("/").rsplit("/", 1)[-1]


def _finding(check: str, resource: str, severity: str, title: str, detail: str, fix: str) -> dict:
    return {"key": f"{CHECK_PREFIX}{check}:{resource}"[:240], "check": check, "resource": resource,
            "severity": severity, "title": title, "detail": f"{detail}\n\nHow to put it right: {fix}"}


# ------------------------------------------------------------------ settings checks


def _ports(rule: dict) -> set[int] | None:
    """The TCP ports a firewall rule lets in; None means every port."""
    out: set[int] = set()
    for allow in rule.get("allowed") or []:
        protocol = str(allow.get("IPProtocol") or allow.get("ipProtocol") or "").lower()
        if protocol == "all":
            return None
        if protocol != "tcp":
            continue
        ports = allow.get("ports") or []
        if not ports:
            return None
        for item in ports:
            low, _, high = str(item).partition("-")
            first, last = int(low), int(high or low)
            if last - first > 1000:
                return None
            out.update(range(first, last + 1))
    return out


def _firewall(asset: dict) -> list[dict]:
    rule = (asset.get("resource") or {}).get("data") or {}
    if rule.get("disabled") or str(rule.get("direction", "INGRESS")).upper() != "INGRESS":
        return []
    if not any(r in OPEN_RANGES for r in rule.get("sourceRanges") or []):
        return []
    name = asset["name"]
    ports = _ports(rule)
    fix = "Limit the rule's source ranges to the addresses that need it, or reach machines through IAP instead."
    if ports is None:
        return [_finding("firewall-open-all", name, "critical", f"Firewall rule {short(name)} lets the whole internet "
                         "reach every port", f"The rule allows any address (0.0.0.0/0) in on all ports of the "
                         f"network {short(str(rule.get('network', '')))}.", fix)]
    out = []
    for port, label in {**ADMIN_PORTS, **DATABASE_PORTS}.items():
        if port in ports:
            out.append(_finding(f"firewall-open-{port}", name, "high", f"Firewall rule {short(name)} opens {label} "
                                f"(port {port}) to the internet", f"Any address (0.0.0.0/0) can reach port {port} on "
                                f"machines in {short(str(rule.get('network', '')))}.", fix))
    return out


def _bucket(asset: dict) -> list[dict]:
    data = (asset.get("resource") or {}).get("data") or {}
    config = data.get("iamConfiguration") or {}
    uniform = (config.get("uniformBucketLevelAccess") or config.get("bucketPolicyOnly") or {}).get("enabled")
    if uniform is False:
        return [_finding("bucket-fine-grained", asset["name"], "medium", f"Bucket {short(asset['name'])} lets single "
                         "files be shared on their own", "Uniform bucket-level access is off, so any file can be made "
                         "public by itself without the bucket's permissions showing it.",
                         "Turn on uniform bucket-level access.")]
    return []


def _sql(asset: dict) -> list[dict]:
    settings = (((asset.get("resource") or {}).get("data") or {}).get("settings")) or {}
    name, out = asset["name"], []
    networks = ((settings.get("ipConfiguration") or {}).get("authorizedNetworks")) or []
    if any(n.get("value") in OPEN_RANGES for n in networks):
        out.append(_finding("sql-open", name, "critical", f"Database {short(name)} accepts connections from anywhere",
                            "An authorized network of 0.0.0.0/0 lets any address on the internet try to sign in.",
                            "Remove 0.0.0.0/0 from authorized networks; connect through a private IP or the "
                            "Cloud SQL Auth Proxy."))
    if (settings.get("backupConfiguration") or {}).get("enabled") is False:
        out.append(_finding("sql-no-backups", name, "high", f"Database {short(name)} has no automated backups",
                            "If the data is lost or encrypted by an attacker, there is no copy to restore.",
                            "Turn on automated backups and point-in-time recovery."))
    return out


def _key(asset: dict) -> list[dict]:
    data = (asset.get("resource") or {}).get("data") or {}
    if str(data.get("keyType", "")).upper() != "USER_MANAGED" or data.get("disabled"):
        return []
    account = asset["name"].split("/serviceAccounts/")[-1].split("/")[0]
    return [_finding("sa-key", asset["name"], "medium", f"A downloadable key exists for {account}",
                     "Anyone holding this key file can act as the service account from anywhere until it is deleted.",
                     "Delete the key and use workload identity or impersonation instead.")]


def _policy(asset: dict) -> list[dict]:
    kind = KIND.get(asset.get("assetType", ""), "Resource")
    name = asset["name"]
    open_roles = sorted({b.get("role", "") for b in (asset.get("iamPolicy") or {}).get("bindings") or []
                         if any(m in PUBLIC for m in b.get("members") or [])})
    if not open_roles:
        return []
    if kind == "Cloud Run service" and open_roles == ["roles/run.invoker"]:
        return [_finding("public-invoke", name, "low", f"Cloud Run service {short(name)} can be called by anyone",
                         "Anyone on the internet can call it. Right for a public website, wrong for an internal API.",
                         "Remove allUsers as invoker if the service is not meant to be public.")]
    return [_finding("public", name, "critical", f"{kind} {short(name)} is open to anyone on the internet",
                     f"allUsers or allAuthenticatedUsers hold {', '.join(open_roles)}: anyone with the link "
                     "can use it.", "Remove allUsers and allAuthenticatedUsers from its permissions, and turn on "
                     "public access prevention for buckets.")]


RESOURCE_CHECKS = {
    "compute.googleapis.com/Firewall": _firewall,
    "storage.googleapis.com/Bucket": _bucket,
    "sqladmin.googleapis.com/Instance": _sql,
    "iam.googleapis.com/ServiceAccountKey": _key,
}


def evaluate(resources: list[dict], policies: list[dict]) -> list[dict]:
    """Every failed check, one finding per (check, resource). Pure, so the tests and the screens agree."""
    out: list[dict] = []
    for asset in resources:
        out += RESOURCE_CHECKS.get(asset.get("assetType", ""), lambda _a: [])(asset)
    for asset in policies:
        out += _policy(asset)
    return list({f["key"]: f for f in out}.values())


# ------------------------------------------------------------------ activity checks

# (words in the method name, what it means, severity). Matched case-insensitively on the method name.
RISKY_CHANGES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("setiampolicy",), "changed who has access", "high"),
    (("setiampermissions",), "changed who has access", "high"),
    (("firewalls.insert",), "added a firewall rule", "high"),
    (("firewalls.patch",), "changed a firewall rule", "high"),
    (("firewalls.update",), "changed a firewall rule", "high"),
    (("createserviceaccountkey",), "created a downloadable service account key", "high"),
    (("sinks.delete",), "deleted a log export", "high"),
    (("deletesink",), "deleted a log export", "high"),
    (("cloudsql.instances.update",), "changed a database's settings", "medium"),
    (("cloudsql.instances.patch",), "changed a database's settings", "medium"),
    (("storage.buckets.update",), "changed a bucket's settings", "medium"),
    (("storage.buckets.create",), "created a bucket", "low"),
)


def _opens_to_public(request: object) -> bool:
    return any(member in str(request) for member in PUBLIC)


def changes(lines: list[dict]) -> list[dict]:
    """The risky admin changes among the activity lines, each with who, what and when."""
    out = []
    for line in lines:
        if line.get("failed"):
            continue
        method = str(line.get("method", "")).lower()
        for words, meaning, severity in RISKY_CHANGES:
            if all(w in method for w in words):
                public = _opens_to_public(line.get("request"))
                what = "made it open to anyone on the internet" if public else meaning
                out.append({"key": f"{CHANGE_PREFIX}{line.get('id')}"[:240], "resource": line.get("target", ""),
                            "severity": "critical" if public else severity,
                            "title": f"{line.get('who')} {what}: {short(str(line.get('target', '')))}",
                            "detail": f"At {line.get('ts')}, {line.get('who')} called {line.get('method')} on "
                                      f"{line.get('target')}. The settings check on the next read shows whether "
                                      "this left a risk open."})
                break
    return out


# ------------------------------------------------------------------ signals


def _projects(db: Session, c: Connector) -> list[int]:
    return routing.projects_for(db, c, routing.connection_resource(c))


def apply_findings(db: Session, c: Connector, findings: list[dict], baseline: bool) -> tuple[int, int]:
    """Open a signal per new failed check, refresh the ones still failing, close the ones that pass now."""
    by_key = {f["key"]: f for f in findings}
    opened = closed = 0
    for project_id in _projects(db, c):
        held = {row.fingerprint: row for row in db.scalars(select(Signal).where(
            Signal.org_id == c.org_id, Signal.project_id == project_id, Signal.source == SOURCE,
            Signal.fingerprint.like(f"{CHECK_PREFIX}%"), Signal.closed_at.is_(None)))}
        decided = finding_decisions.active(db, project_id)
        for key, f in by_key.items():
            row = held.get(key)
            if row is None:
                if key in decided:
                    continue  # an approver called it a false positive or accepted its risk
                db.add(Signal(org_id=c.org_id, project_id=project_id, severity=f["severity"], title=f["title"][:240],
                              detail=f["detail"][:4000], entity=f"gcp:{c.config.get('project_id', '')}"[:160],
                              resource=f["resource"][:300], source=SOURCE, fingerprint=key, origin="live",
                              baseline=baseline))
                opened += 1
            else:
                row.title, row.detail, row.severity = f["title"][:240], f["detail"][:4000], f["severity"]
        for key, row in held.items():
            if key not in by_key:
                row.closed_at, row.closed_reason = utcnow(), "setting put right"
                closed += 1
    return opened, closed


def apply_changes(db: Session, c: Connector, found: list[dict], baseline: bool) -> int:
    opened = 0
    for project_id in _projects(db, c):
        have = set(db.scalars(select(Signal.fingerprint).where(
            Signal.org_id == c.org_id, Signal.project_id == project_id,
            Signal.fingerprint.in_([f["key"] for f in found]))))
        for f in found:
            if f["key"] in have:
                continue
            db.add(Signal(org_id=c.org_id, project_id=project_id, severity=f["severity"], title=f["title"][:240],
                          detail=f["detail"][:4000], entity=f"gcp:{c.config.get('project_id', '')}"[:160],
                          resource=str(f["resource"])[:300], source=SOURCE, fingerprint=f["key"], origin="live",
                          baseline=baseline))
            opened += 1
    return opened


def sync(db: Session, c: Connector, source: Gcp | None = None) -> dict:
    """One read: the settings checks, then the activity since the last read. Returns what it did."""
    state = dict(c.state or {})
    first = not state.get("checked_at")
    try:
        src = source or Gcp(c.config)
        resources, policies = src.inventory()
        findings = evaluate(resources, policies)
        opened, closed = apply_findings(db, c, findings, baseline=first)
        batch = src.read(state.get("bookmark") or {})
        found = changes(batch.lines)
        noticed = apply_changes(db, c, found, baseline=first)
    except SourceError as exc:
        state["last_error"] = str(exc)
        c.state = state
        db.commit()
        raise
    by_type: dict[str, int] = {}
    for asset in resources:
        label = asset.get("assetType", "").split("/")[-1]
        by_type[label] = by_type.get(label, 0) + 1
    result = {"resources": len(resources), "by_type": by_type, "problems": len(findings), "opened": opened,
              "closed": closed, "activity": len(batch.lines), "changes": noticed}
    state.update(result)
    state.update({"bookmark": batch.bookmark, "checked_at": utcnow().isoformat(), "last_error": "",
                  "total": int(state.get("total", 0)) + len(batch.lines)})
    c.state = state
    c.status, c.last_sync_at = "reading", utcnow()
    db.commit()
    return result


def sync_due(db: Session, now: datetime | None = None) -> int:
    """Read every connected Google Cloud project whose minute is up. Returns how many were read."""
    now = now or utcnow()
    done = 0
    for c in db.scalars(select(Connector).where(Connector.key == "gcp", Connector.status.in_(("checked", "reading")))):
        if c.last_sync_at and (now - c.last_sync_at).total_seconds() < SYNC_EVERY_S:
            continue
        try:
            sync(db, c)
            done += 1
        except SourceError as exc:
            log.warning("Google Cloud read failed for connector %s: %s", c.id, exc)
            c.last_sync_at = now  # try again next minute, not in a tight loop
            db.commit()
    return done

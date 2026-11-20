"""The live settings of a connected AWS account, checked by Prowler (CS.1).

The account's activity history says what changed and when; it cannot say how things are now, and it misses whatever
changed before WhiteHats was connected or while reading was down. This reads the settings themselves: Prowler, the
open-source cloud checker, runs its Critical and High checks against the account through the same read-only role the
AWS connection uses (AWS's SecurityAudit policy: configuration, never data), in a container on this server.

- Every FULL_EVERY_S the whole account is checked. When the activity history shows a change to a service (S3, EC2,
  IAM...), that service alone is checked again on the next pass, so a new risk shows within minutes.
- Each failed check is an open signal on the resource it names (its ARN, so the map puts it on the right building), in
  the projects the connection belongs to. A check that passes again closes its signal: the setting was put right.
- The connection's state carries the scan's progress, for the screens: running, when the last one finished, how many
  problems it found, or why it could not run.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..connectors import routing
from ..connectors.base import SourceError
from ..db import utcnow
from ..models import Connector, SettingsCheck, Signal
from . import amazon_findings, auto_incidents, finding_decisions, firewall

log = logging.getLogger("whitehats.cloud_settings")

IMAGE = os.environ.get("WH_PROWLER_IMAGE", "prowlercloud/prowler:5.45.0")
SEVERITIES = ("critical", "high")  # what becomes an alert; the rest is best practice, not an alarm
# The medium checks kept all the same (NET.1): the way into a network Prowler rates medium, and the switch that records
# what moves in and out of it. The rest of medium is best practice, not an alarm.
MEDIUM_KEPT = frozenset({"ec2_networkacl_allow_ingress_tcp_port_22", "ec2_networkacl_allow_ingress_tcp_port_3389",
                         "vpc_flow_logs_enabled"})
FULL_EVERY_S = 3600
TIMEOUT_S = 1800
SOURCE = "whitehat:cloud"  # Cloud Posture: the WhiteHat for the cloud account, by its changes and by its settings
FINGERPRINT_PREFIX = "prowler-"
# The activity history names a service by its endpoint (s3.amazonaws.com); Prowler by its own short name.
DENIED = ("AccessDenied", "is not authorized to perform", "UnauthorizedOperation")


class MissingPermissions(SourceError):
    """Prowler ran, but the role refused some checks: the result cannot be trusted as complete."""


ACCOUNT_WIDE = frozenset({"iam"})  # checked across the whole account, never by the connection's tag

SERVICE_NAMES = {"monitoring": "cloudwatch", "logs": "cloudwatch", "elasticloadbalancing": "elbv2",
                 "es": "opensearch", "states": "stepfunctions", "lambda": "awslambda", "events": "eventbridge",
                 "elasticfilesystem": "efs", "elasticmapreduce": "emr", "secretsmanager": "secretsmanager"}
# What Prowler 5.45 can check (`prowler aws --list-services`). A change to anything else (a console sign-in, a session
# token) has no settings to check: asking Prowler for it would fail the whole check.
PROWLER_SERVICES = frozenset(["accessanalyzer", "account", "acm", "acmpca", "amplify", "apigateway", "apigatewayv2", "appstream", "appsync", "athena", "autoscaling", "awslambda", "backup", "batch", "bedrock", "cloudformation", "cloudfront", "cloudtrail", "cloudwatch", "codeartifact", "codebuild", "codecommit", "codepipeline", "cognito", "config", "datapipeline", "datasync", "directconnect", "directoryservice", "dlm", "dms", "documentdb", "drs", "dynamodb", "ec2", "ecr", "ecs", "efs", "eks", "elasticache", "elasticbeanstalk", "elb", "elbv2", "emr", "eventbridge", "firehose", "fms", "fsx", "glacier", "glue", "guardduty", "iam", "inspector2", "kafka", "kinesis", "kms", "lightsail", "macie", "memorydb", "mq", "neptune", "networkfirewall", "opensearch", "organizations", "rds", "redshift", "resourceexplorer2", "rolesanywhere", "route53", "s3", "sagemaker", "secretsmanager", "securityhub", "servicecatalog", "ses", "shield", "sns", "sqs", "ssm", "ssmincidents", "stepfunctions", "storagegateway", "transfer", "trustedadvisor", "vpc", "waf", "wafv2", "wellarchitected", "workspaces"])
RETRY_AFTER_S = 600  # a check that failed is not tried again for this long: no failing every pass


def service_of(event_source: str) -> str:
    """The Prowler service an activity history event belongs to ("" when the source is not a service endpoint)."""
    name = str(event_source or "").split(".", 1)[0].strip().lower()
    if not name or not str(event_source).endswith(".amazonaws.com"):
        return ""
    service = SERVICE_NAMES.get(name, name)
    return service if service in PROWLER_SERVICES else ""


def changed_services(lines: list[dict]) -> set[str]:
    """The services whose settings changed in a batch of activity history lines (writes only)."""
    found = set()
    for line in lines:
        if not str(line.get("source", "")).startswith("cloudtrail:"):
            continue
        try:
            event = json.loads(line.get("message") or "{}")
        except ValueError:
            continue
        if event.get("readOnly") is False and (service := service_of(event.get("eventSource", ""))):
            found.add(service)
    return found


def row_for(db: Session, c: Connector) -> SettingsCheck:
    """The connection's settings check row, made the first time."""
    row = db.get(SettingsCheck, c.id)
    if row is None:
        row = SettingsCheck(connector_id=c.id, org_id=c.org_id, status={}, pending=[])
        db.add(row)
        db.flush()
    return row


def note_changes(db: Session, c: Connector, lines: list[dict]) -> None:
    """Ask for those services to be checked again on the next pass (called as the activity history is read)."""
    services = changed_services(lines)
    if services:
        row = row_for(db, c)
        row.pending = sorted(set(row.pending or []) | services)


def due(row: SettingsCheck, now: datetime) -> list[str] | None:
    """What to check now: [] for the whole account, a list of services, or None for nothing yet."""
    state = row.status or {}
    if state.get("status") == "running" and (not state.get("started_at") or (
            now - datetime.fromisoformat(state["started_at"])).total_seconds() < TIMEOUT_S + 60):
        return None  # still running; one cut off by a restart is older than any scan can take, and runs again
    if state.get("status") == "failed" and state.get("finished_at") and (
            now - datetime.fromisoformat(state["finished_at"])).total_seconds() < RETRY_AFTER_S:
        return None  # it failed a moment ago: wait, rather than fail again on every pass
    last_full = state.get("full_at")
    if not last_full or (now - datetime.fromisoformat(last_full)).total_seconds() >= FULL_EVERY_S:
        return []
    return [s for s in row.pending or [] if s in PROWLER_SERVICES] or None


def _set(db: Session, c: Connector, **fields) -> None:
    row = row_for(db, c)
    db.refresh(row)  # the pending list as it is now: the activity reader may have added to it meanwhile
    row.status = {**(row.status or {}), **fields}
    db.commit()


def scan(db: Session, c: Connector, services: list[str]) -> dict:
    """Run Prowler for the whole account ([]) or some services, and apply what it found. Returns the summary."""
    source = connectors.for_row(db, c)
    credentials = getattr(source, "credentials", None)
    if credentials is None:
        raise SourceError("Only an AWS connection's settings can be checked.")
    started = utcnow()
    row = row_for(db, c)
    db.refresh(row)
    # What this check covers is no longer waiting; anything else asked for meanwhile still is.
    row.pending = [s for s in (row.pending or []) if services and s not in services]
    _set(db, c, status="running", started_at=started.isoformat(), services=services, error="")
    unknown: tuple = ()
    try:
        tag = (str(c.config.get("tag_key") or "").strip(), str(c.config.get("tag_value") or "").strip())
        region, creds = str(c.config.get("region") or "us-east-1"), credentials()
        seen = dict(row.status or {})
        scoped = [s for s in services if s not in ACCOUNT_WIDE]
        findings = []
        if not services or scoped:
            # The first check of the connection's resources finds what was already there: backlog, not news.
            findings += _first_look(run_prowler(creds, region, scoped, tag=tag if all(tag) else None,
                                                skip=ACCOUNT_WIDE if all(tag) else ()), not seen.get("full_at"))
            if not scoped or "ec2" in scoped:  # outgoing rules, which Prowler does not check (NET.1)
                findings += _first_look(_outgoing(source, region, tag if all(tag) else None), not seen.get("full_at"))
        if not services:  # Amazon's own findings, where the customer has them on (AMZ.1)
            amazon, more = amazon_findings.read(source, region)
            findings += _first_look(more, not seen.get("full_at"))
            _set(db, c, amazon=amazon)
            unknown = tuple(name for name, state in amazon.items() if state == "unreadable")
        if all(tag) and (not services or any(s in ACCOUNT_WIDE for s in services)):
            # Users and roles belong to the whole account, and the one an attacker makes is never tagged: identity is
            # checked account-wide, whatever the connection's tag.
            findings += _first_look(run_prowler(creds, region, sorted(ACCOUNT_WIDE), tag=None),
                                    not seen.get("identity_at"))
            _set(db, c, identity_at=started.isoformat())
    except MissingPermissions as exc:
        # Not a failure of the scan, and never a reason to close a signal: what could not be read is unknown.
        _set(db, c, status="limited", finished_at=utcnow().isoformat(), error=str(exc)[:500],
             **({} if services else {"full_at": started.isoformat()}))
        return {"limited": str(exc)}
    except (SourceError, OSError, subprocess.SubprocessError, ValueError) as exc:
        log.warning("cloud settings scan failed for connector %s: %s", c.id, exc)
        _set(db, c, status="failed", finished_at=utcnow().isoformat(), error=str(exc)[:500])
        return {"error": str(exc)}
    opened, closed = apply(db, c, findings, services, unknown)
    auto_incidents.sweep(db)
    summary = {"status": "done", "finished_at": utcnow().isoformat(), "problems": len(findings),
               "opened": opened, "closed": closed,
               "seconds": round((utcnow() - started).total_seconds())}
    if not services:
        summary["full_at"] = started.isoformat()
    _set(db, c, **summary)
    return summary


def _outgoing(source, region: str, tag: tuple[str, str] | None) -> list[dict]:
    try:
        return firewall.findings(source, region, tag)
    except SourceError as exc:
        if any(word in str(exc) for word in DENIED):
            raise MissingPermissions("The read-only role cannot read security groups. Update the role with the "
                                     "latest WhiteHats template.") from exc
        raise


def _first_look(findings: list[dict], first: bool) -> list[dict]:
    """Mark findings from the first look at a scope: they were there before WhiteHats was."""
    return [{**f, "_baseline": True} for f in findings] if first else findings


def run_prowler(credentials: dict, region: str, services: list[str], tag: tuple[str, str] | None = None,
                skip: tuple | frozenset = ()) -> list[dict]:
    """Prowler's failed Critical and High checks, as its JSON (OCSF) findings. Credentials are the role's short-lived
    ones, passed to the container only through its environment; nothing is written to disk but Prowler's output."""
    if not shutil.which("docker"):
        raise SourceError("Docker is not installed on this server, so cloud settings cannot be checked.")
    out = Path(tempfile.mkdtemp(prefix="whitehats-prowler-"))
    out.chmod(0o777)  # Prowler's container user writes here
    try:
        argv = ["docker", "run", "--rm", "--memory", "1500m", "--cpus", "1.5",
                "-e", "AWS_ACCESS_KEY_ID", "-e", "AWS_SECRET_ACCESS_KEY", "-e", "AWS_SESSION_TOKEN",
                "-v", f"{out}:/home/prowler/output", IMAGE, "aws", "-f", region, "--severity", *SEVERITIES, "medium",
                "--status", "FAIL", "--output-formats", "json-ocsf", "--output-directory", "/home/prowler/output",
                "--output-filename", "found", "--no-banner", "--ignore-exit-code-3"]
        if services:
            argv[argv.index("-f"):argv.index("-f")] = ["--services", *services]
        if tag:  # the connection's scope: only the resources it covers, as the map and every other read
            argv[argv.index("-f"):argv.index("-f")] = ["--resource-tag", f"{tag[0]}={tag[1]}"]
        if skip and not services:
            argv[argv.index("-f"):argv.index("-f")] = ["--excluded-services", *sorted(skip)]
        env = {**os.environ, "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
               "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"], "AWS_SESSION_TOKEN": credentials["SessionToken"]}
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
        said = f"{done.stdout}\n{done.stderr}"
        if done.returncode != 0:
            raise SourceError(f"Prowler stopped ({done.returncode}): {said[-400:]}")
        if any(word in said for word in DENIED):
            # A check the role may not run cannot fail, so "nothing found" would read as "all clear". It is not.
            raise MissingPermissions("The read-only role is missing permissions some checks need. Update the role "
                                     "with the latest WhiteHats template (it adds AWS's SecurityAudit policy).")
        result = out / "found.ocsf.json"
        if not result.is_file():
            return []  # Prowler writes no file when every check it ran passed
        return [f for f in json.loads(result.read_text() or "[]") if f.get("status_code") == "FAIL" and kept(f)]
    finally:
        shutil.rmtree(out, ignore_errors=True)


def check_of(finding: dict) -> str:
    """The check a finding comes from, out of its id (prowler-aws-<check>-<account>-<region>-<resource>)."""
    parts = str((finding.get("finding_info") or {}).get("uid") or "").split("-")
    return parts[2] if len(parts) > 2 else ""


def kept(finding: dict) -> bool:
    """Critical and high, and the few medium checks in MEDIUM_KEPT."""
    severity = str(finding.get("severity") or "").lower()
    return severity in SEVERITIES or (severity == "medium" and check_of(finding) in MEDIUM_KEPT)


def _resource(finding: dict) -> str:
    """The ARN the finding is about (what the map matches a building by), or its name."""
    for item in finding.get("resources") or []:
        meta = ((item.get("data") or {}).get("metadata") or {})
        for key in ("arn", "name"):
            if meta.get(key):
                return str(meta[key])
        if item.get("uid"):
            return str(item["uid"])
    return ""


def _title(finding: dict) -> str:
    """What is wrong, in Prowler's words for this resource ("Block Public Access is not configured for the S3 Bucket
    receipts."), not the check's name, which reads as the safe state. Its first sentence, before any list of evidence."""
    said = str(finding.get("message") or "").split(" -> ", 1)[0].strip()
    first = said.split(". ", 1)[0].strip().rstrip(".")
    return (first or str((finding.get("finding_info") or {}).get("title") or "") or "A cloud setting is unsafe")[:240]


def apply(db: Session, c: Connector, findings: list[dict], services: list[str], unknown: tuple = ()) -> tuple[int, int]:
    """Open or refresh a signal per failed check, in every project the connection belongs to; close the open ones
    within what was checked that did not fail this time. `unknown`: services that could not be read this time (GuardDuty
    or Security Hub), whose open findings stay open. Returns (opened, closed)."""
    projects = routing.projects_for(db, c, routing.connection_resource(c))
    by_key = {str((f.get("finding_info") or {}).get("uid") or ""): f for f in findings}
    by_key.pop("", None)
    opened = closed = 0
    for project_id in projects:
        held = {row.fingerprint: row for row in db.scalars(select(Signal).where(
            Signal.org_id == c.org_id, Signal.project_id == project_id, Signal.source == SOURCE,
            Signal.fingerprint.like(f"{FINGERPRINT_PREFIX}%"), Signal.closed_at.is_(None)))}
        decided = finding_decisions.active(db, project_id)
        for key, f in by_key.items():
            if key in decided and key not in held:
                continue  # an approver called it a false positive or accepted its risk (FD.1)
            remedy = ((f.get("remediation") or {}).get("desc") or "").strip()
            detail = f"{f.get('message', '')}\n\n{remedy}".strip()[:4000]
            row = held.get(key)
            if row is None:
                db.add(Signal(org_id=c.org_id, project_id=project_id, severity=str(f.get("severity", "high")).lower(),
                              title=_title(f),
                              detail=detail, entity=str(c.config.get("region") or ""), resource=_resource(f)[:300],
                              source=SOURCE, fingerprint=key[:240], origin="live", baseline=bool(f.get("_baseline"))))
                opened += 1
            else:
                if row.title != _title(f):
                    auto_incidents.retitle(db, row, _title(f))
                row.title, row.detail = _title(f), detail  # the same problem, as it reads now
        for key, row in held.items():
            in_scope = not services or any(f"-aws-{s}_" in key for s in services)
            if any(f"-aws-{name}_" in key for name in unknown):
                continue  # not read this time: unknown, not put right
            if in_scope and key not in by_key:
                row.closed_at, row.closed_reason = utcnow(), "setting put right"
                closed += 1
    db.commit()
    return opened, closed


def resume_after_restart(db: Session) -> int:
    """At start-up: a check cut off when the server stopped runs again on the next pass, instead of reading as running
    until it times out. Its services go back to pending; a cut-off full check makes the whole account due."""
    stopped = 0
    for row in db.scalars(select(SettingsCheck)):
        status = dict(row.status or {})
        if status.get("status") != "running":
            continue
        services = list(status.get("services") or [])
        if services:
            row.pending = sorted(set(row.pending or []) | set(services))
        else:
            status.pop("full_at", None)
        status["status"] = "interrupted"
        row.status = status
        stopped += 1
    db.commit()
    return stopped


def scan_due(db: Session, now: datetime | None = None) -> int:
    """One pass: check every readable AWS connection whose turn has come. Returns how many scans ran."""
    from . import cloudlogs

    now = now or utcnow()
    ran = 0
    for c in db.scalars(select(Connector).where(Connector.key == "aws")):
        if not cloudlogs.can_read(c):
            continue
        what = due(row_for(db, c), now)
        if what is None:
            continue
        scan(db, c, what)
        ran += 1
    return ran

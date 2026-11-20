"""The map builds and keeps itself from the connections (STUDIO.6).

Attaching AWS or GitHub to a project starts the project's map if it has none, places the connection on it, reads
what the connection sees and brings it in. After that the map is read again when the account says something
changed (a CloudTrail management event that writes, or a GitHub push that touches how the estate is built), and
every hour as a safety net. Added and changed things go in by themselves, with the connection as the author;
what the connection no longer finds is held for a person, never removed by itself.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.base import SourceError
from ..connectors.routing import attachments
from ..db import utcnow
from ..models import Clone, Connector, Organization, Project
from ..whitehats.signals import SECURITY_CHANGES
from . import audit_jobs, cloudlogs, connection_nodes, connector_status, imports, setup_checks

log = logging.getLogger("whitehats.auto_map")

KEYS = ("aws", "github")
EVERY = timedelta(hours=1)
# A push that changes these changes what the estate is made of, so the GitHub map is read again.
BUILD_FILES = (".tf", ".tfvars", "Dockerfile", ".github/workflows/", "package-lock.json", "yarn.lock",
               "pnpm-lock.yaml", "poetry.lock", "uv.lock", "requirements.txt", "go.sum", "Cargo.lock")


def author(connector: Connector) -> str:
    return f"{connector.name} connection"


def refresh(db: Session, project: Project, connector: Connector, *, why: str) -> dict:
    """Start or update this project's map from this connection. Raises SourceError when the connection cannot read."""
    org = db.get(Organization, project.org_id)
    assert org is not None and org.slug
    who = author(connector)
    placed = connection_nodes.place(db, project, connector, company=org.slug, author=who)
    connector_status.discover(db, connector)
    clone = db.get(Clone, placed["clone"])
    assert clone is not None
    done = connection_nodes.bring_in(db, clone, connector, company=org.slug, author=who, hold_removals=True, why=why)
    db.commit()
    done = {**done, "checks": setup_checks.run(db, clone)}
    db.commit()
    log.info("map refresh project=%s connector=%s why=%r %s", project.slug, connector.key, why, done)
    return {**done, "clone": clone.id}


def mark_due(connector: Connector, why: str) -> None:
    """Ask for a read on the next pass, with the reason in words. A security change already waiting is not replaced by
    a later housekeeping one: it is what decides whether the read also starts an audit."""
    waiting = str((connector.state or {}).get("map_due") or "")
    if waiting.startswith(SECURITY_PREFIX) and not why.startswith(SECURITY_PREFIX):
        return
    connector.state = {**(connector.state or {}), "map_due": why[:200]}


# Writes that are housekeeping, not a change to what the estate is made of: agents checking in and reporting.
HOUSEKEEPING = frozenset({
    "UpdateInstanceInformation", "UpdateInstanceAssociationStatus", "PutInventory", "PutComplianceItems",
    "PutMetricData", "PutLogEvents", "CreateLogStream", "SendCommand", "StartSession", "ConsoleLogin",
})


CONNECTED = " was connected"  # mark_due's words when a person connects a source
ATTACHED = " was attached to "  # ... or attaches one to a project
SECURITY_PREFIX = "AWS saw"  # a change that matters for security: the map is read again and a focused audit starts
OTHER_PREFIX = "AWS changed"  # any other change: the map is read again, nothing more


def changed_the_setup(lines: list[dict]) -> str:
    """The CloudTrail event that changed the account's setup, in words: a security change first ("AWS saw ..."), else
    the first other change ("AWS changed ..."), or "" when nothing changed."""
    other = ""
    for line in lines:
        if not str(line.get("source", "")).startswith("cloudtrail:"):
            continue
        try:
            event = json.loads(line.get("message") or "{}")
        except ValueError:
            continue
        if (event.get("readOnly") is False and event.get("managementEvent", True) is not False
                and event.get("eventName") not in HOUSEKEEPING):
            name = event.get("eventName", "a change")
            if name in SECURITY_CHANGES and not event.get("errorCode"):
                return f"{SECURITY_PREFIX} {name}"
            other = other or f"{OTHER_PREFIX} {name}"
    return other


def touched_the_build(files: list[str]) -> str:
    """Whether a push's files change how the estate is built: the first such file, in words, or ""."""
    for path in files:
        if any(part in str(path) for part in BUILD_FILES):
            return f"a push changed {path}"
    return ""


def _last(connector: Connector) -> datetime | None:
    at = (connector.state or {}).get("map_at")
    try:
        return datetime.fromisoformat(at) if at else None
    except ValueError:
        return None


def _audit_after(db: Session, project: Project, connector: Connector, why: str) -> None:
    """A fresh map can start an audit (AUD.5): the first one once the project's code is connected, and a focused one
    when AWS said the account's setup changed."""
    has_code = any(key == "github" for key in project_connection_keys(db, project))
    if not has_code:
        return
    # AUD.6: a full audit only for connections the last one did not read; a person who just connected is told why not
    connected = why.endswith(CONNECTED) or ATTACHED in why
    if audit_jobs.after_connections(db, project, connected=connected) is not None:
        return
    if connector.key == "aws" and why.startswith(SECURITY_PREFIX):  # housekeeping never starts an audit
        audit_jobs.request(db, project.org_id, project.id, "cloud_change", detail=why)


def project_connection_keys(db: Session, project: Project) -> list[str]:
    keys = []
    for row in db.scalars(select(Connector).where(Connector.org_id == project.org_id, Connector.key.in_(KEYS))):
        if any(pid == project.id for pid, _ in attachments(db, row)):
            keys.append(row.key)
    return keys


def run_due(db: Session, now: datetime | None = None) -> int:
    """Read again every connection that asked for it, or whose map is an hour old. Returns how many maps were read."""
    now = now or utcnow()
    done = 0
    for connector in db.scalars(select(Connector).where(Connector.key.in_(KEYS))).all():
        if connector.status not in cloudlogs.READABLE:  # set up and its check passed
            continue
        due = (connector.state or {}).get("map_due")
        last = _last(connector)
        if not due and last is not None and now - last < EVERY:
            continue
        why = due or "the hourly read"
        for project_id, _ in attachments(db, connector):
            project = db.get(Project, project_id)
            if project is None or project.archived:
                continue
            try:
                refresh(db, project, connector, why=why)
                done += 1
                _audit_after(db, project, connector, why)
            except (SourceError, imports.ImportRefused) as exc:
                db.rollback()
                log.warning("map refresh failed project=%s connector=%s: %s", project.slug, connector.key, exc)
        connector.state = {**{k: v for k, v in (connector.state or {}).items() if k != "map_due"},
                           "map_at": now.isoformat()}
        db.commit()
    return done

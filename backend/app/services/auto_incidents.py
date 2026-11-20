"""Incidents WhiteHats opens and resolves itself, for serious problems in a connected cloud account.

A person opens an incident from signals by hand; that stays the rule for everything else. A Critical or High problem in
the cloud account (a bucket made public, audit logging switched off) is different: nobody needs to judge whether it
matters, and it should not wait in a feed. So:

- Each open Critical or High signal from the cloud account opens an incident, one per resource: a second problem on the
  same bucket joins the bucket's open incident instead of opening another.
- When every signal on such an incident has closed (the setting was put right, or the change undone), WhiteHats
  resolves it, saying why. An incident a person has taken on is resolved the same way, with the same note; one with
  a fix still waiting for a decision or proof is left for the person.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..incident_refs import RefTaken, insert_with_next_ref
from ..models import INCIDENT_CLOSED, Incident, IncidentEvent, Remediation, Signal

log = logging.getLogger("whitehats.auto_incidents")

SOURCES = ("whitehat:cloud",)
SEVERITIES = ("critical", "high")
ACTOR = "WhiteHats"
OPENED = "Opened by WhiteHats"  # the first event of an incident WhiteHats opened; how one is told apart
UNVERIFIED = ("pending", "dispatched")
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _opened_here(inc: Incident) -> bool:
    first = next(iter(inc.events), None)
    return first is not None and first.actor == ACTOR and first.text.startswith(OPENED)


def same_resource(resource: str) -> str:
    """A resource's own name, however it was written: the activity history names a user "wh-demo", Prowler names it
    "arn:aws:iam::1:user/wh-demo". Both are the same thing to open one incident for."""
    return str(resource or "").rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _open_for(db: Session, s: Signal) -> Incident | None:
    """The open incident WhiteHats already has for this signal's resource, if any."""
    name = same_resource(s.resource)
    if not name:
        return None
    rows = db.scalars(select(Incident).join(Signal, Signal.incident_id == Incident.id).where(
        Incident.org_id == s.org_id, Incident.project_id == s.project_id,
        Incident.status.notin_(INCIDENT_CLOSED), Signal.source.in_(SOURCES)).distinct())
    for inc in rows:
        if _opened_here(inc) and any(same_resource(other.resource) == name for other in db.scalars(
                select(Signal).where(Signal.incident_id == inc.id))):
            return inc
    return None


def _summary(s: Signal) -> str:
    where = f"\n\nWhere: {s.resource}" if s.resource else ""
    return f"{s.detail or s.title}{where}".strip()[:2000]


def _context(db: Session, inc: Incident, s: Signal) -> None:
    """The lower-rated signals about the same resource join the incident: what the activity history saw a minute before
    Prowler judged it is part of the same story."""
    name = same_resource(s.resource)
    if not name:
        return
    for other in db.scalars(select(Signal).where(
            Signal.project_id == s.project_id, Signal.source.in_(SOURCES), Signal.closed_at.is_(None),
            Signal.incident_id.is_(None), Signal.learning.is_(False), Signal.baseline.is_(False), Signal.id != s.id)):
        if same_resource(other.resource) == name:
            other.incident_id = inc.id


def open_new(db: Session, only: list[int] | None = None) -> int:
    """Open (or join) an incident for every serious cloud signal not on one yet (of `only`, when given). Sample data
    never opens one. Returns how many were opened."""
    q = select(Signal).where(
        Signal.source.in_(SOURCES), Signal.severity.in_(SEVERITIES), Signal.closed_at.is_(None),
        Signal.incident_id.is_(None), Signal.learning.is_(False), Signal.origin == "live",
        Signal.baseline.is_(False))  # what was already there when WhiteHats first looked is backlog, not an incident
    if only is not None:
        q = q.where(Signal.id.in_(only))
    waiting = list(db.scalars(q.order_by(Signal.created_at)))
    opened = 0
    for s in waiting:
        inc = _open_for(db, s)
        if inc is None:
            inc = Incident(org_id=s.org_id, project_id=s.project_id, title=s.title[:200], severity=s.severity,
                           summary=_summary(s), origin=s.origin)
            try:
                insert_with_next_ref(db, inc)
            except RefTaken:
                log.warning("could not number an incident for signal %s; trying again next pass", s.id)
                continue
            inc.events.append(IncidentEvent(project_id=s.project_id, kind="note", actor=ACTOR,
                                            text=f"{OPENED} from a serious problem in the cloud account: {s.title}"))
            opened += 1
        else:
            inc.events.append(IncidentEvent(project_id=s.project_id, kind="signal", actor=ACTOR,
                                            text=f"Another problem on the same resource: {s.title}"))
            if SEV_ORDER.get(s.severity, 9) < SEV_ORDER.get(inc.severity, 9):
                inc.severity = s.severity
        s.incident_id = inc.id
        _context(db, inc, s)
        db.flush()  # the next signal on this resource finds this incident
    db.commit()
    return opened


def retitle(db: Session, s: Signal, title: str) -> None:
    """The signal's wording changed: an incident WhiteHats opened under its old wording takes the new one."""
    inc = db.get(Incident, s.incident_id) if s.incident_id else None
    if inc is not None and _opened_here(inc) and inc.title == s.title[:200]:
        inc.title = title[:200]


def resolve_fixed(db: Session, only: list[int] | None = None) -> int:
    """Resolve each incident (of `only`, when given) whose findings are all closed: WhiteHats' checks no longer find
    them. One whose findings an approver all called false positives ends as a false positive. One with a fix still
    waiting for a decision or its proof is left for the person. Returns how many ended."""
    ended = 0
    q = select(Incident).join(Signal, Signal.incident_id == Incident.id).where(
        Incident.status.notin_(INCIDENT_CLOSED), Incident.origin == "live")
    if only is not None:
        q = q.where(Incident.id.in_(only))
    for inc in list(db.scalars(q.distinct())):
        signals = list(db.scalars(select(Signal).where(Signal.incident_id == inc.id)))
        if not signals or any(s.closed_at is None for s in signals):
            continue
        if db.scalar(select(Remediation.id).where(Remediation.incident_id == inc.id,
                                                  Remediation.status.in_(UNVERIFIED)).limit(1)) is not None:
            continue  # a fix still waits for a decision or its proof: the person resolves it
        status = "false_positive" if all(s.closed_reason == "false positive" for s in signals) else "resolved"
        reasons = "; ".join(dict.fromkeys(s.closed_reason or "put right" for s in signals))
        inc.events.append(IncidentEvent(project_id=inc.project_id, kind="status", actor=ACTOR,
                                        text=f"Status changed from {inc.status} to {status}"))
        inc.events.append(IncidentEvent(project_id=inc.project_id, kind="note", actor=ACTOR,
                                        text=f"Resolved by WhiteHats: its checks no longer find the problem ({reasons})."
                                        if status == "resolved" else "Every finding on it was called a false positive."))
        inc.status, inc.resolved_at = status, utcnow()
        ended += 1
    db.commit()
    return ended


def sweep(db: Session) -> tuple[int, int]:
    """One pass: open what is new, resolve what was put right. Returns (opened, resolved)."""
    return open_new(db), resolve_fixed(db)

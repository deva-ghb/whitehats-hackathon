"""An approver's word on a finding (FD.1): a false positive, or a risk accepted until a date.

- The finding closes with the reason, and so does every open finding that is the same one (same place, same kind).
- Raised again later by any WhiteHat or scanner, it is saved already closed: one rule, applied as findings are saved
  (`register`), so no detector can forget it.
- It teaches the knowledge sphere an accepted exception, and counts against the WhiteHat that raised it (`precision`).
- Withdrawn, or an accepted risk past its date, it stops applying: the finding opens again and the sphere forgets.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import FindingDecision, Incident, IncidentEvent, Remediation, Signal

FALSE_POSITIVE = "false_positive"
ACCEPTED_RISK = "accepted_risk"
KINDS = (FALSE_POSITIVE, ACCEPTED_RISK)
CLOSED_AS = {FALSE_POSITIVE: "false positive", ACCEPTED_RISK: "risk accepted"}


class DecisionError(ValueError):
    pass


def key(signal: Signal) -> str:
    """What makes two findings the same one: the detector's own fingerprint, else what it said about where."""
    if signal.fingerprint:
        return signal.fingerprint[:300]
    return f"{signal.source}|{signal.title}|{signal.resource or signal.entity}"[:300]


def active(db: Session, project_id: int, at: datetime | None = None) -> dict[str, FindingDecision]:
    """The decisions in force in a project, by finding key."""
    at = at or utcnow()
    rows = db.scalars(select(FindingDecision).where(
        FindingDecision.project_id == project_id, FindingDecision.withdrawn_at.is_(None)))
    return {r.key: r for r in rows if r.until is None or r.until > at}


def _same(db: Session, signal: Signal) -> list[Signal]:
    k = key(signal)
    open_ = db.scalars(select(Signal).where(Signal.project_id == signal.project_id, Signal.closed_at.is_(None)))
    return [s for s in open_ if key(s) == k]


def decide(db: Session, signal: Signal, *, kind: str, by: str, reason: str, until: datetime | None = None,
           remediation: Remediation | None = None) -> FindingDecision:
    """Record the decision and close the finding (and its open twins). The caller commits."""
    if kind not in KINDS:
        raise DecisionError(f"Unknown decision {kind}.")
    if len(reason.strip()) < 8:
        raise DecisionError("Say why in a short sentence: the decision is one somebody owns.")
    if kind == ACCEPTED_RISK and (until is None or until <= utcnow()):
        raise DecisionError("An accepted risk needs a date in the future, when WhiteHats asks again.")
    row = FindingDecision(org_id=signal.org_id, project_id=signal.project_id, key=key(signal), kind=kind,
                          reason=reason.strip()[:2000], decided_by=by[:120], until=until, signal_id=signal.id,
                          source=signal.source, agent_id=signal.agent_id,
                          remediation_id=remediation.id if remediation else None)
    db.add(row)
    now = utcnow()
    for s in {signal, *_same(db, signal)}:
        if s.closed_at is None:
            s.closed_at, s.closed_reason = now, CLOSED_AS[kind]
    db.flush()
    from ..sphere import learn as sphere_learn

    sphere_learn.note(db, org_id=signal.org_id, project_id=signal.project_id, kind="decided", ref=f"decision:{row.id}",
                      actor=by, payload={"decision_id": row.id})
    return row


def withdraw(db: Session, decision: FindingDecision, *, by: str, why: str = "withdrawn") -> int:
    """Stop applying the decision: its findings open again. Returns how many reopened. The caller commits."""
    decision.withdrawn_at, decision.withdrawn_by = utcnow(), by[:120]
    reopened = 0
    for s in db.scalars(select(Signal).where(Signal.project_id == decision.project_id,
                                             Signal.closed_reason == CLOSED_AS[decision.kind])):
        if key(s) == decision.key:
            s.closed_at, s.closed_reason = None, ""
            reopened += 1
            _reopen_incident(db, s, by)
    if decision.remediation_id:
        fix = db.get(Remediation, decision.remediation_id)
        if fix is not None:
            db.refresh(fix)  # as it is now, not as this session first read it
        if fix is not None and fix.status == "accepted":
            fix.status, fix.result = "pending", f"The accepted risk ended ({why}); it waits for a decision again."
    from ..sphere import learn as sphere_learn

    sphere_learn.note_reversal(db, org_id=decision.org_id, project_id=decision.project_id,
                               taught=f"decision:{decision.id}", actor=by)
    return reopened


def _reopen_incident(db: Session, signal: Signal, by: str) -> None:
    """An incident closed as a false positive is open again once one of its findings is: it is not over after all."""
    inc = db.get(Incident, signal.incident_id) if signal.incident_id else None
    if inc is None or inc.status != FALSE_POSITIVE:
        return
    inc.status, inc.resolved_at = "open", None
    inc.events.append(IncidentEvent(project_id=inc.project_id, kind="status", actor=by[:120],
                                    text="Status changed from false_positive to open"))
    inc.events.append(IncidentEvent(project_id=inc.project_id, kind="note", actor=by[:120],
                                    text=f"Reopened: {by} withdrew the false positive on {signal.title}."))


def expire_due(db: Session) -> int:
    """Accepted risks past their date ask again: their findings open. Returns how many ended."""
    now = utcnow()
    ended = list(db.scalars(select(FindingDecision).where(
        FindingDecision.kind == ACCEPTED_RISK, FindingDecision.withdrawn_at.is_(None), FindingDecision.until <= now)))
    for d in ended:
        withdraw(db, d, by="WhiteHats", why=f"its date, {d.until:%d %b %Y}, has passed")
    if ended:
        db.commit()
    return len(ended)


def precision(db: Session, org_id: int, project_id: int | None = None) -> dict[str, dict]:
    """Per WhiteHat or scanner: how many findings it raised and how many approvers called false positives."""
    q = select(Signal.source, Signal.id).where(Signal.org_id == org_id, Signal.learning.is_(False))
    d = select(FindingDecision.source, FindingDecision.id).where(
        FindingDecision.org_id == org_id, FindingDecision.kind == FALSE_POSITIVE, FindingDecision.withdrawn_at.is_(None))
    if project_id is not None:
        q, d = q.where(Signal.project_id == project_id), d.where(FindingDecision.project_id == project_id)
    out: dict[str, dict] = {}
    for source, _ in db.execute(q):
        out.setdefault(source, {"raised": 0, "false_positives": 0})["raised"] += 1
    for source, _ in db.execute(d):
        out.setdefault(source, {"raised": 0, "false_positives": 0})["false_positives"] += 1
    return out


def _hold_back(session: Session, _context, _instances) -> None:
    """As findings are saved: one an approver already decided on is saved closed, with that decision's reason."""
    fresh = [o for o in session.new if isinstance(o, Signal) and o.closed_at is None and o.project_id]
    if not fresh:
        return
    with session.no_autoflush:
        by_project: dict[int, dict[str, FindingDecision]] = {}
        for s in fresh:
            decisions = by_project.get(s.project_id)
            if decisions is None:
                decisions = by_project[s.project_id] = active(session, s.project_id)
            found = decisions.get(key(s))
            if found is not None:
                s.closed_at, s.closed_reason = utcnow(), CLOSED_AS[found.kind]


_registered = False


def register() -> None:
    """Apply the rule to every session (once)."""
    global _registered
    if not _registered:
        event.listen(Session, "before_flush", _hold_back)
        _registered = True

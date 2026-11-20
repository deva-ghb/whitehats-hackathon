"""Moves a fix sent to Fix Factory on once its run has an outcome, so its incident can be resolved.

Fix Factory owns the run; WhiteHats only reads it. A fix becomes `verified` when Fix Factory
proved it and a person approved it there (for a pull request fix: merged every pull request on
GitHub), and `failed` when the run ended without a fix. While its pull requests wait to be merged,
the fix lists them in merge order, exactly as the engine recorded them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Incident, IncidentEvent, Organization, Remediation
from . import factory

log = logging.getLogger("whitehats.fix_outcomes")

ACTOR = "Fix Factory"
PROVEN = ("verified", "review_flagged", "draft")  # draft: not proven, opened for a person's thorough review
DRAFT = "draft"
# Why a finished run left the weakness in place, in the words a person reads on the fix.
NOT_FIXED = {
    "failed": "no attempt passed every check",
    "ci_failed": "CI did not pass on its pull requests",
    "declined": "it judged the fix out of scope",
    "not_reproduced": "it could not reproduce the weakness",
    "error": "the run stopped with an error",
}
UNDONE = {
    "rejected": "a person rejected the proven fix",
    "withdrawn": "the proven fix was withdrawn when the practice repo was reset",
}


@dataclass(frozen=True)
class Outcome:
    status: str  # verified | failed
    result: str
    timeline: str


def _last_event(detail: dict, stage: str) -> str:
    messages = [e.get("message", "") for e in detail.get("events") or [] if e.get("stage") == stage]
    return messages[-1] if messages else ""


def run_outcome(detail: dict) -> Outcome | None:
    """What a Fix Factory run means for the fix that started it. None while it is still going."""
    row = detail.get("row") or {}
    run_id, status, approval = row.get("id"), row.get("status"), row.get("approval")
    if status in PROVEN and approval == "approved":
        delivered = _last_event(detail, "approve") or "Approved"
        if status == DRAFT:
            return Outcome("verified", f"Fix Factory run {run_id}: {delivered.rstrip('.')}, after a person reviewed the "
                                       "draft it could not prove. WhiteHats checks the finding again.",
                           "A person reviewed and merged Fix Factory's draft")
        return Outcome("verified", f"Fix Factory run {run_id}: {delivered.rstrip('.')}.",
                       "Fix Factory proved and delivered")
    reason = UNDONE.get(approval or "") if status in PROVEN else NOT_FIXED.get(status or "")
    if reason is None:
        return None
    why = (detail.get("note") or "").strip()
    text = f"Fix Factory run {run_id} did not fix this: {reason}. A person needs to take it from here."
    return Outcome("failed", f"{text} {why}" if why else text, "Fix Factory could not fix")


@dataclass(frozen=True)
class Opened:
    count: int
    result: str


def opened_pull_requests(detail: dict) -> Opened | None:
    """What a fix says while its proven pull requests wait for a person to merge them. None otherwise."""
    row = detail.get("row") or {}
    pieces = [p for p in detail.get("pieces") or [] if isinstance(p, dict)]
    opened = [p for p in pieces if isinstance(p.get("pull_request"), dict)]
    if row.get("status") not in PROVEN or row.get("approval") != "pending" or not opened or opened != pieces:
        return None
    count = len(opened)
    plural = "" if count == 1 else "s"
    if row.get("status") == DRAFT:
        why = (detail.get("note") or "").strip()
        lead = (f"Fix Factory run {row.get('id')} could not prove a fix, so it opened its best attempt as {count} draft "
                f"pull request{plural} that need{'s' if count == 1 else ''} a thorough human review. Fix Factory's "
                "comment blocks mark each change; remove them once reviewed. Review on GitHub:")
        lines = [lead]
        lines += [f"{p.get('number')}. #{p['pull_request'].get('number')} {p.get('part')}: {p['pull_request'].get('url')}"
                  for p in opened]
        if why:
            lines.append(why)
        return Opened(count, "\n".join(lines))
    lead = f"Fix Factory run {row.get('id')} opened {count} pull request{plural}"
    if all((p.get("ci") or {}).get("state") == "passed" for p in opened):
        lead += ", and CI passed on each"
    lines = [f"{lead}. Merge {'it' if count == 1 else 'them in this order'} on GitHub:"]
    lines += [f"{p.get('number')}. #{p['pull_request'].get('number')} {p.get('part')}: {p['pull_request'].get('url')}"
              for p in opened]
    return Opened(count, "\n".join(lines))


def _settle(db: Session, fix: Remediation, outcome: Outcome) -> None:
    fix.status, fix.result, fix.done_at = outcome.status, outcome.result, utcnow()
    if fix.incident_id:
        inc = db.get(Incident, fix.incident_id)
        inc.events.append(IncidentEvent(kind="action", text=f"{outcome.timeline}: {fix.title}", actor=ACTOR))


def _note_opened(db: Session, fix: Remediation, opened: Opened) -> None:
    fix.result = opened.result
    if fix.incident_id:
        noun = "pull request" if opened.count == 1 else "pull requests"
        inc = db.get(Incident, fix.incident_id)  # held, so the session does not drop it before the append
        inc.events.append(
            IncidentEvent(kind="action", text=f"Fix Factory opened {opened.count} {noun}: {fix.title}", actor=ACTOR))


def sync(db: Session, org: Organization | None, fixes: list[Remediation]) -> None:
    """Read the run of every fix still waiting on Fix Factory and record any outcome. Caller commits."""
    waiting = [f for f in fixes if f.status == "dispatched" and f.factory_run_id]
    if not waiting or org is None or not factory.serves(org):
        return
    for fix in waiting:
        try:
            detail = factory.read_for_screen(org, factory.run_path(fix.factory_run_id))
        except HTTPException as exc:
            # The fix keeps its last known state; the next read tries again.
            log.warning("fix %s: could not read Fix Factory run %s: %s", fix.id, fix.factory_run_id, exc.detail)
            continue
        if not isinstance(detail, dict):
            continue
        outcome = run_outcome(detail)
        if outcome:
            _settle(db, fix, outcome)
            continue
        waiting = opened_pull_requests(detail)
        if waiting and fix.result != waiting.result:
            _note_opened(db, fix, waiting)

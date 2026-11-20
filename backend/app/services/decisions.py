"""The queue a person has to act on.

The Overview list, the nav badge, the briefing and the Approvals header all
count this same list. A bundle of proven code fixes is one decision, not one
per fix and not zero.
"""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..models import Collector, Organization, Remediation, Signal
from ..projects import main_project_id
from . import deploy, factory


def fixable():
    """A waiting fix that can still be applied: it has no finding, or its finding is open. A fix whose finding was closed
    (put right, or no longer found) has nothing left to fix, so it is not offered; nothing is deleted, and reopening the
    finding offers it again."""
    return Remediation.signal_id.is_(None) | exists().where(Signal.id == Remediation.signal_id, Signal.closed_at.is_(None))


def waiting(db: Session, org_id: int, *, project_id: int | None = None, link_prefix: str = "") -> list[dict]:
    """The queue for the whole account, or for one project when project_id is set.

    Fix Factory is the account's code, so it stays on the account queue and on Main.
    """
    org = db.get(Organization, org_id)
    pending_query = select(Remediation).where(Remediation.org_id == org_id, Remediation.status == "pending", fixable())
    if project_id is not None:
        pending_query = pending_query.where(Remediation.project_id == project_id)
    pending = db.scalars(pending_query.order_by(Remediation.created_at)).all()
    decisions = [{"kind": "fix", "id": r.id, "title": r.title, "detail": f"{r.target} · {r.destination}",
                  "severity": r.risk, "link": f"{link_prefix}/approvals"} for r in pending]
    on_main = project_id is None or project_id == main_project_id(db, org_id)
    if org is not None and on_main:
        fstat = factory.status(org)
        if fstat.get("connected"):
            waiting_fixes = fstat["summary"].get("verified", 0) - fstat["summary"].get("approved", 0)
            if waiting_fixes > 0:
                decisions.append({
                    "kind": "factory", "id": 0,
                    "title": f"{waiting_fixes} proven code {'fix' if waiting_fixes == 1 else 'fixes'} waiting for a person",
                    # A pull request fix is approved by merging it on GitHub, so "approve" alone would mislead.
                    "detail": "Fix Factory proved each one works. Approve it there, or merge its pull requests on GitHub.",
                    "severity": "high",
                    "link": f"{link_prefix}/fix-factory",
                })
    cols = db.scalars(select(Collector).where(Collector.org_id == org_id)).all() if on_main else []
    for collector in cols:
        if deploy.health(collector) in ("offline", "crashed"):
            decisions.append({
                "kind": "blind", "id": collector.id, "title": f"{collector.name} has stopped reporting",
                "detail": "A blind spot until it is back", "severity": "medium",
                "link": f"{link_prefix}/collectors/{collector.id}",
            })
    return decisions

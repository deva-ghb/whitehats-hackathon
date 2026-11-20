"""Incidents, signals, the WhiteHat swarm, approvals and the Fix Factory bridge."""

from __future__ import annotations

import hmac
from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import store as audit_store
from ..config import settings
from ..db import get_db, utcnow
from ..incident_refs import RefTaken, insert_with_next_ref
from ..models import INCIDENT_CLOSED, Agent, Incident, IncidentEvent, Organization, Remediation, Signal
from ..permissions import FromObject, allow
from ..projects import MAIN_SLUG
from ..registry import statements as registry_statements
from ..security import Principal, audit
from ..services import (
    auto_incidents,
    coverage,
    decisions,
    factory,
    finding_decisions,
    fix_details,
    fix_jobs,
    fix_outcomes,
    local_workspace,
    setup_checks,
)
from ..services.posture import whitehat_watch
from ..services.tenancy import mark_milestone
from ..sphere import learn as sphere_learn
from ..whitehats import CATALOG, enable

router = APIRouter(prefix="/api")
_INCIDENT = FromObject(Incident, "iid")
_AGENT = FromObject(Agent, "aid")
_FIX = FromObject(Remediation, "rid")
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def signal_out(s: Signal) -> dict:
    return {"id": s.id, "title": s.title, "detail": s.detail, "severity": s.severity, "entity": s.entity,
            "mitre": s.mitre, "source": s.source, "agent": s.agent.name if s.agent else None,
            "incident_id": s.incident_id, "origin": s.origin, "at": s.created_at.isoformat()}


NOT_SIMULATED = "Impact not simulated yet"


def _check_fact(item: dict) -> str:
    check = str(item.get("check") or "").strip()
    before = str(item.get("before") or "").strip()
    after = str(item.get("after") or "").strip()
    if not check or not (before or after):
        return ""
    if before and after:
        return f"{check} {before} before, {after} after"
    return f"{check}: {after or before}"


def evidence_from_detail(detail: dict | None) -> dict | None:
    """Checks a Fix Factory run actually recorded. Empty until those checks exist."""
    if not isinstance(detail, dict):
        return None
    row = detail.get("row") if isinstance(detail.get("row"), dict) else {}
    run_id = row.get("id") or detail.get("run_id")
    facts = []
    for item in detail.get("verification") or []:
        if not isinstance(item, dict):
            continue
        fact = _check_fact(item)
        if fact:
            facts.append(fact)
    if not run_id or not facts:
        return None
    return {"run_id": str(run_id), "facts": facts}


def impact_from_evidence(evidence: dict | None) -> dict:
    """Banner copy. 'Simulated first' only when a linked run produced checks."""
    facts = (evidence or {}).get("facts") or []
    run_id = (evidence or {}).get("run_id") or None
    if not facts or not run_id:
        return {"text": NOT_SIMULATED, "run_id": None}
    return {"text": "Simulated first: " + " · ".join(facts), "run_id": run_id}


def _run_detail(org: Organization, run_id: str) -> dict | None:
    try:
        detail = factory.read_for_screen(org, factory.run_path(run_id))
    except HTTPException:
        return None
    return detail if isinstance(detail, dict) else None


def evidence_by_remediation(org: Organization | None, rows: list[Remediation]) -> dict[int, dict]:
    """Real run checks keyed by fix. The stored simulated_impact string is ignored."""
    linked = [r for r in rows if r.factory_run_id or (r.destination == "Fix Factory" and r.factory_intent)]
    if not linked or org is None or not factory.serves(org):
        return {}
    try:
        board = factory.read_for_screen(org, "/api/runs")
    except HTTPException:
        return {}
    runs = board.get("runs") if isinstance(board, dict) else []
    if not isinstance(runs, list):
        runs = []
    by_id: dict[str, dict] = {}
    newest_finished: dict[str, str] = {}
    for run in runs:
        if not isinstance(run, dict) or not run.get("id"):
            continue
        by_id[str(run["id"])] = run
        item = run.get("item_id")
        if item and item not in newest_finished and run.get("finished_at"):
            newest_finished[item] = str(run["id"])
    details: dict[str, dict | None] = {}
    found: dict[int, dict] = {}
    for r in linked:
        run_id = r.factory_run_id or newest_finished.get(r.factory_intent or "")
        if not run_id:
            continue
        run = by_id.get(run_id)
        if run is not None and not run.get("finished_at"):
            continue
        if run_id not in details:
            details[run_id] = _run_detail(org, run_id)
        evidence = evidence_from_detail(details[run_id])
        if evidence:
            found[r.id] = evidence
    return found


def _sent_to_factory(r: Remediation) -> bool:
    return bool(r.factory_run_id) or (r.result or "").startswith("Sent to Fix Factory")


def _manual(r: Remediation) -> bool:
    return r.status in ("dispatched", "done") and not _sent_to_factory(r)


def _manual_result(r: Remediation) -> str:
    steps = (r.action or "").strip()
    lead = f"Manual: carry this out in {r.destination}."
    return f"{lead}\n{steps}" if steps else lead


def rem_out(r: Remediation, evidence: dict | None = None) -> dict:
    return {"id": r.id, "incident_id": r.incident_id, "signal_id": r.signal_id, "title": r.title, "target": r.target, "action": r.action,
            "why": r.why, "destination": r.destination, "factory_intent": r.factory_intent, "risk": r.risk,
            "simulated_impact": r.simulated_impact, "impact_banner": impact_from_evidence(evidence),
            "factory_run_id": r.factory_run_id, "manual": _manual(r), "status": r.status, "origin": r.origin,
            # A fix for a live setting: only WhiteHats reading the change in the account finishes it (HAND.1).
            "closes_itself": r.destination == setup_checks.DESTINATION and bool(r.signal_id),
            "decided_by": r.decided_by, "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "first_approved_by": r.first_approved_by, "first_approved_by_id": r.first_approved_by_id,
            "done_by": r.done_by, "done_at": r.done_at.isoformat() if r.done_at else None, "result": r.result,
            "created_at": r.created_at.isoformat()}


# ------------------------------------------------------------------ incidents


@router.get("/incidents")
def incidents(status: str | None = None, user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    q = select(Incident).where(Incident.org_id == user.org_id, Incident.project_id == user.project.id)
    if status == "open":
        q = q.where(Incident.status.notin_(INCIDENT_CLOSED))
    elif status:
        q = q.where(Incident.status == status)
    rows = db.scalars(q.order_by(Incident.created_at.desc())).all()
    counts = dict(db.execute(select(Signal.incident_id, func.count(Signal.id)).where(
        Signal.org_id == user.org_id, Signal.project_id == user.project.id).group_by(Signal.incident_id)).all())
    rows = sorted(rows, key=lambda i: (i.closed, SEV_ORDER.get(i.severity, 9), -i.created_at.timestamp()))
    return [{"id": i.id, "ref": i.ref, "title": i.title, "severity": i.severity, "status": i.status,
             "summary": i.summary, "impact": i.business_impact, "owner": i.owner, "signals": counts.get(i.id, 0),
             "origin": i.origin, "created_at": i.created_at.isoformat()} for i in rows]


def _incident(db: Session, user: Principal, iid: int) -> Incident:
    inc = db.get(Incident, iid)
    if inc is None or inc.org_id != user.org_id:
        raise HTTPException(404, "Incident not found.")
    return inc


def _incident_fixes(inc: Incident):
    signal_ids = select(Signal.id).where(Signal.incident_id == inc.id,
                                         Signal.org_id == inc.org_id, Signal.project_id == inc.project_id)
    return select(Remediation).where(
        Remediation.org_id == inc.org_id, Remediation.project_id == inc.project_id,
        or_(Remediation.incident_id == inc.id, Remediation.signal_id.in_(signal_ids)),
    )


@router.get("/incidents/{iid}")
def incident(iid: int, user: Principal = Depends(allow("see_project", _INCIDENT)), db: Session = Depends(get_db)):
    inc = _incident(db, user, iid)
    sigs = db.scalars(select(Signal).where(Signal.incident_id == inc.id).order_by(Signal.created_at)).all()
    rems = db.scalars(_incident_fixes(inc)).all()
    org = db.get(Organization, user.org_id)
    fix_outcomes.sync(db, org, rems)
    db.commit()
    evidence = evidence_by_remediation(org, rems)
    return {"id": inc.id, "ref": inc.ref, "title": inc.title, "severity": inc.severity, "status": inc.status,
            "summary": inc.summary, "impact": inc.business_impact, "owner": inc.owner, "origin": inc.origin,
            "created_at": inc.created_at.isoformat(), "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
            "timeline": [{"at": e.at.isoformat(), "kind": e.kind, "text": e.text, "actor": e.actor} for e in inc.events],
            "signals": [signal_out(s) for s in sigs],
            "remediations": [rem_out(r, evidence.get(r.id)) for r in rems]}


class IncidentPatch(BaseModel):
    status: Literal["investigating", "verifying"] | None = None
    owner: str | None = None
    note: str | None = None
    undo: bool = False


# What a person may set (FD.2). Resolved is WhiteHats' to say, after checking again ("verifying"), or when its own
# checks find every finding gone; a false positive is an approver's decision, with its reason (its own endpoint).
NEXT_STATUS = {
    "open": ("investigating", "verifying"),
    "investigating": ("verifying",),
    "verifying": (),
    "resolved": (),
    "false_positive": (),
}
STATUS_WORDS = {"investigating": "Investigating", "verifying": "Fixed, waiting for WhiteHats to check",
                "resolved": "Resolved", "false_positive": "False positive", "open": "Open"}
# The incident toast offers Undo for the same 10 seconds.
UNDO_SECONDS = 10
UNVERIFIED = ("pending", "dispatched")


def _undo_status(user: Principal, inc: Incident) -> None:
    last = next((e for e in reversed(list(inc.events))
                 if e.kind == "status" and e.text.startswith("Status changed from ")), None)
    if last is None or last.actor_id != user.id:
        raise HTTPException(409, "There is no status change to undo.")
    if (utcnow() - last.at).total_seconds() > UNDO_SECONDS:
        raise HTTPException(409, "Undo is only available for a few seconds.")
    previous, current = last.text.removeprefix("Status changed from ").split(" to ", 1)
    if inc.status != current:
        raise HTTPException(409, "There is no status change to undo.")
    inc.status = previous
    inc.resolved_at = utcnow() if previous == "resolved" else None
    inc.events.append(IncidentEvent(kind="status", text=f"Undone. Status returned to {previous}.",
                                    actor=user.name, actor_id=user.id))


def _change_status(db: Session, user: Principal, inc: Incident, status: str, note: str | None) -> None:
    text = (note or "").strip()
    if len(text) < 8:
        raise HTTPException(400, "Add a short note for the timeline.")
    allowed = NEXT_STATUS.get(inc.status, ())
    if status not in allowed:
        if inc.status == "verifying":
            raise HTTPException(409, "WhiteHats is checking this incident; it resolves it, or sends it back saying "
                                     "what is still there.")
        raise HTTPException(409, f"From {STATUS_WORDS.get(inc.status, inc.status).lower()}, it can move on to "
                                 f"{' or '.join(STATUS_WORDS[s].lower() for s in allowed) or 'nothing'}.")
    failed_fixes = []
    if status == "verifying":
        open_fixes = db.scalars(_incident_fixes(inc).where(Remediation.status.in_(UNVERIFIED))).all()
        fix_outcomes.sync(db, db.get(Organization, user.org_id), open_fixes)
        open_fixes = [r for r in open_fixes if r.status in UNVERIFIED]
        if open_fixes:
            titles = "; ".join(r.title for r in open_fixes)
            if any(r.status == "pending" for r in open_fixes):
                raise HTTPException(409, f"Decide every proposed fix first. Still waiting: {titles}.")
            raise HTTPException(409, f"A fix is not verified yet, so it cannot be called fixed: {titles}.")
        failed_fixes = db.scalars(_incident_fixes(inc).where(Remediation.status == "failed")).all()
    previous = inc.status
    inc.events.append(IncidentEvent(kind="status", text=f"Status changed from {previous} to {status}",
                                    actor=user.name, actor_id=user.id))
    inc.events.append(IncidentEvent(kind="note", text=text, actor=user.name))
    if failed_fixes:
        # A failed fix does not block it (R-045); the record says so, so nobody reads it as fixed by the tool.
        inc.events.append(IncidentEvent(
            kind="note", actor=user.name,
            text="Warning: called fixed by a person although the automated fix failed: "
                 + "; ".join(r.title for r in failed_fixes)))
    if status == "verifying":
        inc.events.append(IncidentEvent(kind="note", actor="WhiteHats",
                                        text="WhiteHats will check each finding again and resolve the incident if "
                                             "they are gone."))
    inc.status = status
    inc.resolved_at = None


@router.patch("/incidents/{iid}")
def update_incident(iid: int, body: IncidentPatch, user: Principal = Depends(allow("triage", _INCIDENT)),
                    db: Session = Depends(get_db)):
    inc = _incident(db, user, iid)
    if body.undo:
        _undo_status(user, inc)
    elif body.status and body.status != inc.status:
        _change_status(db, user, inc, body.status, body.note)
    elif body.note:
        inc.events.append(IncidentEvent(kind="note", text=body.note, actor=user.name))
    if body.owner is not None and body.owner != inc.owner:
        inc.owner = body.owner
        inc.events.append(IncidentEvent(kind="note", text=f"Owner set to {body.owner}", actor=user.name))
    audit(db, user, "updated incident", inc.ref, body.model_dump_json(exclude_none=True))
    db.commit()
    return incident(iid, user, db)


@router.get("/signals")
def signals(days: int = 7, unlinked: bool = False, user: Principal = Depends(allow("see_project")),
            db: Session = Depends(get_db)):
    q = select(Signal).where(Signal.org_id == user.org_id, Signal.project_id == user.project.id,
                             Signal.created_at >= utcnow() - timedelta(days=days))
    q = q.where(Signal.closed_at.is_(None))  # a finding a later commit closed is history, not something to open an incident on
    q = q.where(Signal.learning.is_(False))  # a learning WhiteHat's signal is a trial, not something to act on (GL.2)
    if unlinked:
        q = q.where(Signal.incident_id.is_(None))
    rows = db.scalars(q.order_by(Signal.created_at.desc()).limit(300)).all()
    return [signal_out(s) for s in rows]


class IncidentIn(BaseModel):
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    signal_ids: list[int]
    summary: str = ""


def _insert_with_next_ref(db: Session, inc: Incident) -> None:
    try:
        insert_with_next_ref(db, inc)
    except RefTaken:
        raise HTTPException(409, "Other incidents were opened at the same moment. Open this one again.") from None


@router.post("/incidents")
def open_incident(body: IncidentIn, user: Principal = Depends(allow("triage")), db: Session = Depends(get_db)):
    """A person opens an incident. Nothing here groups signals on its own."""
    title = body.title.strip()
    if len(title) < 8:
        raise HTTPException(400, "Give the incident a title of at least a short sentence.")
    ids = list(dict.fromkeys(body.signal_ids))
    if not ids:
        raise HTTPException(400, "Choose at least one signal.")
    if len(ids) > 50:
        raise HTTPException(400, "Choose at most 50 signals.")
    rows = db.scalars(select(Signal).where(
        Signal.id.in_(ids), Signal.org_id == user.org_id, Signal.project_id == user.project.id,
        Signal.learning.is_(False))).all()
    if len(rows) != len(ids):
        raise HTTPException(404, "A chosen signal is not in this project.")
    taken = [s for s in rows if s.incident_id]
    if taken:
        raise HTTPException(409, "Already on an incident: " + "; ".join(s.title for s in taken))
    origin = "live" if any(s.origin != "sample" for s in rows) else "sample"
    inc = Incident(org_id=user.org_id, project_id=user.project.id, title=title[:200],
                   severity=body.severity, summary=body.summary.strip()[:2000], origin=origin)
    _insert_with_next_ref(db, inc)
    for s in rows:
        s.incident_id = inc.id
    joined = "; ".join(s.title for s in rows)
    inc.events.append(IncidentEvent(
        kind="note", actor=user.name, actor_id=user.id,
        text=f"Opened by hand from {len(rows)} signal(s): {joined}",
    ))
    audit(db, user, "opened incident", inc.ref, title)
    db.commit()
    return incident(inc.id, user, db)


# ------------------------------------------------------------------ the swarm


@router.get("/agents")
def agents(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    week = utcnow() - timedelta(days=7)
    counts, learned = {}, {}
    for agent_id, learning, n in db.execute(select(Signal.agent_id, Signal.learning, func.count(Signal.id))
                                            .where(Signal.org_id == user.org_id, Signal.project_id == user.project.id,
                                                   Signal.created_at >= week)
                                            .group_by(Signal.agent_id, Signal.learning)):
        (learned if learning else counts)[agent_id] = n
    rows = db.scalars(select(Agent).where(Agent.org_id == user.org_id, Agent.project_id == user.project.id)
                      .order_by(Agent.id)).all()
    have = {a.key for a in rows}
    watched = whitehat_watch(db, user.org_id, project_ids=[user.project.id])
    by_id = {row["id"]: row["state"] for row in watched["agents"]}
    measured = coverage.measure(db, user.org_id, user.project.id, [a.key for a in rows])  # COV.1
    out = []
    for a in rows:
        w = CATALOG.get(a.key)
        item = {"id": a.id, "key": a.key, "name": a.name,
                "domain": w.domain if w else a.domain, "status": a.status,
                "coverage": measured[a.key]["percent"], "covers": measured[a.key], "signals_7d": counts.get(a.id, 0), "would_have_7d": learned.get(a.id, 0),
                "last_signal_at": a.last_signal_at.isoformat() if a.last_signal_at else None,
                "health": a.health or {},
                "detects": bool(w and w.available), "info": w.info() if w else None}
        if a.id in by_id:
            item["watch"] = by_id[a.id]
        out.append(item)
    catalog = [w.info() for k, w in CATALOG.items() if k not in have]
    return {"agents": out, "catalog": catalog,
            "summary": {"watching": watched["watching"], "blind_spots": watched["blind_spots"],
                        "roster": watched["watching"] + watched["blind_spots"]}}


class AddAgent(BaseModel):
    key: str


@router.post("/agents")
def add_agent(body: AddAgent, user: Principal = Depends(allow("edit_model")), db: Session = Depends(get_db)):
    w = CATALOG.get(body.key)
    if w is None:
        raise HTTPException(404, "No WhiteHat with that key.")
    if not w.available:
        raise HTTPException(409, f"{w.name} cannot be switched on yet: {w.unavailable_reason}")
    a = enable(db, user.org_id, body.key, project_id=user.project.id)
    audit(db, user, "switched on WhiteHat", w.name)
    db.commit()
    return {"id": a.id}


class AgentPatch(BaseModel):
    status: Literal["active", "learning", "dark"]


@router.patch("/agents/{aid}")
def update_agent(aid: int, body: AgentPatch, user: Principal = Depends(allow("edit_model", _AGENT)), db: Session = Depends(get_db)):
    a = db.get(Agent, aid)
    if a is None or a.org_id != user.org_id:
        raise HTTPException(404, "WhiteHat not found.")
    a.status = body.status
    audit(db, user, "changed WhiteHat status", a.name, body.status)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ approvals


REMEDIATION_PAGE = 20  # the most fixes one page of Approvals shows


@router.get("/remediations")
def remediations(status: Literal["pending", "decided"] | None = None, page: int | None = None,
                 size: int = REMEDIATION_PAGE, user: Principal = Depends(allow("see_project")),
                 db: Session = Depends(get_db)):
    """The project's fixes, waiting ones first. With `page` (from 1): one page of the waiting or the decided ones,
    with how many there are in all."""
    q = select(Remediation).where(Remediation.org_id == user.org_id, Remediation.project_id == user.project.id)
    if status == "pending":
        q = q.where(Remediation.status == "pending", decisions.fixable())
    elif status == "decided":
        q = q.where(Remediation.status != "pending")
    else:
        q = q.where((Remediation.status != "pending") | decisions.fixable())
    q = q.order_by(Remediation.status != "pending", Remediation.created_at.desc(), Remediation.id.desc())
    total = None
    if page is not None:
        size = max(1, min(size, 100))
        total = db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
        page = max(1, min(page, max(1, -(-total // size))))
        q = q.limit(size).offset((page - 1) * size)
    rows = db.scalars(q).all()
    org = db.get(Organization, user.org_id)
    fix_outcomes.sync(db, org, rows)
    db.commit()
    evidence = evidence_by_remediation(org, rows)
    items = [rem_out(r, evidence.get(r.id)) for r in rows]
    if page is None:
        return items
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/remediations/{rid}")
def remediation(rid: int, user: Principal = Depends(allow("see_project", _FIX)), db: Session = Depends(get_db)):
    """One fix with what a person reads before deciding it (FX.1)."""
    r = _owned(db, user, rid)
    org = db.get(Organization, user.org_id)
    fix_outcomes.sync(db, org, [r])
    db.commit()
    return {**rem_out(r, evidence_by_remediation(org, [r]).get(r.id)), **fix_details.details(db, org, r)}


def _owned(db: Session, user: Principal, rid: int) -> Remediation:
    r = db.get(Remediation, rid)
    if r is None or r.org_id != user.org_id:
        raise HTTPException(404, "Fix not found.")
    return r


def _rem(db: Session, user: Principal, rid: int, *, retry: bool = False) -> Remediation:
    """A fix still waiting for a decision. With `retry`, a Fix Factory fix whose run failed may be applied again."""
    r = _owned(db, user, rid)
    again = retry and r.status == "failed" and r.destination == "Fix Factory" and bool(r.factory_intent)
    if r.status != "pending" and not again:
        raise HTTPException(409, "This fix has already been decided.")
    return r


# The spec's "critical fixes" are the ones whose risk is critical.
TWO_APPROVER_RISK = "critical"


def _needs_two(user: Principal, r: Remediation) -> bool:
    return bool(user.project.two_approver) and r.risk == TWO_APPROVER_RISK


def _first_approval(db: Session, user: Principal, r: Remediation) -> dict:
    """Record the first of two approvals. The fix stays pending until a different Approver decides."""
    r.first_approved_by, r.first_approved_by_id, r.first_approved_at = user.name, user.id, utcnow()
    if r.incident_id:
        # Held in a name: a parent that is only an expression can be garbage collected before the append lands.
        inc = db.get(Incident, r.incident_id)
        inc.events.append(IncidentEvent(
            kind="action", text=f"First approval: {r.title}. A second Approver must decide.", actor=user.name))
    audit(db, user, "gave the first of two approvals", r.title, r.destination)
    db.commit()
    return rem_out(r)


@router.post("/remediations/{rid}/approve")
def approve(rid: int, user: Principal = Depends(allow("approve", _FIX)), db: Session = Depends(get_db)):
    r = _rem(db, user, rid, retry=True)
    tried_again = r.status == "failed"
    if _needs_two(user, r):
        if r.first_approved_by_id is None:
            return _first_approval(db, user, r)
        if r.first_approved_by_id == user.id:
            raise HTTPException(409, "This project needs two different Approvers for a critical fix. "
                                     "You gave the first approval; someone else must give the second.")
    org = db.get(Organization, user.org_id)
    if r.destination == "Fix Factory" and r.factory_intent:
        if not local_workspace.ENABLED:
            if r.factory_intent.startswith(fix_jobs.FINDING_INTENTS):
                _send_finding_fix(db, org, r)
            else:
                _factory_here(user)
        request = {"intent": r.factory_intent, "mode": "live"}
        if local_workspace.ENABLED:
            request["title"] = r.title
        out = factory.call(org, "POST", "/api/runs", request)
        run_id = out.get("run") if isinstance(out, dict) else None
        if not run_id:
            raise HTTPException(502, "Fix Factory did not start a run, so the fix was not sent.")
        r.factory_run_id = str(run_id)
        r.result = (f"Sent to Fix Factory as run {r.factory_run_id}. "
                    "It will prove the fix before anyone merges it.")
        if tried_again:
            r.result = f"Tried again by {user.name} after the last run failed. {r.result}"
    elif r.destination == setup_checks.DESTINATION:
        r.result = (f"Taken on by {user.name}. WhiteHats closes this fix when it reads the change in AWS.\n"
                    f"{(r.action or '').strip()}")
    else:
        r.result = _manual_result(r)
    decided_by = f"{r.first_approved_by} and {user.name}" if r.first_approved_by_id else user.name
    r.status, r.decided_by, r.decided_at = "dispatched", decided_by[:120], utcnow()
    sphere_learn.note_fix(db, r, decided_by)  # KS.1: the rule this fix protects becomes known
    mark_milestone(db, user.org_id, "first_decision")
    if r.incident_id:
        inc = db.get(Incident, r.incident_id)
        inc.events.append(IncidentEvent(kind="action", text=f"Approved: {r.title} via {r.destination}", actor=user.name))
        if inc.status == "open":
            inc.status = "investigating"
    audit(db, user, "approved fix", r.title, r.destination)
    db.commit()
    return rem_out(r)


class Reject(BaseModel):
    reason: str = ""


@router.post("/remediations/{rid}/reject")
def reject(rid: int, body: Reject, user: Principal = Depends(allow("approve", _FIX)), db: Session = Depends(get_db)):
    r = _rem(db, user, rid)
    r.status, r.decided_by, r.decided_at, r.result = "rejected", user.name, utcnow(), body.reason or "Rejected"
    mark_milestone(db, user.org_id, "first_decision")
    if r.incident_id:
        inc = db.get(Incident, r.incident_id)  # held, as in _first_approval
        inc.events.append(
            IncidentEvent(kind="action", text=f"Rejected: {r.title}. {body.reason}", actor=user.name))
    audit(db, user, "rejected fix", r.title, body.reason)
    db.commit()
    return rem_out(r)


class ByHand(BaseModel):
    note: str = ""


@router.post("/remediations/{rid}/by-hand")
def by_hand(rid: int, body: ByHand, user: Principal = Depends(allow("approve", _FIX)), db: Session = Depends(get_db)):
    """FD.3: the team fixes it themselves instead of Fix Factory. When they say it is done, WhiteHats checks it."""
    r = _rem(db, user, rid)
    r.destination, r.factory_intent = "By hand", ""
    r.status, r.decided_by, r.decided_at = "dispatched", user.name, utcnow()
    r.result = (f"{user.name}'s team fixes this by hand. Mark it done when it is, and WhiteHats checks it."
                + (f"\n{body.note.strip()}" if body.note.strip() else ""))
    mark_milestone(db, user.org_id, "first_decision")
    sphere_learn.note_fix(db, r, user.name)  # an approved fix teaches the rule it protects, however it is made
    if r.incident_id:
        db.get(Incident, r.incident_id).events.append(
            IncidentEvent(kind="action", text=f"Fixing by hand: {r.title}", actor=user.name))
    audit(db, user, "chose to fix by hand", r.title)
    db.commit()
    return rem_out(r)


class AcceptRisk(BaseModel):
    reason: str = Field(min_length=8, max_length=2000)
    until: date


@router.post("/remediations/{rid}/accept-risk")
def accept_risk(rid: int, body: AcceptRisk, user: Principal = Depends(allow("approve", _FIX)),
                db: Session = Depends(get_db)):
    """FD.3: accept the risk until a date, with the reason. The finding closes and asks again on that date."""
    r = _rem(db, user, rid)
    signal = db.get(Signal, r.signal_id) if r.signal_id else None
    if signal is None or signal.org_id != user.org_id:
        raise HTTPException(409, "This fix is not about a finding, so there is no risk to accept. Reject it instead.")
    until = datetime.combine(body.until, time(23, 59, 59))
    try:
        finding_decisions.decide(db, signal, kind=finding_decisions.ACCEPTED_RISK, by=user.name, reason=body.reason,
                                 until=until, remediation=r)
    except finding_decisions.DecisionError as exc:
        raise HTTPException(422, str(exc)) from exc
    r.status, r.decided_by, r.decided_at = "accepted", user.name, utcnow()
    r.result = f"Risk accepted by {user.name} until {until:%d %b %Y}: {body.reason.strip()}"
    if r.incident_id:
        db.get(Incident, r.incident_id).events.append(
            IncidentEvent(kind="action", text=f"Risk accepted until {until:%d %b %Y}: {r.title}", actor=user.name))
    audit(db, user, "accepted a risk", r.title, body.reason)
    db.commit()
    return rem_out(r)


class FalsePositive(BaseModel):
    reason: str = Field(min_length=8, max_length=2000)


_SIGNAL = FromObject(Signal, "sid")


@router.post("/signals/{sid}/false-positive")
def signal_false_positive(sid: int, body: FalsePositive, user: Principal = Depends(allow("approve", _SIGNAL)),
                          db: Session = Depends(get_db)):
    """FD.1: an approver says the finding is wrong. It closes, never alerts again, and teaches WhiteHats."""
    signal = db.get(Signal, sid)
    if signal is None or signal.org_id != user.org_id or signal.project_id != user.project.id:
        raise HTTPException(404, "No finding with that id.")
    try:
        decision = finding_decisions.decide(db, signal, kind=finding_decisions.FALSE_POSITIVE, by=user.name,
                                            reason=body.reason)
    except finding_decisions.DecisionError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "called a finding a false positive", signal.title, body.reason)
    db.commit()
    auto_incidents.resolve_fixed(db)  # an incident whose findings are all decided ends
    return {"ok": True, "decision": decision.id}


@router.post("/signals/{sid}/reopen")
def signal_reopen(sid: int, user: Principal = Depends(allow("approve", _SIGNAL)), db: Session = Depends(get_db)):
    """FD.1: withdraw the false positive or accepted risk on this finding: it (and its twins) count again."""
    signal = db.get(Signal, sid)
    if signal is None or signal.org_id != user.org_id or signal.project_id != user.project.id:
        raise HTTPException(404, "No finding with that id.")
    decision = finding_decisions.active(db, signal.project_id).get(finding_decisions.key(signal))
    if decision is None:
        raise HTTPException(409, "No false positive or accepted risk is in force on this finding.")
    reopened = finding_decisions.withdraw(db, decision, by=user.name)
    audit(db, user, "reopened a finding", signal.title, decision.kind)
    db.commit()
    return {"ok": True, "reopened": reopened}


@router.get("/false-positives")
def false_positives(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    """FD.1: per WhiteHat or scanner, how many findings it raised in this project and how many were false positives."""
    return finding_decisions.precision(db, user.org_id, user.project.id)


@router.post("/incidents/{iid}/false-positive")
def incident_false_positive(iid: int, body: FalsePositive, user: Principal = Depends(allow("approve", _INCIDENT)),
                            db: Session = Depends(get_db)):
    """FD.1: the whole incident was a false alarm: every finding on it is a false positive, with the reason."""
    inc = _incident(db, user, iid)
    if inc.closed:
        raise HTTPException(409, "This incident is already closed.")
    for signal in db.scalars(select(Signal).where(Signal.incident_id == inc.id, Signal.closed_at.is_(None))):
        finding_decisions.decide(db, signal, kind=finding_decisions.FALSE_POSITIVE, by=user.name, reason=body.reason)
    previous = inc.status
    inc.status, inc.resolved_at = "false_positive", utcnow()
    inc.events.append(IncidentEvent(kind="status", text=f"Status changed from {previous} to false_positive",
                                    actor=user.name, actor_id=user.id))
    inc.events.append(IncidentEvent(kind="note", text=f"False positive: {body.reason.strip()}", actor=user.name))
    audit(db, user, "called an incident a false positive", inc.ref, body.reason)
    db.commit()
    return incident(iid, user, db)


@router.post("/remediations/{rid}/done")
def mark_done(rid: int, user: Principal = Depends(allow("triage", _FIX)), db: Session = Depends(get_db)):
    r = _owned(db, user, rid)
    if r.status == "done":
        raise HTTPException(409, "This fix is already marked done.")
    if _sent_to_factory(r):
        raise HTTPException(409, "This fix was sent to Fix Factory. It is finished when that run is approved there.")
    if r.status != "dispatched":
        raise HTTPException(409, "Approve this fix before marking it done.")
    if setup_checks.awaits_the_setting(db, r):
        raise HTTPException(409, "WhiteHats closes this fix itself when it reads the change in AWS, "
                                 "so it cannot be marked done by hand.")
    r.done_by, r.done_at = user.name, utcnow()
    if r.signal_id:
        # FD.2: WhiteHats checks the finding again; the fix is verified when the check shows it gone.
        r.status = "verifying"
        r.result = ((r.result + "\n") if r.result else "") + f"{user.name} says it is done; WhiteHats is checking."
    else:
        r.status = "done"
    if r.incident_id:
        inc = db.get(Incident, r.incident_id)
        inc.events.append(IncidentEvent(kind="action", text=f"Marked done: {r.title}", actor=user.name))
    audit(db, user, "marked fix done", r.title, r.destination)
    db.commit()
    return rem_out(r)


# ------------------------------------------------------------------ Fix Factory bridge


class Recheck(BaseModel):
    rule: str = Field(min_length=1, max_length=300)
    scanner: str = Field(min_length=1, max_length=40)
    path: str = Field(min_length=1, max_length=500)
    files: dict[str, str]

# The engine works on the account's code, and that code belongs to one project: Main, until projects name
# their own repositories (CN.1). Every other project is told so and never sees the engine's runs.
FACTORY_HOME = MAIN_SLUG
NOT_HERE = "Fix Factory works on the code of the Main project. Switch to Main to see its runs."
OWN_RUNS = "The runs this project's fixes started. Apply Fix on Approvals sends a fix here."


def _factory_here(user: Principal, db: Session | None = None, run_id: str | None = None) -> None:
    """Main's code is the engine's; any other project sees only the runs its own fixes started."""
    if user.project.slug == FACTORY_HOME:
        return
    if db is not None and run_id and db.scalar(select(Remediation.id).where(
            Remediation.org_id == user.org_id, Remediation.project_id == user.project.id,
            Remediation.factory_run_id == run_id).limit(1)) is not None:
        return
    raise HTTPException(409, NOT_HERE)


def _send_finding_fix(db: Session, org: Organization, r: Remediation) -> None:
    """Give the engine the fix made from the finding, to wait in its inbox until the run below starts it."""
    repos = factory.call(org, "GET", "/api/repos")
    try:
        made = fix_jobs.intent(db, r, repos if isinstance(repos, list) else [])
    except fix_jobs.NotFixable as exc:
        raise HTTPException(409, str(exc)) from exc
    factory.call(org, "POST", "/api/intents", made)


@router.post("/factory/recheck")
def factory_recheck(body: Recheck, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    """Fix Factory's proof for a finding WhiteHats raised: the rule that found it, run again on the changed code.

    The engine calls this with the dispatch token it shares with this server; no person signs in to it.
    """
    if not settings.factory_token or not hmac.compare_digest(f"Bearer {settings.factory_token}", authorization):
        raise HTTPException(401, "Fix Factory's token is needed to recheck a finding.")
    try:
        org = db.scalar(select(Organization).where(Organization.slug == settings.factory_tenant))
        if body.scanner == audit_store.SCANNER:
            if org is None:
                raise ValueError("Fix Factory's company is not set up on this server.")
            return audit_store.recheck(db, org.id, body.rule, body.files)
        statements = registry_statements.for_company(db, org.id) if org is not None else []
        return fix_jobs.recheck(body.rule, body.scanner, body.path, body.files, statements)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _run_in_project(db: Session, user: Principal, run_id: str) -> None:
    """A run that a fix in another project started is that project's, so it answers as missing here."""
    elsewhere = db.scalar(select(Remediation.id).where(
        Remediation.org_id == user.org_id, Remediation.factory_run_id == run_id,
        Remediation.project_id != user.project.id).limit(1))
    if elsewhere is not None:
        raise HTTPException(404, "No Fix Factory run has that id.")


@router.get("/factory/status")
def factory_status(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    """Main works with the engine directly; another project sees the runs its own fixes started (FF.SRV)."""
    status = factory.status(db.get(Organization, user.org_id))
    if user.project.slug != FACTORY_HOME and status.get("connected"):
        return {**status, "own_runs_only": True, "message": OWN_RUNS}
    return status


def _own(db: Session, user: Principal, column) -> set[str]:
    """This project's fixes' run ids or intents."""
    return set(db.scalars(select(column).where(
        Remediation.org_id == user.org_id, Remediation.project_id == user.project.id, column.is_not(None))))


@router.get("/factory/inbox")
def factory_inbox(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    answer = factory.call(db.get(Organization, user.org_id), "GET", "/api/inbox")
    if user.project.slug == FACTORY_HOME or not isinstance(answer, list):
        return answer
    mine = _own(db, user, Remediation.factory_intent)
    return [item for item in answer if item.get("id") in mine]


@router.get("/factory/runs")
def factory_runs(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    answer = factory.call(db.get(Organization, user.org_id), "GET", "/api/runs")
    if not (isinstance(answer, dict) and isinstance(answer.get("runs"), list)):
        return answer
    if user.project.slug != FACTORY_HOME:
        mine = _own(db, user, Remediation.factory_run_id)
        runs = [run for run in answer["runs"] if run.get("id") in mine]
        return {**answer, "runs": runs, "summary": _summary(runs)}
    # A run that a fix in another project started belongs to that project, as in _run_in_project.
    elsewhere = set(db.scalars(select(Remediation.factory_run_id).where(
        Remediation.org_id == user.org_id, Remediation.factory_run_id.is_not(None),
        Remediation.project_id != user.project.id)))
    answer["runs"] = [run for run in answer["runs"] if run.get("id") not in elsewhere]
    return answer


def _summary(runs: list[dict]) -> dict:
    """The engine's scorecard (its store.summary) over these runs only: finished live runs."""
    done = [run for run in runs if run.get("finished_at") and run.get("mode") == "live"]
    verified = [run for run in done if run.get("status") == "verified"]
    scores = [run["score"] for run in done if run.get("score") is not None]
    cost = round(sum(run.get("cost_usd") or 0 for run in done), 4)
    seconds = [run["seconds"] for run in verified if run.get("seconds") is not None]
    return {"runs": len(done), "verified": len(verified),
            "approved": sum(1 for run in done if run.get("approval") == "approved"),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None, "cost_usd": cost,
            "cost_per_fix": round(cost / max(len(verified), 1), 4),
            "avg_seconds": round(sum(seconds) / len(seconds), 1) if seconds else None}


@router.get("/factory/runs/{run_id}")
def factory_run(run_id: str, user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    _factory_here(user, db, run_id)
    path = factory.run_path(run_id)
    _run_in_project(db, user, run_id)
    return factory.call(db.get(Organization, user.org_id), "GET", path)


class StartRun(BaseModel):
    intent: str
    mode: Literal["live", "replay"] = "live"


@router.post("/factory/runs")
def factory_start(body: StartRun, user: Principal = Depends(allow("triage")), db: Session = Depends(get_db)):
    _factory_here(user)
    out = factory.call(db.get(Organization, user.org_id), "POST", "/api/runs", body.model_dump())
    audit(db, user, "sent finding to Fix Factory", body.intent, body.mode)
    db.commit()
    return out


@router.post("/factory/runs/{run_id}/{decision}")
def factory_decide(run_id: str, decision: str, user: Principal = Depends(allow("approve")), db: Session = Depends(get_db)):
    if decision not in ("approve", "reject"):
        raise HTTPException(404, "Unknown decision.")
    _factory_here(user, db, run_id)
    path = factory.run_path(run_id, decision)
    _run_in_project(db, user, run_id)
    org = db.get(Organization, user.org_id)
    out = factory.call(org, "POST", path)
    audit(db, user, f"{decision}d proven fix", run_id)
    fixes = list(db.scalars(select(Remediation).where(
        Remediation.org_id == user.org_id, Remediation.project_id == user.project.id,
        Remediation.factory_run_id == run_id)))
    fix_outcomes.sync(db, org, fixes)
    mark_milestone(db, user.org_id, "first_decision")
    if decision == "approve":
        mark_milestone(db, user.org_id, "first_fix")
        for fix in fixes:
            sphere_learn.note_fix(db, fix, user.name)  # KS.1
    db.commit()
    return out


@router.post("/factory/practice/reset")
def factory_reset(user: Principal = Depends(allow("manage_account_setup")), db: Session = Depends(get_db)):
    """The practice repository is the account's, so resetting it is an account setting, not a project one."""
    return factory.call(db.get(Organization, user.org_id), "POST", "/api/practice/reset")

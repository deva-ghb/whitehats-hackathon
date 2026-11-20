"""Security audits for the screens (AUD.6): the project's audits, one audit as it runs, and asking for one.

Anyone who may see the project reads them; a person who may triage asks for an audit by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Audit, Remediation, Signal
from ..permissions import allow
from ..security import Principal, audit
from ..services import audit_jobs, local_workspace

router = APIRouter(prefix="/api")
EVENTS_SHOWN = 200


class Ask(BaseModel):
    reason: str = Field(default="", max_length=500)
    instructions: str = Field(default="", max_length=1000)
    scope: list[str] = Field(default_factory=list, max_length=50)


def _summary(row: Audit) -> dict:
    return {"id": row.id, "trigger": row.trigger, "trigger_detail": row.trigger_detail, "scope": row.scope,
            "instructions": row.instructions, "profile": row.profile, "repo": row.repo, "branch": row.branch, "status": row.status, "step": row.step,
            "counts": row.counts or {}, "coverage_percent": row.coverage_percent, "cost_usd": round(row.cost_usd, 2),
            "error": row.error, "created_at": row.created_at.isoformat() if row.created_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None}


@router.get("/audits")
def list_audits(db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    assert user.project is not None
    rows = db.scalars(select(Audit).where(Audit.org_id == user.org_id, Audit.project_id == user.project.id)
                      .order_by(Audit.id.desc()).limit(50)).all()
    audits = []
    for row in rows:
        progress = local_workspace.audit_progress(db, row) if local_workspace.ENABLED else {}
        audits.append({**_summary(row), **{key: value for key, value in progress.items() if key != "events"}})
    return {"audits": audits, "running": local_workspace.ENABLED or audit_jobs.enabled()}


@router.get("/audits/{audit_id}")
def read_audit(audit_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    assert user.project is not None
    row = db.get(Audit, audit_id)
    if row is None or row.org_id != user.org_id or row.project_id != user.project.id:
        raise HTTPException(404, "No audit with that id.")
    run_dir = Path(row.run_dir) if row.run_dir else None
    progress = local_workspace.audit_progress(db, row) if local_workspace.ENABLED else {}
    out = {"events": _events(run_dir), **_summary(row), **progress}
    out["records"] = [_record(r) for r in _json(run_dir, "findings.json", [])]
    out["units"] = [{"surface": u["surface"], "boundary": u["boundary"], "attack_class": u["attack_class"],
                     "paths": u["starting_paths"], "status": u["status"], "unresolved": u["unresolved"]}
                    for u in _json(run_dir, "coverage-ledger.json", [])]
    signals = db.scalars(select(Signal).where(Signal.audit_id == row.id, Signal.org_id == user.org_id)).all()
    fixes = {f.signal_id: f for f in db.scalars(select(Remediation).where(
        Remediation.signal_id.in_([s.id for s in signals]), Remediation.org_id == user.org_id))} if signals else {}
    out["findings"] = [{"id": s.id, "title": s.title, "severity": s.severity, "where": s.entity,
                        "fingerprint": s.fingerprint, "closed": s.closed_at is not None,
                        "fix": ({"id": fixes[s.id].id, "status": fixes[s.id].status} if s.id in fixes else None)}
                       for s in signals]
    return out


@router.post("/audits/{audit_id}/cancel")
def cancel_audit(audit_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("triage"))):
    """AUD.7: stop an audit that is waiting or running. A running one stops at its next step."""
    assert user.project is not None
    row = db.get(Audit, audit_id)
    if row is None or row.org_id != user.org_id or row.project_id != user.project.id:
        raise HTTPException(404, "No audit with that id.")
    try:
        if local_workspace.ENABLED and row.local_duration_seconds:
            local_workspace.cancel_audit(db, row)
        else:
            audit_jobs.cancel(db, row)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, user, "cancelled an audit", f"audit {row.id}", row.trigger_detail[:200])
    db.commit()
    return _summary(row)


@router.post("/audits")
def ask_for_audit(body: Ask, db: Session = Depends(get_db), user: Principal = Depends(allow("triage"))):
    assert user.project is not None
    detail = f"{user.name} asked" + (f": {body.reason}" if body.reason else "")
    scope = sorted({path.strip().lstrip("/") for path in body.scope if path.strip()})
    if local_workspace.ENABLED:
        row = Audit(org_id=user.org_id, project_id=user.project.id, trigger="request",
                    trigger_detail=detail, scope=scope, instructions=body.instructions.strip())
        db.add(row)
        local_workspace.start_audit(db, row)
        db.flush()
    else:
        row = audit_jobs.request(db, user.org_id, user.project.id, "request", detail=detail, scope=scope)
    audit(db, user, "audit.request", f"audit {row.id}", body.reason or "whole repository")
    db.commit()
    return _summary(row)


def _json(run_dir: Path | None, name: str, default):
    path = run_dir / name if run_dir else None
    if path is None or not path.is_file():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _events(run_dir: Path | None) -> list[dict]:
    path = run_dir / "events.jsonl" if run_dir else None
    if path is None or not path.is_file():
        return []
    lines = path.read_text().splitlines()[-EVENTS_SHOWN:]
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        out.append({k: entry.get(k) for k in ("at", "step", "message", "verdict", "severity", "fingerprint")
                    if entry.get(k) is not None})
    return out


def _record(r: dict) -> dict:
    sink = (r.get("trace") or [{}])[-1]
    return {"verdict": r.get("verdict"), "title": r.get("title"), "fingerprint": r.get("fingerprint"),
            "severity": (r.get("severity") or {}).get("overall_severity"), "where": f"{sink.get('file')}:{sink.get('line')}",
            "reason": r.get("reason") or "", "blockers": r.get("blockers") or []}

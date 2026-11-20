"""SCAN.5: mark a finding "not affected" (or fixed) as an OpenVEX statement, withdraw one, read a supplier's VEX, and
give the project's VEX with what is fixed, not affected and still open."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Literal

import jsonschema
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Signal, VexStatement
from ..permissions import FromObject, allow
from ..scanning import vex
from ..security import Principal, audit
from ..sphere import learn as sphere_learn

router = APIRouter(prefix="/api")
_SIGNAL = FromObject(Signal, "signal_id")
_STATEMENT = FromObject(VexStatement, "statement_id")
SCHEMA = Path(__file__).resolve().parent.parent / "scanning" / "openvex_json_schema_0.2.0.json"  # openvex/spec, Apache-2.0


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def problems(document: object) -> str:
    """Why this is not a valid OpenVEX document (the official schema), or an empty string."""
    try:
        jsonschema.validate(document, _schema())
    except jsonschema.ValidationError as exc:
        where = "/".join(str(part) for part in exc.absolute_path) or "the document"
        return f"{where}: {exc.message}"[:300]
    return ""


class MarkIn(BaseModel):
    status: Literal["not_affected", "fixed"] = "not_affected"
    justification: str = ""
    reason: str = Field(min_length=1, max_length=2000)
    until: date | None = None
    vulnerability: str = ""
    package: str = ""  # a package URL, for one weakness in an image


@router.post("/signals/{signal_id}/vex")
def mark(signal_id: int, body: MarkIn, user: Principal = Depends(allow("triage", _SIGNAL)), db: Session = Depends(get_db)):
    signal = db.get(Signal, signal_id)
    if signal is None or signal.closed_at is not None:
        raise HTTPException(404, "No open finding with that id.")
    until = datetime.combine(body.until, time(23, 59, 59)) if body.until else None
    try:
        statement = vex.mark(db, signal, author=user.name, status=body.status, justification=body.justification,
                             reason=body.reason, expires_at=until, vulnerability=body.vulnerability, package=body.package)
    except vex.VexError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "marked a finding with a VEX statement", f"signal {signal_id}",
          f"{statement.status}: {statement.justification} {statement.impact_statement}")
    if statement.status == "not_affected" and sphere_learn.is_approver(db, user):  # KS.1: an accepted exception
        sphere_learn.note(db, org_id=signal.org_id, project_id=signal.project_id, kind="not_affected",
                          ref=f"vex:{statement.id}", actor=user.name, payload={"statement_id": statement.id})
    db.commit()
    return _out(statement, utcnow())


@router.post("/vex/{statement_id}/withdraw")
def withdraw(statement_id: int, user: Principal = Depends(allow("triage", _STATEMENT)), db: Session = Depends(get_db)):
    statement = db.get(VexStatement, statement_id)
    if statement is None or statement.withdrawn_at is not None:
        raise HTTPException(404, "No statement in force with that id.")
    vex.withdraw(db, statement, by=user.name)
    audit(db, user, "withdrew a VEX statement", f"statement {statement_id}")
    sphere_learn.note_reversal(db, org_id=statement.org_id, project_id=statement.project_id,
                               taught=f"vex:{statement.id}", actor=user.name)
    db.commit()
    return _out(statement, utcnow())


class SupplierIn(BaseModel):
    supplier: str = Field(min_length=1, max_length=120)
    document: dict
    image: str = ""  # the image it is about; empty for every image of the project
    source_url: str = ""


@router.post("/vex/supplier")
def supplier(body: SupplierIn, user: Principal = Depends(allow("triage")), db: Session = Depends(get_db)):
    """A supplier's published OpenVEX document, checked against the OpenVEX schema, kept with the supplier named."""
    problem = problems(body.document)
    if problem:
        raise HTTPException(422, f"This is not a valid OpenVEX document: {problem}")
    try:
        kept = vex.read_supplier(db, org_id=user.org_id, project_id=user.project.id, supplier=body.supplier,
                                 document=body.document, image=body.image, source_url=body.source_url)
    except vex.VexError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "read a supplier's VEX", body.supplier, f"{len(kept)} statement(s)")
    db.commit()
    return {"kept": [_out(row, utcnow()) for row in kept]}


@router.get("/vex")
def project_vex(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    """The project's VEX in force, every statement ever made with its state, and the findings fixed, not affected and open."""
    now = utcnow()
    rows = db.scalars(select(VexStatement).where(VexStatement.project_id == user.project.id).order_by(VexStatement.id.desc()))
    findings = db.scalars(select(Signal).where(Signal.project_id == user.project.id, Signal.fingerprint != "")).all()
    counts = {"fixed": sum(row.closed_at is not None for row in findings),
              "not_affected": sum(row.closed_at is None and bool(row.vex_status) for row in findings),
              "open": sum(row.closed_at is None and not row.vex_status for row in findings)}
    return {"document": vex.document(db, user.project.id, f"project {user.project.slug}"),
            "statements": [_out(row, now) for row in rows], "counts": counts}


@router.get("/vex.json")
def project_vex_document(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    """The project's VEX as an OpenVEX document, for scanners and auditors."""
    return vex.document(db, user.project.id, f"project {user.project.slug}")


def _out(row: VexStatement, now: datetime) -> dict:
    state = ("withdrawn" if row.withdrawn_at else "expired" if row.expires_at and row.expires_at <= now else "in force")
    return {"id": row.id, "vulnerability": row.vulnerability, "product": row.product, "subcomponent": row.subcomponent,
            "image": row.image, "status": row.status, "justification": row.justification, "reason": row.impact_statement,
            "author": row.author, "supplier": row.supplier, "source_url": row.source_url,
            "created_at": row.created_at.isoformat(), "until": row.expires_at.isoformat() if row.expires_at else None,
            "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None, "withdrawn_by": row.withdrawn_by,
            "signal_id": row.signal_id, "state": state, "words": vex.words(row)}

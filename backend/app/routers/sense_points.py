"""Sense points: run the analysis on a stored clone, then accept, reject or place them by hand (P6.3).

Anyone who may see the project reads them; the roles that may edit the model change them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Clone, SensePoint
from ..permissions import FromObject, allow
from ..security import Principal, audit
from ..services import sense_point_store as store

router = APIRouter(prefix="/api")
_CLONE = FromObject(Clone, "clone_id")
_POINT = FromObject(SensePoint, "point_id")


class Decision(BaseModel):
    status: str = Field(min_length=1, max_length=20)


class Placement(BaseModel):
    node: str = Field(min_length=1, max_length=80)
    mode: str = Field(default="", max_length=40)
    reason: str = Field(default="", max_length=2000)


def _answer(found: dict | None) -> dict:
    if found is None:
        raise HTTPException(404, "No clone with that id.")
    return found


def _refuse(error: store.Refused) -> HTTPException:
    return HTTPException(error.status, str(error))


@router.get("/clones/{clone_id}/sense-points")
def read(clone_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _CLONE))):
    return _answer(store.overview(db, user.org_id, clone_id))


@router.post("/clones/{clone_id}/sense-points/analyse")
def analyse(clone_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model", _CLONE))):
    found = _answer(store.run_analysis(db, user.org_id, clone_id, user.name))
    audit(db, user, "sense_points.analyse", f"clone {clone_id}", f"{found['counts']['suggested']} suggested, coverage {found['coverage']['share']:.0%}")
    db.commit()
    return found


@router.post("/clones/{clone_id}/sense-points")
def place(clone_id: int, body: Placement, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model", _CLONE))):
    try:
        found = _answer(store.place(db, user.org_id, clone_id, body.node, body.mode, body.reason, user.name))
    except store.Refused as error:
        raise _refuse(error) from None
    audit(db, user, "sense_points.place", f"clone {clone_id} node {body.node}", "placed by hand")
    db.commit()
    return found


@router.patch("/sense-points/{point_id}")
def decide(point_id: int, body: Decision, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model", _POINT))):
    try:
        done = store.decide(db, user.org_id, point_id, body.status, user.name)
    except store.Refused as error:
        raise _refuse(error) from None
    if done is None:
        raise HTTPException(404, "No sense point with that id.")
    row, found = done
    audit(db, user, f"sense_points.{body.status}", f"sense point {point_id}", f"node {row.node_key}")
    db.commit()
    return found


@router.delete("/sense-points/{point_id}")
def remove(point_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model", _POINT))):
    try:
        found = store.remove(db, user.org_id, point_id)
    except store.Refused as error:
        raise _refuse(error) from None
    if found is None:
        raise HTTPException(404, "No sense point with that id.")
    audit(db, user, "sense_points.remove", f"sense point {point_id}", "")
    db.commit()
    return found

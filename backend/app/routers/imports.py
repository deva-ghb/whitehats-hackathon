"""Import an estate into a clone as a change set a person reviews (PG.3).

Starting an import compares what the source holds with the clone and keeps the difference. Nothing
reaches the clone until someone accepts items and applies them, which is a normal clone save.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChangeSet, Clone, Organization
from ..permissions import FromObject, allow
from ..security import Principal, audit
from ..services import connector_status, imports, repo_import
from ..services.clones import blank_document, create_clone, document_problems
from ..services.terraform_state import import_state_with_pending, left_out

router = APIRouter(prefix="/api")
_IMPORT = FromObject(ChangeSet, "import_id")


class NewClone(BaseModel):
    name: str  # the clone's name as it is saved: lower case letters, numbers and hyphens
    environment: str


class StartImport(BaseModel):
    source: Literal["terraform_state", "connections", "repository"]
    clone_id: int | None = None
    new_clone: NewClone | None = None  # start this clone empty, but only once the source has been read
    label: str = Field(default="", max_length=200)
    state: dict | None = None  # the Terraform state file, for terraform_state; it is read once and not kept


class Decide(BaseModel):
    keys: list[str] = Field(min_length=1)
    decision: Literal["accept", "reject", "undecided"]
    type: str | None = None  # the library type chosen for a held item


def _refuse(exc: imports.ImportRefused) -> HTTPException:
    return HTTPException(422, exc.problems)


def _company(db: Session, user: Principal) -> str:
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(401, "This company no longer exists.")
    return org.slug


def _incoming(db: Session, user: Principal, body: StartImport, environment: str) -> tuple[dict, dict[str, int], str]:
    """What the source holds, what it left out, and a label for the change set."""
    if body.source == "terraform_state":
        if body.state is None:
            raise HTTPException(422, "Send the Terraform state file as `state`.")
        try:
            found = import_state_with_pending(body.state, environment=environment)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None
        return found, left_out(body.state), body.label or "Terraform state"
    if body.source == "repository":
        try:
            scanned, label = repo_import.scan_project(db, user.org_id, user.project.id, environment)
        except imports.ImportRefused as exc:
            raise _refuse(exc) from None
        return scanned.incoming(), {**scanned.not_drawn, **dict.fromkeys(scanned.notes, 1)}, body.label or label
    view = connector_status.project_view(db, user.org_id, user.project.id)
    found = {key: view["document"].get(key, []) for key in ("nodes", "groups", "edges")}
    if not any(found.values()):
        raise HTTPException(422, "No connection attached to this project has found anything yet. "
                                 "Select a connection in the Studio and choose Find resources.")
    names = ", ".join(source["name"] for source in view["sources"])
    return found, {}, body.label or f"What {names} found"


def _target(db: Session, user: Principal, body: StartImport) -> tuple[Clone | None, str]:
    """The clone to import into, or None for a new one that is not made yet, and its environment."""
    if (body.clone_id is None) == (body.new_clone is None):
        raise HTTPException(422, "Name either a clone to import into (`clone_id`) or a new one (`new_clone`).")
    if body.new_clone is not None:
        problems = document_problems(blank_document(body.new_clone.name, body.new_clone.environment, user.name, "check"),
                                     _company(db, user))
        if problems:
            raise HTTPException(422, problems)
        return None, body.new_clone.environment
    clone = db.get(Clone, body.clone_id)
    if clone is None or clone.org_id != user.org_id or clone.project_id != user.project.id:
        raise HTTPException(404, "No clone with that id in this project.")
    return clone, clone.environment


def _create(db: Session, user: Principal, body: StartImport) -> Clone:
    made = body.new_clone
    reason = "Started for an import"
    try:
        return create_clone(db, user.org_id, blank_document(made.name, made.environment, user.name, reason),
                            user.name, reason, project_id=user.project.id)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This project already has a clone with that name.") from None


@router.post("/imports")
def start(body: StartImport, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model"))):
    clone, environment = _target(db, user, body)
    found, skipped, label = _incoming(db, user, body, environment)
    if clone is None:
        clone = _create(db, user, body)  # the source read cleanly, so the clone is worth starting
    try:
        made = imports.open_change_set(db, clone, found, source=body.source, label=label, author=user.name,
                                       left_out=skipped)
    except imports.ImportRefused as exc:
        raise _refuse(exc) from None
    if made is None:
        return {"id": None, "changes": 0, "clone_id": clone.id,
                "message": "Nothing has changed since the clone was last brought up to date."}
    audit(db, user, "started an import", clone.slug, f"{label}: {len(made.items)} changes")
    db.commit()
    return {"id": made.id, "changes": len(made.items), "clone_id": clone.id}


@router.get("/imports/sources")
def sources(db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    """Which sources an import can read right now for this project. Makes no call to a cloud or to GitHub."""
    return {"repository": repo_import.readiness(db, user.org_id, user.project.id)}


@router.get("/imports")
def index(clone_id: int | None = None, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    """The imports of this project, newest first."""
    return {"imports": imports.list_change_sets(db, user.org_id, user.project.id, clone_id)}


@router.get("/imports/{import_id}")
def read(import_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _IMPORT))):
    return imports.detail(db, _owned(db, user, import_id))


@router.put("/imports/{import_id}/decisions")
def decide(import_id: int, body: Decide, db: Session = Depends(get_db),
           user: Principal = Depends(allow("edit_model", _IMPORT))):
    found = _owned(db, user, import_id)
    try:
        imports.decide(db, found, body.keys, body.decision, body.type)
    except imports.ImportRefused as exc:
        raise _refuse(exc) from None
    return imports.detail(db, found)


@router.post("/imports/{import_id}/apply")
def apply(import_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model", _IMPORT))):
    found = _owned(db, user, import_id)
    try:
        done = imports.apply(db, found, company=_company(db, user), author=user.name)
    except imports.ImportRefused as exc:
        raise _refuse(exc) from None
    report = done["report"]
    audit(db, user, "applied an import", found.label,
          f"{report['added']} added, {report['updated']} updated, {report['removed']} removed")
    db.commit()
    return done


@router.post("/imports/{import_id}/discard")
def discard(import_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model", _IMPORT))):
    found = _owned(db, user, import_id)
    try:
        imports.discard(db, found)
    except imports.ImportRefused as exc:
        raise _refuse(exc) from None
    audit(db, user, "discarded an import", found.label)
    db.commit()
    return {"id": found.id, "status": found.status}


def _owned(db: Session, user: Principal, import_id: int) -> ChangeSet:
    found = db.get(ChangeSet, import_id)
    if found is None or found.org_id != user.org_id:
        raise HTTPException(404, "No import with that id.")
    return found

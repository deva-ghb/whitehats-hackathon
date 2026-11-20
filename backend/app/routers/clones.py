"""Clone documents for the signed-in company.

Import refuses a secret value, a vault path that climbs out of the company, and
a vault reference that names a different company.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Clone, Inventory, Organization
from ..permissions import FromObject, allow
from ..scanning import inventory
from ..security import Principal
from ..services import map_alerts
from ..services.clones import create_clone, document_problems, list_clones, list_versions, read_clone, save_version
from ..services.recommendations import for_project as recommendations_for
from ..services.weaknesses import for_clone

router = APIRouter(prefix="/api")
_CLONE = FromObject(Clone, "clone_id")


class SaveClone(BaseModel):
    """The author is never sent: the server records the signed-in person, so a version cannot be signed by someone else."""

    reason: str = Field(min_length=1, max_length=2000)
    document: dict


def _company(db: Session, user: Principal) -> str:
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(401, "This company no longer exists.")
    return org.slug


HAND_EDITING_OFF = ("The map is drawn from your connections, so it cannot be drawn or changed by hand in this version. "
                    "Connect a source in the Studio and the map follows it.")


def _drawn_by_hand() -> None:
    """STUDIO.7: refuse a document a person made, while the installation draws maps from the connections only."""
    if not settings.studio_hand_editing:
        raise HTTPException(409, HAND_EDITING_OFF)


def _accept(document: dict, company: str) -> None:
    errors = document_problems(document, company)
    if errors:
        raise HTTPException(422, errors)


def _store(db: Session, user: Principal, document: dict, author: str, reason: str) -> dict:
    try:
        clone = create_clone(db, user.org_id, document, author, reason, project_id=user.project.id)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This company already has a clone with that name.") from None
    return {"id": clone.id, "version": 1}


@router.post("/clones")
def create(body: SaveClone, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model"))):
    _drawn_by_hand()
    _accept(body.document, _company(db, user))
    return _store(db, user, body.document, user.name, body.reason)


@router.post("/clones/import")
def import_document(
    document: dict = Body(...), db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model")),
):
    """Import a clone document. The reason comes from its version block; the author is the person importing it."""
    _drawn_by_hand()
    _accept(document, _company(db, user))
    version = document.get("version") or {}
    return _store(db, user, document, user.name, version["reason"])


@router.get("/clones")
def index(db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    """The clones of one project (the `project` query parameter, or Main)."""
    return {"clones": list_clones(db, user.org_id, user.project.id)}


@router.get("/clones/{clone_id}")
def read(clone_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _CLONE))):
    document = read_clone(db, user.org_id, clone_id)
    if document is None:
        raise HTTPException(404, "No clone with that id.")
    return {"id": clone_id, "document": document}


@router.get("/clones/{clone_id}/weaknesses")
def weaknesses(clone_id: int, version: int | None = None, db: Session = Depends(get_db),
               user: Principal = Depends(allow("see_project", _CLONE))):
    """SC.1: each service node's known package weaknesses, ranked by reach, for one version of the clone (the latest)."""
    document = read_clone(db, user.org_id, clone_id, version)
    if document is None:
        raise HTTPException(404, "No clone or version with that id.")
    return {"id": clone_id, "version": document["version"]["number"], **for_clone(db, document)}


@router.get("/recommendations")
def recommendations(db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    """REC.1: what the customer could switch on so WhiteHats sees more of this project's AWS accounts."""
    assert user.project is not None
    return {"items": recommendations_for(db, user.org_id, user.project.id)}


@router.get("/clones/{clone_id}/alerts")
def alerts(clone_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _CLONE))):
    """Open cloud alerts on the buildings they concern, for the latest version of the clone. The Studio asks again
    every few seconds while it is open, so an alert shows on its building as soon as WhiteHats raises it."""
    document = read_clone(db, user.org_id, clone_id, None)
    row = db.get(Clone, clone_id)
    if document is None or row is None:
        raise HTTPException(404, "No clone with that id.")
    return {"id": clone_id, **map_alerts.for_clone(db, row.project_id, document)}


@router.get("/clones/{clone_id}/nodes/{node_id}/inventory")
def node_inventory(clone_id: int, node_id: str, version: int | None = None, format: str = "parts",
                   db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _CLONE))):
    """SCAN.2: every part of one service, direct and hidden, for one version of the clone (the latest).

    Only what is stored is read: making an inventory pulls an image, which the image worker does within ten minutes
    of a service naming it. `format=cyclonedx` gives the stored CycloneDX inventory of the service's image."""
    document = read_clone(db, user.org_id, clone_id, version)
    if document is None:
        raise HTTPException(404, "No clone or version with that id.")
    node = next((item for item in document.get("nodes", []) if item.get("id") == node_id), None)
    if node is None:
        raise HTTPException(404, "No node with that id in this version.")
    found = inventory.for_node(db, node, make=None)
    if format == "cyclonedx":
        row = db.get(Inventory, found["image"]) if found["image"] else None
        if row is None or not row.made_at:
            raise HTTPException(404, "This service's image has no inventory yet.")
        return row.document
    return {"id": clone_id, "version": document["version"]["number"], "node": node_id, **found}


@router.get("/clones/{clone_id}/versions")
def versions(clone_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _CLONE))):
    found = list_versions(db, user.org_id, clone_id)
    if found is None:
        raise HTTPException(404, "No clone with that id.")
    return {"versions": found}


@router.get("/clones/{clone_id}/versions/{number}")
def read_version(clone_id: int, number: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _CLONE))):
    if list_versions(db, user.org_id, clone_id) is None:
        raise HTTPException(404, "No clone with that id.")
    document = read_clone(db, user.org_id, clone_id, number)
    if document is None:
        raise HTTPException(404, "No version with that number.")
    return document


@router.post("/clones/{clone_id}/versions")
def save(
    clone_id: int, body: SaveClone, db: Session = Depends(get_db), user: Principal = Depends(allow("edit_model", _CLONE)),
):
    _drawn_by_hand()
    _accept(body.document, _company(db, user))
    version = save_version(db, user.org_id, clone_id, body.document, user.name, body.reason)
    if version is None:
        raise HTTPException(404, "No clone with that id.")
    return {"id": clone_id, "version": version.number}


@router.get("/clones/{clone_id}/export")
def export(clone_id: int, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project", _CLONE))):
    """The clone document itself, suitable to store or import again."""
    document = read_clone(db, user.org_id, clone_id)
    if document is None:
        raise HTTPException(404, "No clone with that id.")
    return document

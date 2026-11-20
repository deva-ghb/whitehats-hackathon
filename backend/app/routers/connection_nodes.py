"""Connections as nodes on the canvas (STUDIO.4): bring what a connection found into its node, and list what it can be narrowed to.

Setting a connection up, checking it and finding resources are the connector routes (admin.py); this module is the part that
touches a clone. Placing a connection and bringing in what it found are the connection drawing, not a person (STUDIO.7), so
either the right to edit the model or the right to manage connections allows them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors import routing
from ..db import get_db
from ..models import Clone, Connector, Organization
from ..permissions import allow, is_allowed, roles_in
from ..security import Principal, audit
from ..services import connection_nodes, imports

router = APIRouter(prefix="/api")


def _connector(db: Session, user: Principal, key: str) -> Connector:
    found = db.scalar(select(Connector).where(Connector.org_id == user.org_id, Connector.key == key))
    if found is None:
        raise HTTPException(404, "No connector with that name.")
    return found


def _may_draw_from_connections(db: Session, user: Principal) -> None:
    roles = roles_in(db, user, user.project)
    if not (is_allowed(roles, "edit_model") or is_allowed(roles, "manage_connections")):
        raise HTTPException(403, "Showing a connection on the map needs an Engineer or Project admin in this project, "
                                 "or an Account owner or Account admin.")


@router.post("/connections/{key}/place")
def place(key: str, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    """Put this connection on the project's map, starting the map when the project has none (STUDIO.7)."""
    _may_draw_from_connections(db, user)
    connector = _connector(db, user, key)
    org = db.get(Organization, user.org_id)
    try:
        done = connection_nodes.place(db, user.project, connector, company=org.slug, author=user.name)
    except imports.ImportRefused as exc:
        raise HTTPException(422, exc.problems) from None
    if done["placed"]:
        audit(db, user, "placed a connection on the map", connector.name, f"clone {done['clone']}, version {done['version']}")
    # Connecting from this project's map feeds this map: a connection that is not attached here would read everything
    # into other projects and leave this one empty. Whoever may attach it, or set it up (an Account owner), may.
    attached = user.project.id in [pid for pid, _ in routing.attachments(db, connector)]
    roles = roles_in(db, user, user.project)
    may_attach = is_allowed(roles, "attach_connections") or is_allowed(roles, "manage_connections")
    if not attached and not routing.is_account_wide(connector) and may_attach:
        routing.attach(db, connector, user.project, {}, user.name)
        audit(db, user, "attached a connection to the project", connector.name, user.project.name)
        attached = True
    db.commit()
    return {**done, "attached": attached}


@router.post("/clones/{clone_id}/connections/{key}/bring-in")
def bring_in(clone_id: int, key: str, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    """Save what the connection found, as this project's scope lets it, into the clone inside the connection's node."""
    _may_draw_from_connections(db, user)
    clone = db.get(Clone, clone_id)
    if clone is None or clone.org_id != user.org_id or clone.project_id != user.project.id:
        raise HTTPException(404, "No clone with that id in this project.")
    connector = _connector(db, user, key)
    org = db.get(Organization, user.org_id)
    try:
        done = connection_nodes.bring_in(db, clone, connector, company=org.slug, author=user.name)
    except imports.ImportRefused as exc:
        raise HTTPException(422, exc.problems) from None
    if done["change_set"] is not None:
        audit(db, user, "brought a connection's findings into a clone", clone.slug,
              f"{connector.name}: {done['added']} added, {done['updated']} updated, {done['removed']} removed")
    db.commit()
    return done


@router.get("/connections/{key}/choices")
def choices(key: str, db: Session = Depends(get_db), user: Principal = Depends(allow("see_project"))):
    """The regions and repositories this connection has found, and what this project is narrowed to."""
    return connection_nodes.choices(db, _connector(db, user, key), user.project)

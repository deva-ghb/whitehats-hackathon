"""Connections attached to projects, and data sent only where a project's scope matches (checklist AP.3).

scope.py decides; this module reads and writes the attachments around it.
"""

from __future__ import annotations

from sqlalchemy import DateTime, bindparam, func, select, text
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Connector, Project, ProjectConnection, Signal
from ..projects import MAIN_SLUG, main_project_id
from .scope import ACCOUNT_WIDE_CATEGORIES, SCOPE_KEYS_BY_CATEGORY, Resource, check_scope, matching

# Categories whose connections are attached to projects one by one.
_ATTACHABLE = tuple(SCOPE_KEYS_BY_CATEGORY)
# Typed so SQLAlchemy writes the date itself: sqlite3's own date adapter is deprecated in Python 3.12.
_NOW = bindparam("now", type_=DateTime())


def is_account_wide(connector: Connector) -> bool:
    return connector.category in ACCOUNT_WIDE_CATEGORIES


def attach(db: Session, connector: Connector, project: Project, scope: object, by: str) -> ProjectConnection:
    """Attach, or change the scope of an existing attachment. ValueError when the scope is not valid."""
    if is_account_wide(connector):
        raise ValueError(f"{connector.name} is used by every project of the account, so it is not attached.")
    checked = check_scope(connector.category, scope)
    row = db.scalar(select(ProjectConnection).where(
        ProjectConnection.project_id == project.id, ProjectConnection.connector_id == connector.id))
    if row is None:
        row = ProjectConnection(org_id=connector.org_id, project_id=project.id, connector_id=connector.id)
        db.add(row)
    row.scope, row.attached_by, row.attached_at = checked, by, utcnow()
    db.flush()
    return row


def detach(db: Session, connector: Connector, project: Project) -> bool:
    """Stop new data reaching this project. What it already holds stays."""
    row = db.scalar(select(ProjectConnection).where(
        ProjectConnection.project_id == project.id, ProjectConnection.connector_id == connector.id))
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def attachments(db: Session, connector: Connector) -> list[tuple[int, dict]]:
    """(project id, scope) for every live project the connection reaches."""
    if is_account_wide(connector):
        return [(project_id, {}) for project_id in db.scalars(select(Project.id).where(
            Project.org_id == connector.org_id, Project.archived.is_(False)))]
    rows = db.execute(select(ProjectConnection.project_id, ProjectConnection.scope)
                      .join(Project, Project.id == ProjectConnection.project_id)
                      .where(ProjectConnection.connector_id == connector.id, Project.archived.is_(False))).all()
    return [(project_id, scope or {}) for project_id, scope in rows]


def projects_for(db: Session, connector: Connector, resource: Resource) -> list[int]:
    return matching(attachments(db, connector), resource)


def connection_resource(connector: Connector) -> Resource:
    """What a batch of log lines from this connection is about: its cloud account, region and workspace.

    Lines do not name their own resource yet, so a project's scope is matched against the connection.
    """
    config = connector.config or {}
    role = str(config.get("role_arn", ""))
    account = role.split(":")[4] if role.count(":") >= 5 else str(config.get("account", ""))
    identity = role or str(config.get("workspace_id") or config.get("project_id") or f"{connector.key}:{connector.id}")
    return Resource(identity=identity, account=account, region=str(config.get("region", "")),
                    resource_group=str(config.get("resource_group", "")))


def raise_finding(db: Session, connector: Connector, resource: Resource, *, severity: str, title: str,
                  detail: str = "") -> list[Signal]:
    """A finding from a connection, raised once in every project that holds the resource."""
    rows = []
    for project_id in projects_for(db, connector, resource):
        row = Signal(org_id=connector.org_id, project_id=project_id, severity=severity, title=title,
                     detail=detail, entity=resource.identity[:160], resource=resource.identity,
                     source=f"connector:{connector.key}"[:40])
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def account_finding_count(db: Session, org_id: int) -> int:
    """Findings across the account, the same resource and title counted once however many projects hold it."""
    keyed = select(Signal.resource, Signal.title).where(Signal.org_id == org_id, Signal.resource != "").distinct()
    shared = db.scalar(select(func.count()).select_from(keyed.subquery())) or 0
    alone = db.scalar(select(func.count(Signal.id)).where(Signal.org_id == org_id, Signal.resource == "")) or 0
    return shared + alone


def attach_to_main(db: Session, connector: Connector) -> None:
    """A connection the account has not attached anywhere yet goes to Main with everything in scope.

    A one-project account then works as before; a second project chooses its own slice.
    """
    if is_account_wide(connector) or connector.category not in _ATTACHABLE:
        return
    if db.scalar(select(ProjectConnection.id).where(ProjectConnection.connector_id == connector.id)):
        return
    db.add(ProjectConnection(org_id=connector.org_id, project_id=main_project_id(db, connector.org_id),
                             connector_id=connector.id, scope={}, attached_by="WhiteHats"))
    db.flush()


def upgrade_attachments(conn) -> list[str]:
    """Migration: each connection from before projects is attached to Main with everything in scope."""
    categories = ", ".join(f"'{name}'" for name in _ATTACHABLE)
    n = conn.execute(text(
        "INSERT INTO project_connections (org_id, project_id, connector_id, scope, attached_by, attached_at) "
        "SELECT c.org_id, p.id, c.id, '{}', 'WhiteHats', :now FROM connectors c "
        "JOIN projects p ON p.org_id = c.org_id AND p.slug = :slug "
        f"WHERE c.category IN ({categories}) "
        "AND NOT EXISTS (SELECT 1 FROM project_connections pc WHERE pc.connector_id = c.id)"
    ).bindparams(_NOW), {"now": utcnow(), "slug": MAIN_SLUG}).rowcount
    return [f"attached {n} connections to Main"] if n else []


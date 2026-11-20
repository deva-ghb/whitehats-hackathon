"""Projects inside an account.

The organizations table stays the account. A project holds the estate and what
WhiteHats finds there. Callers that do not name a project yet write to Main.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import DateTime, bindparam, event, select, text
from sqlalchemy.orm import Session

from .db import utcnow

if TYPE_CHECKING:
    from .models import Organization, Project, User

MAIN_NAME = "Main"
MAIN_SLUG = "main"
DEFAULT_CRITICALITY = "medium"
CRITICALITIES = ("high", "medium", "low")
ACCOUNT_ROLES = ("account_owner", "account_admin", "security_lead", "auditor")
PROJECT_ROLES = ("project_admin", "approver", "engineer", "viewer")

# Log findings are signals. There is no separate log-findings table.
# Network findings are network_findings. Credential refs travel with the clone.
PROJECT_TABLES = (
    "clones",
    "clone_versions",
    "nodes",
    "edges",
    "groups",
    "access_paths",
    "credential_refs",
    "change_sets",
    "audits",
    "sense_points",
    "signals",
    "incidents",
    "incident_events",
    "remediations",
    "posture_snapshots",
    "agents",
    "network_findings",
    "chat_messages",
    "project_connections",
    "vex_statements",
    "sphere_decisions",
    "sphere_versions",
    "sphere_graph_state",
    "finding_decisions",
)
_PROJECT_TABLES_BY_ORG = tuple(name for name in PROJECT_TABLES if name != "incident_events")

_ACCOUNT_ROLE = {"ceo": "account_owner", "ciso": "security_lead"}
_PROJECT_ROLE = {"ceo": "approver", "analyst": "engineer", "engineer": "engineer"}

_registered = False


def ensure_main_project(db: Session, org: Organization, *, created_by: int | None = None) -> Project:
    """Open Main for this account when it does not have one yet."""
    from .models import Project

    found = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == MAIN_SLUG))
    if found is not None:
        return found
    found = Project(
        org_id=org.id, name=MAIN_NAME, slug=MAIN_SLUG, criticality=DEFAULT_CRITICALITY,
        archived=False, created_by=created_by,
    )
    db.add(found)
    db.flush()
    return found


def main_project_id(db: Session, org_id: int) -> int:
    """The id of this account's Main project, opening it if account creation skipped it."""
    from .models import Organization, Project

    found = db.scalar(select(Project.id).where(Project.org_id == org_id, Project.slug == MAIN_SLUG))
    if found is not None:
        return found
    org = db.get(Organization, org_id)
    if org is None:
        raise LookupError(f"No account {org_id}.")
    return ensure_main_project(db, org).id


def apply_legacy_access(db: Session, user: User, project_ids: list[int] | None = None) -> None:
    """Copy a legacy role onto the account role and project membership.

    CEO becomes account owner and approver in every project. CISO becomes
    security lead. An analyst becomes engineer in every project. An engineer
    becomes engineer only in project_ids, the projects they are added to.
    """
    from .models import Project, ProjectMember

    account_role = _ACCOUNT_ROLE.get(user.role)
    if account_role and not user.account_role:
        user.account_role = account_role
    project_role = _PROJECT_ROLE.get(user.role)
    if project_role is None:
        return
    if user.role == "engineer":
        chosen = list(project_ids or [])
    elif project_ids is None:
        chosen = list(db.scalars(select(Project.id).where(Project.org_id == user.org_id)))
    else:
        chosen = list(project_ids)
    for project_id in chosen:
        exists = db.scalar(select(ProjectMember.id).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user.id))
        if exists is None:
            db.add(ProjectMember(project_id=project_id, user_id=user.id, role=project_role))


def apply_invited_access(db: Session, user: User, account_role: str | None) -> None:
    """Roles for someone joining through an invitation.

    An invitation that makes the account owner also makes them Approver in every
    project, as the CEO mapping does, so the person who runs the account can act in it.
    """
    from .models import Project, ProjectMember

    if account_role:
        user.account_role = account_role
    apply_legacy_access(db, user)
    if account_role != "account_owner":
        return
    db.flush()
    held = set(db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)))
    for project_id in db.scalars(select(Project.id).where(Project.org_id == user.org_id)):
        if project_id not in held:
            db.add(ProjectMember(project_id=project_id, user_id=user.id, role=_PROJECT_ROLE["ceo"]))


def change_legacy_role(db: Session, user: User, new_role: str) -> None:
    """Move a person to a new legacy role and keep the account and project roles in step.

    Only what the old role gave is replaced. An account role or project role
    granted some other way stays. An engineer keeps the projects they were in.
    """
    from .models import ProjectMember

    previous = user.role
    user.role = new_role
    if user.account_role is None or user.account_role == _ACCOUNT_ROLE.get(previous):
        user.account_role = _ACCOUNT_ROLE.get(new_role)
    granted = db.scalars(select(ProjectMember).where(
        ProjectMember.user_id == user.id, ProjectMember.role == _PROJECT_ROLE.get(previous))).all()
    if new_role == "engineer":
        for member in granted:
            member.role = _PROJECT_ROLE["engineer"]
        return
    for member in granted:
        db.delete(member)
    db.flush()  # the session does not autoflush, and the unique rule needs the old rows gone first
    apply_legacy_access(db, user)


def register_project_hooks() -> None:
    """Fill a missing project_id with Main. Account creation opens Main first."""
    global _registered
    if _registered:
        return
    _registered = True
    from .models import (
        AccessPath,
        Agent,
        ChangeSet,
        ChatMessage,
        Clone,
        CloneVersion,
        CredentialRef,
        Edge,
        Group,
        Incident,
        IncidentEvent,
        NetworkFinding,
        Node,
        PostureSnapshot,
        ProjectConnection,
        Remediation,
        SensePoint,
        Signal,
    )

    for model in (
        Clone, CloneVersion, Node, Edge, Group, AccessPath, CredentialRef, ChangeSet, SensePoint,
        Signal, Incident, Remediation, PostureSnapshot, Agent, NetworkFinding, ChatMessage, ProjectConnection,
    ):
        event.listen(model, "before_insert", _assign_project)
    event.listen(IncidentEvent, "before_insert", _assign_event_project)


def upgrade_legacy_accounts(conn) -> list[str]:
    """Give each existing account Main and move pre-project rows into it."""
    done = []
    org_ids = _orgs_without_main(conn)
    if org_ids:
        _insert_main_projects(conn, org_ids)
        done.append(f"gave {len(org_ids)} accounts a Main project")
        mapped = _map_legacy_users(conn, org_ids)
        if mapped:
            done.append(f"mapped {mapped} existing users onto account and project roles")
    placed = _place_rows_in_main(conn)
    if placed:
        done.append(f"placed {placed} existing rows into Main")
    return done


def _assign_project(mapper, connection, target) -> None:
    if target.project_id is not None:
        return
    target.project_id = _main_id(connection, target.org_id)


def _assign_event_project(mapper, connection, target) -> None:
    if target.project_id is not None:
        return
    row = connection.execute(
        text("SELECT project_id FROM incidents WHERE id = :id"), {"id": target.incident_id},
    ).first()
    if row is None or row[0] is None:
        raise RuntimeError("An incident event needs the incident's project.")
    target.project_id = row[0]


# Typed so SQLAlchemy writes the date itself: sqlite3's own date adapter is deprecated in Python 3.12.
CREATED_AT = bindparam("created_at", type_=DateTime())
# Columns added with the account home. Raw inserts that open Main have to name them.
_NEW_PROJECT_COLUMNS = {
    "environments": '["production"]', "two_approver": False, "origin": "empty", "template_key": "",
}


def _main_id(connection, org_id: int) -> int:
    if org_id is None:
        raise RuntimeError("A project-scoped row needs an account.")
    found = connection.execute(
        text("SELECT id FROM projects WHERE org_id = :org_id AND slug = :slug"),
        {"org_id": org_id, "slug": MAIN_SLUG},
    ).first()
    if found is not None:
        return int(found[0])
    inserted = connection.execute(text(
        "INSERT INTO projects (org_id, name, slug, criticality, archived, environments, two_approver, "
        "origin, template_key, created_at) "
        "VALUES (:org_id, :name, :slug, :criticality, :archived, :environments, :two_approver, "
        ":origin, :template_key, :created_at) RETURNING id"
    ).bindparams(CREATED_AT), {
        "org_id": org_id, "name": MAIN_NAME, "slug": MAIN_SLUG,
        "criticality": DEFAULT_CRITICALITY, "archived": False, "created_at": utcnow(),
        **_NEW_PROJECT_COLUMNS,
    })
    return int(inserted.scalar_one())


def _orgs_without_main(conn) -> list[int]:
    rows = conn.execute(text(
        "SELECT id FROM organizations o WHERE NOT EXISTS ("
        "SELECT 1 FROM projects p WHERE p.org_id = o.id AND p.slug = :slug)"
    ), {"slug": MAIN_SLUG}).all()
    return [row[0] for row in rows]


def _insert_main_projects(conn, org_ids: list[int]) -> None:
    now = utcnow()
    statement = text(
        "INSERT INTO projects (org_id, name, slug, criticality, archived, environments, two_approver, "
        "origin, template_key, created_at) "
        "VALUES (:org_id, :name, :slug, :criticality, :archived, :environments, :two_approver, "
        ":origin, :template_key, :created_at)"
    ).bindparams(CREATED_AT)
    for org_id in org_ids:
        conn.execute(statement, {
            "org_id": org_id, "name": MAIN_NAME, "slug": MAIN_SLUG,
            "criticality": DEFAULT_CRITICALITY, "archived": False, "created_at": now,
            **_NEW_PROJECT_COLUMNS,
        })


def _map_legacy_users(conn, org_ids: list[int]) -> int:
    clause, params = _org_params(org_ids)
    conn.execute(text(
        f"UPDATE users SET account_role = 'account_owner' "
        f"WHERE account_role IS NULL AND role = 'ceo' AND org_id IN ({clause})"
    ), params)
    conn.execute(text(
        f"UPDATE users SET account_role = 'security_lead' "
        f"WHERE account_role IS NULL AND role = 'ciso' AND org_id IN ({clause})"
    ), params)
    conn.execute(text(
        "INSERT INTO project_members (project_id, user_id, role) "
        "SELECT p.id, u.id, CASE WHEN u.role = 'ceo' THEN 'approver' ELSE 'engineer' END "
        "FROM users u JOIN projects p ON p.org_id = u.org_id AND p.slug = :slug "
        f"WHERE u.org_id IN ({clause}) AND u.role IN ('ceo', 'analyst', 'engineer') "
        "AND NOT EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id AND m.user_id = u.id)"
    ), {**params, "slug": MAIN_SLUG})
    return int(conn.execute(text(
        f"SELECT COUNT(*) FROM users WHERE org_id IN ({clause}) "
        "AND role IN ('ceo', 'ciso', 'analyst', 'engineer')"
    ), params).scalar() or 0)


def _place_rows_in_main(conn) -> int:
    placed = 0
    for table in _PROJECT_TABLES_BY_ORG:
        result = conn.execute(text(
            f'UPDATE "{table}" SET project_id = ('
            "SELECT p.id FROM projects p "
            f'WHERE p.org_id = "{table}".org_id AND p.slug = :slug) '
            "WHERE project_id IS NULL AND org_id IN (SELECT org_id FROM projects WHERE slug = :slug)"
        ), {"slug": MAIN_SLUG})
        placed += _changed(result)
    result = conn.execute(text(
        "UPDATE incident_events SET project_id = ("
        "SELECT i.project_id FROM incidents i WHERE i.id = incident_events.incident_id) "
        "WHERE project_id IS NULL AND incident_id IN ("
        "SELECT id FROM incidents WHERE project_id IS NOT NULL)"
    ))
    return placed + _changed(result)


def _changed(result) -> int:
    """SQLite sometimes reports -1 when it does not know the count. That is not a change."""
    count = result.rowcount or 0
    return max(0, count)


def _org_params(org_ids: list[int]) -> tuple[str, dict[str, int]]:
    params = {f"org{index}": org_id for index, org_id in enumerate(org_ids)}
    clause = ", ".join(f":org{index}" for index in range(len(org_ids)))
    return clause, params

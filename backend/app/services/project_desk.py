"""Account home and project settings: create a project, list what a person can see, change it.

A project is empty, copied from a library template, or opened from one connection's scope.
The person who creates it becomes its Project admin, so they can invite and archive it.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..connectors import PROTOTYPE_CONNECTORS, routing
from ..models import (
    Connector,
    Incident,
    Organization,
    PostureSnapshot,
    Project,
    ProjectConnection,
    ProjectMember,
    Signal,
    User,
)
from ..permissions import account_roles, held, is_allowed, roles_in
from ..projects import ACCOUNT_ROLES, CRITICALITIES, MAIN_SLUG, PROJECT_ROLES
from ..security import Principal, audit
from ..whitehats import BUILTIN_KEYS, enable
from . import posture
from .clones import create_clone

_TEMPLATES = {"health": "template-a.clone.json", "fintech": "template-b.clone.json"}


def account_overview(db: Session, principal: Principal) -> dict:
    """Scores for the projects this person can see, weighted into one account score."""
    return posture.roll_up(db, principal.org_id, _visible(db, principal))


def visible_projects(db: Session, principal: Principal) -> list[Project]:
    """The live projects this person may open: every one for the account roles, otherwise their own."""
    return _visible(db, principal)


def project_roles(db: Session, principal: Principal) -> dict[int, list[dict]]:
    """Each person's project roles, in the projects this reader can open, so the people list says who can do what."""
    visible = {project.id: project.name for project in _visible(db, principal)}
    rows = db.execute(select(ProjectMember.user_id, ProjectMember.project_id, ProjectMember.role)
                      .where(ProjectMember.project_id.in_(list(visible)))).all()
    out: dict[int, list[dict]] = {}
    for user_id, project_id, role in sorted(rows, key=lambda row: visible[row[1]]):
        out.setdefault(user_id, []).append({"project": visible[project_id], "role": role})
    return out


def list_visible(db: Session, principal: Principal) -> list[dict]:
    """Projects this person may open, with the figures the account home shows."""
    rows = _visible(db, principal)
    live = _live_main_score(db, principal.org_id, rows)
    return [summarise(db, project, live_main=live) for project in rows]


def create_project(
    db: Session, principal: Principal, *, name: str, kind: str, criticality: str,
    template: str | None, connector_id: int | None, scope: dict | None,
) -> dict:
    """Open a project and, when asked, a clone or a connection attachment."""
    if criticality not in CRITICALITIES:
        raise ValueError("Criticality is high, medium or low.")
    document = _template(template) if kind == "template" else None
    connector = _connector(db, principal.org_id, connector_id) if kind == "connection" else None
    if kind not in ("empty", "template", "connection"):
        raise ValueError("Start from an empty project, a template, or a connection.")
    if kind == "template" and document is None:
        raise ValueError("Choose the health or fintech template.")
    if kind == "connection" and connector is None:
        raise ValueError("Choose a connection.")
    project = Project(
        org_id=principal.org_id, name=name.strip(), slug=_free_slug(db, principal.org_id, name),
        criticality=criticality, archived=False, created_by=principal.id, origin=kind,
        template_key=template or "", environments=["production"], two_approver=False,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=principal.id, role="project_admin"))
    db.flush()
    # The same WhiteHats the company's first project starts with, so what a connection brings in is read from day one
    # (without them, an AWS change in this project's account reaches no one).
    org = db.get(Organization, principal.org_id)
    for key in (org.contracted_agents if org else None) or BUILTIN_KEYS:
        enable(db, principal.org_id, key, project_id=project.id)
    if document is not None:
        create_clone(db, principal.org_id, document, principal.name, f"Opened from the {template} template",
                     project_id=project.id)
        project = db.get(Project, project.id)
    elif connector is not None:
        routing.attach(db, connector, project, scope or {}, principal.name)
    principal.project = project
    audit(db, principal, "created a project", project.name, kind)
    db.commit()
    db.refresh(project)
    return summarise(db, project, live_main=None)


def update_project(db: Session, principal: Principal, project: Project, fields: dict) -> dict:
    """Rename, set criticality or environments, or archive. The caller already holds manage_projects."""
    if fields.get("name"):
        project.name = fields["name"].strip()
    if fields.get("criticality"):
        if fields["criticality"] not in CRITICALITIES:
            raise ValueError("Criticality is high, medium or low.")
        project.criticality = fields["criticality"]
    if fields.get("environments") is not None:
        project.environments = _environment_names(fields["environments"])
    if fields.get("archived") is not None:
        project.archived = bool(fields["archived"])
    principal.project = project
    audit(db, principal, "updated a project", project.name)
    db.commit()
    db.refresh(project)
    return summarise(db, project, live_main=None)


def set_two_approver(db: Session, principal: Principal, project: Project, enabled: bool) -> dict:
    project.two_approver = enabled
    principal.project = project
    audit(db, principal, "set two-approver review", project.name, "on" if enabled else "off")
    db.commit()
    return describe(db, principal, project)


def set_weekly_audits(db: Session, principal: Principal, project: Project, enabled: bool) -> dict:
    """AUD.6: the weekly full audit, on or off; the change is in the audit trail with who made it."""
    project.weekly_audits = enabled
    principal.project = project
    audit(db, principal, "set weekly audits", project.name, "on" if enabled else "off")
    db.commit()
    return describe(db, principal, project)


def archive_project(db: Session, principal: Principal, project: Project) -> dict:
    if project.slug == MAIN_SLUG:
        # The home project is where every new connection, WhiteHat and screen lands until a project is named.
        raise ValueError(f"{project.name} cannot be archived: it is this account's home project, where new "
                         "connections and WhiteHats start. Archive another project instead.")
    project.archived = True
    principal.project = project
    audit(db, principal, "archived a project", project.name)
    db.commit()
    return {"slug": project.slug, "archived": True}


OWN_ROLE = "Nobody changes their own role. Ask another Project admin or an Account admin."


def add_member(db: Session, principal: Principal, project: Project, email: str, role: str, reason: str) -> dict:
    """Give someone already in the account a role in this project, or change the one they have, and say why."""
    if role not in PROJECT_ROLES:
        raise ValueError("Choose a project role.")
    person = _account_person(db, project, email)
    if person.id == principal.id:
        raise PermissionError(OWN_ROLE)
    row = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == project.id, ProjectMember.user_id == person.id))
    previous = row.role if row is not None else None
    if row is None:
        db.add(ProjectMember(project_id=project.id, user_id=person.id, role=role))
    else:
        row.role = role
    principal.project = project
    change = f"{previous} to {role}" if previous else role
    audit(db, principal, "changed a project role", person.email, f"{change}. Why: {_why(reason)}")
    db.commit()
    return describe(db, principal, project)


def remove_member(db: Session, principal: Principal, project: Project, email: str, reason: str) -> dict:
    """Take someone out of this project, and say why. Their account and other projects stay."""
    person = _account_person(db, project, email, active_only=False)
    if person.id == principal.id:
        raise PermissionError(OWN_ROLE)
    row = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == project.id, ProjectMember.user_id == person.id))
    if row is None:
        raise LookupError(f"{person.email} is not in {project.name}.")
    db.delete(row)
    principal.project = project
    audit(db, principal, "removed from a project", person.email, f"was {row.role}. Why: {_why(reason)}")
    db.commit()
    return describe(db, principal, project)


def _account_person(db: Session, project: Project, email: str, *, active_only: bool = True) -> User:
    person = db.scalar(select(User).where(User.org_id == project.org_id, User.email == email.strip().lower()))
    if person is None or (active_only and not person.active):
        raise ValueError("Invite them to the account first, from Account settings.")
    return person


def _why(reason: str) -> str:
    text = reason.strip()
    if len(text) < REASON_MIN:
        raise ValueError("Say why in a few words. It goes on the audit trail.")
    return text


# A reason this short still says something ("new hire", "left team").
REASON_MIN = 5


def describe(db: Session, principal: Principal, project: Project) -> dict:
    """Project settings: the fields, the people, the attached connections, and what this person may change."""
    perms = set(held(db, principal, project))
    members = db.execute(
        select(User.name, User.email, ProjectMember.role)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.name)
    ).all()
    connections = db.execute(
        select(Connector.id, Connector.name, Connector.category, Connector.status, ProjectConnection.scope)
        .join(ProjectConnection, ProjectConnection.connector_id == Connector.id)
        # A coming-soon connection feeds nothing, so it is not shown as attached (PT.1).
        .where(ProjectConnection.project_id == project.id, Connector.key.in_(PROTOTYPE_CONNECTORS))
        .order_by(Connector.name)
    ).all()
    return {
        "slug": project.slug, "name": project.name, "criticality": project.criticality,
        "environments": list(project.environments or []), "two_approver": bool(project.two_approver),
        "weekly_audits": bool(project.weekly_audits),
        "origin": project.origin, "archived": bool(project.archived),
        "members": [{"name": name, "email": email, "role": role} for name, email, role in members],
        "connections": [
            {"id": cid, "name": name, "category": category, "status": status, "scope": scope or {}}
            for cid, name, category, status, scope in connections
        ],
        "can": {
            "manage_projects": "manage_projects" in perms,
            "archive_project": "archive_project" in perms,
            "manage_members": "manage_members" in perms,
            "attach_connections": "attach_connections" in perms,
            "set_policies": "set_policies" in perms,
        },
    }


def summarise(db: Session, project: Project, *, live_main: int | None) -> dict:
    snap = db.scalar(select(PostureSnapshot).where(PostureSnapshot.project_id == project.id)
                     .order_by(PostureSnapshot.day.desc()).limit(1))
    if snap is not None:
        score, measured = snap.score, True
    elif project.slug == MAIN_SLUG and live_main is not None:
        score, measured = live_main, True
    else:
        score, measured = None, False
    return {
        "slug": project.slug, "name": project.name, "criticality": project.criticality,
        "archived": bool(project.archived), "origin": project.origin,
        "score": score, "score_measured": measured,
        "open_findings": posture.open_findings(db, project.id),
        "connection_health": _health(db, project.id),
        "last_activity": _last_activity(db, project.id),
    }


def list_archived(db: Session, principal: Principal) -> list[dict]:
    """Archived projects, for the account roles that may still read them (R-047). Project roles see none."""
    if principal.support or not any(is_allowed({role}, "see_project") for role in account_roles(db, principal)):
        return []
    rows = db.scalars(select(Project).where(Project.org_id == principal.org_id, Project.archived.is_(True))
                      .order_by(Project.name)).all()
    return [summarise(db, project, live_main=None) for project in rows]


def _visible(db: Session, principal: Principal) -> list[Project]:
    rows = list(db.scalars(select(Project).where(
        Project.org_id == principal.org_id, Project.archived.is_(False),
    ).order_by(Project.name)))
    roles = roles_in(db, principal, None)
    sees_all = any(role in ACCOUNT_ROLES and is_allowed({role}, "see_project") for role in roles)
    if principal.support and principal.support_project_id is not None:
        return [project for project in rows if project.id == principal.support_project_id]
    if sees_all:
        return rows
    mine = set(db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == principal.id)))
    return [project for project in rows if project.id in mine]


def _live_main_score(db: Session, org_id: int, rows: list[Project]) -> int | None:
    main = next((project for project in rows if project.slug == MAIN_SLUG), None)
    if main is None:
        return None
    has_snapshot = db.scalar(select(PostureSnapshot.id).where(PostureSnapshot.project_id == main.id).limit(1))
    if has_snapshot is not None:
        return None
    return posture.compute(db, org_id)["score"]


def _health(db: Session, project_id: int) -> str:
    statuses = db.scalars(select(Connector.status).join(
        ProjectConnection, ProjectConnection.connector_id == Connector.id,
    ).where(ProjectConnection.project_id == project_id)).all()
    if not statuses:
        return "none"
    if any(status == "error" for status in statuses):
        return "error"
    return "ok"


def _last_activity(db: Session, project_id: int) -> str | None:
    times = [
        db.scalar(select(func.max(Signal.created_at)).where(Signal.project_id == project_id)),
        db.scalar(select(func.max(Incident.created_at)).where(Incident.project_id == project_id)),
    ]
    found = [moment for moment in times if moment is not None]
    return max(found).isoformat() if found else None


def _template(key: str | None) -> dict | None:
    name = _TEMPLATES.get(key or "")
    if name is None:
        return None
    import library
    path = Path(library.__file__).resolve().parents[1] / "examples" / name
    if not path.is_file():
        raise ValueError("That template is not installed.")
    return json.loads(path.read_text())


def _connector(db: Session, org_id: int, connector_id: int | None) -> Connector | None:
    if connector_id is None:
        return None
    row = db.get(Connector, connector_id)
    if row is None or row.org_id != org_id:
        raise ValueError("That connection is not in this account.")
    return row


def _environment_names(raw: object) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ValueError("Environments must be a list of names.")
    return [item.strip() for item in raw]


def _free_slug(db: Session, org_id: int, name: str) -> str:
    base = _slug(name)
    if base == "audit":
        base = "audit-team"
    slug = base
    number = 2
    while db.scalar(select(Project.id).where(Project.org_id == org_id, Project.slug == slug)):
        suffix = f"-{number}"
        slug = f"{base[:63 - len(suffix)]}{suffix}"
        number += 1
        if number > 50:
            raise ValueError("Choose a different name.")
    return slug


def _slug(name: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in raw:
        raw = raw.replace("--", "-")
    return raw[:60].strip("-") or "project"

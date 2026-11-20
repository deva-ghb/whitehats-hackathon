"""Who may do what: the role matrix from spec section 0A, as data, and the one check every route uses.

Account roles apply in every project of the account. Project roles apply only in the
project they are granted in. Access is denied unless a role grants it. Routes say what
they need with `allow(permission, project_from=...)`; nothing else decides access.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Project, ProjectMember
from .projects import ACCOUNT_ROLES, MAIN_SLUG, PROJECT_ROLES
from .security import Principal, current_user

ACCOUNT, PROJECT, PLATFORM = "account", "project", "platform"


@dataclass(frozen=True)
class Permission:
    scope: str  # account: about the whole account; project: inside one project; platform: WhiteHats staff
    label: str  # what it lets someone do, in the words of the spec


# Rows of spec 0A first. The rows marked "gap" are not in the spec table; each says why it exists.
PERMISSIONS: dict[str, Permission] = {
    "see_project": Permission(PROJECT, "See the project and its findings"),
    "edit_model": Permission(PROJECT, "Edit the canvas, sense points and WhiteHat settings"),
    "triage": Permission(PROJECT, "Triage: change status, add notes, open incidents"),
    "approve": Permission(PROJECT, "Approve fixes and pre-approved responses"),
    "set_policies": Permission(PROJECT, "Set response policies"),
    "attach_connections": Permission(PROJECT, "Attach or detach connections and their scope"),
    "manage_members": Permission(PROJECT, "Manage project members and their roles"),
    "archive_project": Permission(PROJECT, "Archive this project"),
    "read_project_audit": Permission(PROJECT, "Read this project's audit trail"),
    "manage_projects": Permission(ACCOUNT, "Create, rename, archive and delete projects"),
    "manage_users": Permission(ACCOUNT, "Manage users and account roles"),
    "manage_connections": Permission(ACCOUNT, "Manage connections, collectors and the Runner"),
    "manage_ai": Permission(ACCOUNT, "Set up AI models"),
    "manage_billing": Permission(ACCOUNT, "Change the plan and billing"),
    "read_audit": Permission(ACCOUNT, "Read the account's audit trail"),
    # gap: shared account pages (people, models, connections, setup status) need some role in the account.
    "use_account": Permission(ACCOUNT, "See the account's shared setup"),
    # gap: raw logs, collector feeds and network flows come from account collectors and are not tied to one
    # project yet (AP.3 routes them), so only the roles that see every project may read them.
    "read_account_data": Permission(ACCOUNT, "Read raw logs, collector feeds and network flows"),
    # gap: settings that hold for the whole account and are not credentials: working hours and the Fix
    # Factory practice repository. A project role must not change what every project uses.
    "manage_account_setup": Permission(ACCOUNT, "Change the account's own setup: working hours, the practice repository"),
    # gap: the customer's go-live sign-off is the account owner's or admin's, like the contract.
    "sign_off": Permission(ACCOUNT, "Sign off go-live and open support access"),
    # gap: anyone signed in may read who they are, including WhiteHats staff and support sessions.
    "be_signed_in": Permission(ACCOUNT, "Use their own session"),
    "operate_platform": Permission(PLATFORM, "Run the WhiteHats platform"),
}

_ALL_PROJECT_READS = {"see_project", "use_account", "be_signed_in"}
# The account roles whose "see the project" covers every project of the account.
_EVERY_PROJECT_READS = _ALL_PROJECT_READS | {"read_account_data"}

GRANTS: dict[str, frozenset[str]] = {
    "account_owner": frozenset(_EVERY_PROJECT_READS | {
        "manage_projects", "manage_users", "manage_connections", "manage_ai", "manage_billing", "read_audit",
        "read_project_audit", "sign_off", "manage_account_setup",
    }),
    "account_admin": frozenset(_EVERY_PROJECT_READS | {
        "attach_connections", "manage_members", "manage_projects", "manage_users", "manage_connections",
        "manage_ai", "read_audit", "read_project_audit", "sign_off", "manage_account_setup",
    }),
    "security_lead": frozenset(_EVERY_PROJECT_READS | {
        "triage", "approve", "set_policies", "read_audit", "read_project_audit",
    }),
    "auditor": frozenset(_EVERY_PROJECT_READS | {"read_audit", "read_project_audit"}),
    "project_admin": frozenset(_ALL_PROJECT_READS | {
        "edit_model", "triage", "set_policies", "attach_connections", "manage_members", "archive_project",
        "read_project_audit",
    }),
    "approver": frozenset(_ALL_PROJECT_READS | {"triage", "approve"}),
    "engineer": frozenset(_ALL_PROJECT_READS | {"edit_model", "triage"}),
    "viewer": frozenset(_ALL_PROJECT_READS),
    # A support session is read-only, whatever the operator's own role.
    "support": frozenset(_ALL_PROJECT_READS),
    # A support session the account limited to one project, anywhere else: only its own session.
    "support_elsewhere": frozenset({"be_signed_in"}),
    "operator": frozenset({"operate_platform", "be_signed_in"}),
}

ROLE_LABELS = {
    "account_owner": "Account owner", "account_admin": "Account admin", "security_lead": "Security lead",
    "auditor": "Auditor", "project_admin": "Project admin", "approver": "Approver", "engineer": "Engineer",
    "viewer": "Viewer",
}

assert set(GRANTS) == set(ACCOUNT_ROLES) | set(PROJECT_ROLES) | {"support", "support_elsewhere", "operator"}
assert all(grant in PERMISSIONS for grants in GRANTS.values() for grant in grants)


def roles_in(db: Session, principal: Principal, project: Project | None) -> set[str]:
    """The roles that count for this request: the account role, plus the project role in that project.

    Without a project, every project role the person holds in the account counts, so an
    engineer of one project can still see the account's shared pages.
    """
    if principal.support:
        limit = principal.support_project_id
        inside = limit is None or (project is not None and project.id == limit)
        return {"support"} if inside else {"support_elsewhere"}
    user = principal.user
    roles = {"operator"} if user.role == "operator" else set()
    if user.account_role:
        roles.add(user.account_role)
    members = select(ProjectMember.role).where(ProjectMember.user_id == user.id)
    if project is not None:
        members = members.where(ProjectMember.project_id == project.id)
    else:
        members = members.join(Project).where(Project.org_id == principal.org_id)
    roles.update(db.scalars(members))
    return roles


def is_allowed(roles: set[str], permission: str) -> bool:
    return any(permission in GRANTS.get(role, ()) for role in roles)


def can(db: Session, principal: Principal, permission: str) -> bool:
    """Whether this person holds a permission, for a route that shows part of its answer only to some.

    A project permission is checked in the project the route already resolved.
    """
    project = principal.project if PERMISSIONS[permission].scope == PROJECT else None
    return is_allowed(roles_in(db, principal, project), permission)


def holders(permission: str) -> list[str]:
    """Role names that grant a permission, for the refusal message."""
    return [ROLE_LABELS[role] for role in ROLE_LABELS if permission in GRANTS[role]]


def held(db: Session, principal: Principal, project: Project | None = None) -> list[str]:
    """What this person may do: account permissions, plus project permissions in one project.

    Screens use it to show or disable a control; the server checks again on every call.
    The project is the one the URL names. When the URL names none, that project is Main.
    """
    if project is None:
        project = db.scalar(select(Project).where(Project.org_id == principal.org_id, Project.slug == MAIN_SLUG))
    in_project = roles_in(db, principal, project) if project is not None else set()
    anywhere = roles_in(db, principal, None)
    if project is not None and project.archived:  # read-only for account roles, closed for project roles
        account = account_roles(db, principal)
        return sorted(
            name for name, rule in PERMISSIONS.items()
            if (rule.scope != PROJECT and is_allowed(anywhere, name))
            or (rule.scope == PROJECT and name in ARCHIVED_READS and is_allowed(account, name))
        )
    return sorted(
        name for name, rule in PERMISSIONS.items()
        if is_allowed(in_project if rule.scope == PROJECT else anywhere, name)
    )


@dataclass(frozen=True)
class FromObject:
    """Take the project from the row a path parameter names, after checking it is in the caller's account."""

    model: type
    param: str


class Allow:
    """The dependency every route uses. Returns the Principal, with `.project` set for project permissions.

    A project permission without `project_from` acts on the project named by the `project`
    query parameter (a slug), or on Main when there is none.
    """

    def __init__(self, permission: str, project_from: FromObject | None = None):
        rule = PERMISSIONS[permission]
        if project_from is not None and rule.scope != PROJECT:
            raise ValueError(f"{permission} is not a project permission, so it cannot take a project.")
        self.permission, self.rule, self.project_from = permission, rule, project_from

    def __call__(self, request: Request, principal: Principal = Depends(current_user),
                 db: Session = Depends(get_db)) -> Principal:
        project = None
        if self.rule.scope == PROJECT:
            project = _project_for(db, principal, request, self.project_from)
        if project is not None and project.archived:
            _archived_gate(db, principal, self.permission)
        elif not is_allowed(roles_in(db, principal, project), self.permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, _refusal(principal, self.permission, project))
        # An account permission leaves the project alone, so a route may also need a project permission.
        if project is not None:
            principal.project = project
        return principal


def allow(permission: str, project_from: FromObject | None = None) -> Allow:
    return Allow(permission, project_from)


def _project_for(db: Session, principal: Principal, request: Request, source: FromObject | None) -> Project:
    if source is None:
        slug = request.query_params.get("project") or MAIN_SLUG
        project = db.scalar(select(Project).where(Project.org_id == principal.org_id, Project.slug == slug))
    else:
        row = _row(db, source, request.path_params.get(source.param))
        if row is None or row.org_id != principal.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
        project = db.get(Project, row.project_id)
    if project is None or project.org_id != principal.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return project


ARCHIVED = "This project is archived. An Account owner or Account admin can restore it."
ARCHIVED_READ_ONLY = "This project is archived, so it is read-only. An Account owner or Account admin can restore it."
# What an account role may still do in an archived project (R-047, Deva 2026-10-30: read-only for account roles).
# Project roles are closed out, so for them the project does not exist until it is restored.
ARCHIVED_READS = frozenset({"see_project", "read_project_audit"})


def account_roles(db: Session, principal: Principal) -> set[str]:
    """The person's account-wide role, without any project role. Support sessions hold none."""
    if principal.support:
        return set()
    return {role for role in roles_in(db, principal, None) if role in ACCOUNT_ROLES}


def _archived_gate(db: Session, principal: Principal, permission: str) -> None:
    account = account_roles(db, principal)
    if permission in ARCHIVED_READS and is_allowed(account, permission):
        return
    if any(is_allowed({role}, "see_project") for role in account):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ARCHIVED_READ_ONLY)
    raise HTTPException(status.HTTP_404_NOT_FOUND, ARCHIVED)


def _row(db: Session, source: FromObject, raw: str | None):
    try:
        return db.get(source.model, int(raw or ""))
    except ValueError:
        return None


def _refusal(principal: Principal, permission: str, project: Project | None) -> str:
    if principal.support and principal.support_project_id is not None and (
            project is None or project.id != principal.support_project_id):
        return "This support session is limited to one project by the account."
    if principal.support:
        return "Support sessions are read-only."
    rule = PERMISSIONS[permission]
    where = f" in {project.name}" if project is not None else ""
    return f"Your role cannot do this{where}. {rule.label} needs one of: {', '.join(holders(permission))}."


# Routes reached before or without a session. Each says why; the route scan test refuses any other route
# that does not use allow().
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): "the first sign-in step",
    ("POST", "/api/auth/mfa"): "the two-step code, with a short step token",
    ("POST", "/api/auth/mfa/setup"): "two-step setup, with a short step token",
    ("POST", "/api/auth/mfa/confirm"): "two-step setup, with a short step token",
    ("GET", "/api/invites/{token}"): "an invitation link, checked by its one-time token",
    ("POST", "/api/invites/{token}/accept"): "an invitation link, checked by its one-time token",
    ("POST", "/api/ingest/logs"): "a collector, checked by its own token",
    ("POST", "/api/ingest/network"): "a collector, checked by its own token",
    ("POST", "/api/ingest/processes"): "a collector, checked by its own token",
    ("GET", "/api/health"): "a liveness check that returns only the version",
    ("GET", "/api/agent/collector.py"): "the collector program, for a server to install; it holds no secret",
    ("GET", "/openapi.json"): "the API description, which holds no data",
    ("GET", "/docs"): "the API description page, which holds no data",
    ("GET", "/docs/oauth2-redirect"): "part of the API description page",
    ("GET", "/redoc"): "the API description page, which holds no data",
    ("GET", "/{path:path}"): "the built screens; every screen asks the API, which checks again",
    ("POST", "/api/github/webhook"): "a GitHub delivery, checked by its signature over the exact body",
    ("GET", "/api/github/callback"): "GitHub's return from installing the App, checked by its signed state and code",
    ("POST", "/api/factory/recheck"): "Fix Factory proving a fix, checked by the dispatch token it shares with this server",
    ("GET", "/api/registry/simple/"): "a build system reading the registry index, checked by its registry token",
    ("GET", "/api/registry/simple/{project}/"): "a build system reading the registry index, checked by its registry token",
    ("GET", "/api/registry/files/{filename}"): "a build system downloading a fixed library, checked by its registry token",
    ("POST", "/api/factory/registry-access"): "Fix Factory's install step, checked by the dispatch token it shares with this server",
}

"""The role matrix of spec 0A, enforced on every route.

The account comes from matrix_account: a person for each role and two projects, Main, where the
project roles are granted, and Side, where nobody below the account roles is a member.
"""

from fastapi.routing import APIRoute
from matrix_account import ROLES, doc, matrix_fixture  # noqa: F401 - the fixture is named matrix
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Clone, Project, ProjectMember, User
from app.permissions import GRANTS, PERMISSIONS, PUBLIC_ROUTES, Allow, is_allowed

EVERYONE = frozenset(ROLES)
ACCOUNT_READERS = {"account_owner", "account_admin", "security_lead", "auditor"}

# Spec 0A, copied by hand so a change to the matrix in code has to be made twice to pass.
SPEC = {
    "see_project": EVERYONE,
    "edit_model": {"project_admin", "engineer"},
    "triage": {"security_lead", "project_admin", "approver", "engineer"},
    "approve": {"security_lead", "approver"},
    "set_policies": {"security_lead", "project_admin"},
    "attach_connections": {"account_admin", "project_admin"},
    "manage_members": {"account_admin", "project_admin"},
    "manage_projects": {"account_owner", "account_admin"},
    "archive_project": {"project_admin"},
    "manage_users": {"account_owner", "account_admin"},
    "manage_connections": {"account_owner", "account_admin"},
    "manage_ai": {"account_owner", "account_admin"},
    "manage_billing": {"account_owner"},
    "read_audit": {"account_owner", "account_admin", "security_lead", "auditor"},
    # "All" for the account roles, "own project" for a Project admin.
    "read_project_audit": {"account_owner", "account_admin", "security_lead", "auditor", "project_admin"},
    # Gaps the spec table does not name; permissions.py says why each exists.
    "use_account": EVERYONE,
    "sign_off": {"account_owner", "account_admin"},
    "be_signed_in": EVERYONE,
    # Raw collector data belongs to no project yet, so it goes with seeing every project.
    "read_account_data": ACCOUNT_READERS,
    # Settings every project uses (working hours, the practice repository) are the account's (R-041).
    "manage_account_setup": {"account_owner", "account_admin"},
}

# One real route per permission that has one today. An empty body makes an allowed call stop at
# validation (422) and a refused call stop at the permission check (403), so nothing changes.
# The approve route is called on a fix that is already decided, so an allowed call stops at 409.
ENDPOINTS = {
    "approve": ("POST", "/api/remediations/{fix_decided}/approve"),
    "attach_connections": ("PUT", "/api/connectors/{connector}/scope"),
    "read_project_audit": ("GET", "/api/projects/audit"),
    "see_project": ("GET", "/api/clones/{clone_main}"),
    "edit_model": ("POST", "/api/clones"),
    "triage": ("PATCH", "/api/network/findings/{finding_main}"),
    "manage_users": ("POST", "/api/admin/invites"),
    "manage_connections": ("POST", "/api/collectors"),
    "manage_ai": ("POST", "/api/models"),
    "manage_billing": ("PUT", "/api/admin/plan"),
    "read_audit": ("GET", "/api/admin/audit"),
    "use_account": ("GET", "/api/models"),
    "sign_off": ("DELETE", "/api/setup/support-access"),
    "be_signed_in": ("GET", "/api/auth/me"),
    "manage_projects": ("POST", "/api/projects"),
    "manage_members": ("POST", "/api/projects/{project_slug}/members"),
    "archive_project": ("POST", "/api/projects/{project_slug}/archive"),
    "set_policies": ("PATCH", "/api/projects/{project_slug}/policy"),
    "read_account_data": ("GET", "/api/logs/digests"),
    "manage_account_setup": ("PUT", "/api/setup/hours"),
}
NO_ROUTE_YET: set[str] = set()


def test_the_matrix_in_code_is_spec_0a():
    assert set(SPEC) == set(PERMISSIONS) - {"operate_platform"}
    for role in ROLES:
        for permission, holders in SPEC.items():
            assert is_allowed({role}, permission) == (role in holders), (role, permission)
    assert len(ROLES) * len(SPEC) == 160


def test_a_support_session_and_an_operator_only_read_or_run_the_platform():
    assert GRANTS["support"] == {"see_project", "use_account", "be_signed_in"}
    assert GRANTS["support_elsewhere"] == {"be_signed_in"}
    assert GRANTS["operator"] == {"operate_platform", "be_signed_in"}
    assert not any(is_allowed({role}, "operate_platform") for role in ROLES)


def test_every_role_gets_exactly_what_the_matrix_says_on_a_real_route(client, matrix):
    calls = 0
    for permission, (method, template) in ENDPOINTS.items():
        path = template.format(**matrix.ids)
        for role in ROLES:
            body = None if method in ("GET", "DELETE") else {"scope": "not a scope"} if "/scope" in path else {}
            res = client.request(method, path, headers=matrix.headers[role], json=body)
            calls += 1
            if role in SPEC[permission]:
                assert res.status_code not in (401, 403, 404) and res.status_code < 500, (role, permission, res.text)
            else:
                assert res.status_code == 403, (role, permission, res.status_code, res.text)
    assert calls == len(ENDPOINTS) * len(ROLES)


# P6.3: the sense point routes. One permission each, so every role is called on every route; an empty body stops an allowed
# call at validation (422) or at "that node is not in this clone" (404), so nothing changes.
SENSE_ROUTES = (
    ("see_project", "GET", "/api/clones/{clone_main}/sense-points", None),
    ("edit_model", "POST", "/api/clones/{clone_main}/sense-points", {"node": "n_nowhere"}),
    ("edit_model", "PATCH", "/api/sense-points/{sense_point_main}", {"status": "not a status"}),
    ("edit_model", "DELETE", "/api/sense-points/{sense_point_main}", None),
    # Last: a new analysis replaces the suggestions the other calls were made on.
    ("edit_model", "POST", "/api/clones/{clone_main}/sense-points/analyse", None),
)


def test_every_role_gets_exactly_what_the_matrix_says_on_the_sense_point_routes(client, matrix):
    for permission, method, template, body in SENSE_ROUTES:
        path = template.format(**matrix.ids)
        for role in ROLES:
            res = client.request(method, path, headers=matrix.headers[role], json=body)
            if role in SPEC[permission]:
                assert res.status_code not in (401, 403) and res.status_code < 500, (role, method, path, res.text)
            else:
                assert res.status_code == 403, (role, method, path, res.status_code, res.text)


def test_sense_points_of_another_project_or_company_are_closed(client, matrix, engineer):
    mine = matrix.headers["engineer"]
    with SessionLocal() as db:
        side = db.scalar(select(Clone.id).where(Clone.org_id == matrix.org_id, Clone.slug == "side-estate"))
    assert client.get(f"/api/clones/{side}/sense-points", headers=mine).status_code == 403
    assert client.post(f"/api/clones/{side}/sense-points/analyse", headers=mine).status_code == 403
    # Another company's engineer asks for this company's clone and sense point: nothing is there for them.
    other = engineer
    assert client.get(f"/api/clones/{matrix.ids['clone_main']}/sense-points", headers=other).status_code == 404
    assert client.patch(f"/api/sense-points/{matrix.ids['sense_point_main']}", headers=other, json={"status": "accepted"}).status_code == 404


def test_every_permission_without_a_route_is_named():
    assert set(ENDPOINTS) | NO_ROUTE_YET == set(SPEC)


def test_a_project_member_sees_and_changes_only_their_own_project(client, matrix):
    engineer = matrix.headers["engineer"]
    ids = matrix.ids
    assert client.get(f"/api/clones/{ids['clone_main']}", headers=engineer).status_code == 200
    assert client.get(f"/api/clones/{ids['clone_side']}", headers=engineer).status_code == 403
    assert client.get(f"/api/clones/{ids['clone_side']}/versions", headers=engineer).status_code == 403
    assert client.get(f"/api/clones/{ids['clone_side']}/export", headers=engineer).status_code == 403
    assert client.post("/api/clones?project=side", headers=engineer, json={}).status_code == 403
    assert client.post(f"/api/clones/{ids['clone_side']}/versions", headers=engineer, json={}).status_code == 403
    assert client.patch(f"/api/network/findings/{ids['finding_side']}", headers=engineer,
                        json={"status": "resolved"}).status_code == 403
    assert client.get("/api/network/overview?project=side", headers=engineer).status_code == 403
    seen = client.get("/api/network/overview", headers=engineer).json()["findings"]
    assert {row["id"] for row in seen} == {ids["finding_main"]}
    for path in ("/api/incidents?project=side", "/api/signals?project=side", "/api/remediations?project=side",
                 "/api/agents?project=side", f"/api/incidents/{ids['incident_side']}"):
        assert client.get(path, headers=engineer).status_code == 403, path
    assert client.patch(f"/api/incidents/{ids['incident_side']}", headers=engineer,
                        json={"note": "not mine"}).status_code == 403
    assert client.post("/api/incidents?project=side", headers=engineer,
                       json={"title": "Opened in the wrong project", "severity": "high",
                             "signal_ids": [ids["signal_side"]]}).status_code == 403
    wrong = client.post("/api/incidents", headers=engineer, json={
        "title": "Main incident from a Side signal", "severity": "high", "signal_ids": [ids["signal_side"]]})
    assert wrong.status_code == 404 and "not in this project" in wrong.json()["detail"]
    assert {row["id"] for row in client.get("/api/incidents", headers=engineer).json()} == {ids["incident_main"]}
    assert ids["signal_side"] not in {row["id"] for row in client.get("/api/signals", headers=engineer).json()}
    fixes = {row["id"] for row in client.get("/api/remediations", headers=engineer).json()}
    assert ids["fix_decided"] in fixes and ids["fix_side"] not in fixes
    approver = matrix.headers["approver"]
    assert client.post(f"/api/remediations/{ids['fix_side']}/approve", headers=approver).status_code == 403


def test_an_account_role_reaches_every_project_of_its_own_account_only(client, ceo, matrix):
    lead = matrix.headers["security_lead"]
    ids = matrix.ids
    assert client.get(f"/api/clones/{ids['clone_side']}", headers=lead).status_code == 200
    side = client.get("/api/network/overview?project=side", headers=lead).json()["findings"]
    assert {row["id"] for row in side} == {ids["finding_side"]}
    assert client.get(f"/api/clones/{ids['clone_main']}", headers=ceo).status_code == 404
    assert client.patch(f"/api/network/findings/{ids['finding_main']}", headers=ceo,
                        json={"status": "resolved"}).status_code == 404
    assert client.get("/api/network/overview?project=nowhere", headers=lead).status_code == 404


def test_a_refusal_says_who_may_do_it(client, matrix):
    res = client.post("/api/admin/invites", headers=matrix.headers["approver"], json={})
    assert res.status_code == 403
    assert res.json()["detail"] == (
        "Your role cannot do this. Manage users and account roles needs one of: Account owner, Account admin."
    )


def test_the_last_account_owner_cannot_be_removed_or_change_their_own_role(client, matrix):
    with SessionLocal() as db:
        owner = db.scalar(select(User.id).where(User.email == "account_owner@matrixco.test"))
    admin_headers = matrix.headers["account_admin"]
    demote = client.patch(f"/api/admin/users/{owner}", headers=admin_headers, json={"role": "analyst"})
    assert demote.status_code == 409 and "last account owner" in demote.json()["detail"]
    retire = client.patch(f"/api/admin/users/{owner}", headers=admin_headers, json={"active": False})
    assert retire.status_code == 409 and "last account owner" in retire.json()["detail"]
    own = client.patch(f"/api/admin/users/{owner}", headers=matrix.headers["account_owner"], json={"role": "analyst"})
    assert own.status_code == 409 and "own role" in own.json()["detail"]
    with SessionLocal() as db:
        person = db.get(User, owner)
        assert person.account_role == "account_owner" and person.active and person.role == "ceo"


def test_every_route_says_what_it_needs():
    """Every route the app serves, including the ones main.py adds itself, uses allow() or is listed public."""
    served = {(method, path): checked for path, methods, checked in _served(app.routes, "", False)
              for method in methods}
    unchecked = sorted(f"{method} {path}" for (method, path), checked in served.items()
                       if (method, path) not in PUBLIC_ROUTES and not checked)
    assert unchecked == []
    # The walk must reach the included routers: today there are 121 routes; a walk that sees few is broken.
    assert len(served) > 100
    # The built screens are only served when the frontend has been built.
    assert {key for key in PUBLIC_ROUTES if key[1].startswith("/api/")} <= set(served)


def test_the_help_pages_need_a_session(client, matrix):
    assert client.get("/api/help").status_code == 401
    assert client.get("/api/help/index").status_code == 401
    pages = client.get("/api/help", headers=matrix.headers["viewer"])
    assert pages.status_code == 200 and pages.json()


def _served(routes, prefix: str, checked_above: bool):
    """(path, methods, uses allow) for every route, walking into included routers with their prefix."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route.methods, checked_above or _uses_allow(route.dependant)
        elif hasattr(route, "original_router"):  # an included router, kept apart by newer FastAPI
            context = route.include_context
            above = checked_above or any(isinstance(dep.dependency, Allow) for dep in context.dependencies)
            yield from _served(route.original_router.routes, prefix + context.prefix, above)


def _uses_allow(dependant) -> bool:
    return any(isinstance(dep.call, Allow) or _uses_allow(dep) for dep in dependant.dependencies)


def test_a_clone_lands_in_the_project_the_request_names(client, matrix):
    with SessionLocal() as db:
        side = db.scalar(select(Project).where(Project.org_id == matrix.org_id, Project.slug == "side"))
        db.add(ProjectMember(project_id=side.id, user_id=db.scalar(
            select(User.id).where(User.email == "engineer@matrixco.test")), role="engineer"))
        db.commit()
        side_id = side.id
    try:
        made = client.post("/api/clones?project=side", headers=matrix.headers["engineer"],
                           json={"author": "test", "reason": "side drawing", "document": doc("side-new")})
        assert made.status_code == 200, made.text
        with SessionLocal() as db:
            assert db.get(Clone, made.json()["id"]).project_id == side_id
    finally:
        with SessionLocal() as db:
            db.query(ProjectMember).filter(ProjectMember.project_id == side_id,
                                           ProjectMember.role == "engineer").delete()
            db.commit()

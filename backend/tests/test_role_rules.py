"""The role rules of spec 0A that are more than the matrix, each enforced on the server (R-041).

Uses the shared account from matrix_account: Main holds the project roles, Side holds nobody below the
account roles.
"""

from dataclasses import dataclass

import pytest
from matrix_account import matrix_fixture  # noqa: F401 - the fixture is named matrix
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Agent, AuditLog, Invite, Project, ProjectMember, Remediation, User
from app.whitehats import CATALOG, dispatch


def _project(db, org_id: int, slug: str) -> Project:
    return db.scalar(select(Project).where(Project.org_id == org_id, Project.slug == slug))


def _critical_fix(org_id: int, project_id: int, title: str) -> int:
    with SessionLocal() as db:
        row = Remediation(org_id=org_id, project_id=project_id, title=title, target="db-1", risk="critical",
                          action="Rotate the key.", destination="Slack", status="pending")
        db.add(row)
        db.commit()
        return row.id


@pytest.fixture
def two_approver_main(matrix):
    with SessionLocal() as db:
        main = _project(db, matrix.org_id, "main")
        main.two_approver = True
        db.commit()
        main_id = main.id
    yield main_id
    with SessionLocal() as db:
        _project(db, matrix.org_id, "main").two_approver = False
        db.commit()


def test_a_critical_fix_needs_two_different_approvers_when_the_project_asks(client, matrix, two_approver_main):
    rid = _critical_fix(matrix.org_id, two_approver_main, "Matrix critical fix")
    approver, lead = matrix.headers["approver"], matrix.headers["security_lead"]
    first = client.post(f"/api/remediations/{rid}/approve", headers=approver)
    assert first.status_code == 200 and first.json()["status"] == "pending"
    assert first.json()["first_approved_by"] == "Approver"
    again = client.post(f"/api/remediations/{rid}/approve", headers=approver)
    assert again.status_code == 409 and "two different Approvers" in again.json()["detail"]
    second = client.post(f"/api/remediations/{rid}/approve", headers=lead)
    assert second.status_code == 200 and second.json()["status"] == "dispatched"
    assert second.json()["decided_by"] == "Approver and Security Lead"


def test_one_approval_is_enough_below_critical_or_when_the_project_does_not_ask(client, matrix, two_approver_main):
    with SessionLocal() as db:
        high = Remediation(org_id=matrix.org_id, project_id=two_approver_main, title="Matrix high fix",
                           target="db-1", risk="high", action="Patch.", destination="Slack", status="pending")
        db.add(high)
        db.commit()
        high_id = high.id
    approver = matrix.headers["approver"]
    assert client.post(f"/api/remediations/{high_id}/approve", headers=approver).json()["status"] == "dispatched"
    with SessionLocal() as db:
        _project(db, matrix.org_id, "main").two_approver = False
        db.commit()
    rid = _critical_fix(matrix.org_id, two_approver_main, "Matrix critical fix, one approver")
    assert client.post(f"/api/remediations/{rid}/approve", headers=approver).json()["status"] == "dispatched"


@pytest.mark.parametrize("role", ["project_admin", "account_admin"])
def test_nobody_gives_themselves_a_project_role(client, matrix, role):
    res = client.post("/api/projects/main/members", headers=matrix.headers[role],
                      json={"email": f"{role}@matrixco.test", "role": "approver", "reason": "I want to approve"})
    assert res.status_code == 403 and "Nobody changes their own role" in res.json()["detail"]
    with SessionLocal() as db:
        person = db.scalar(select(User.id).where(User.email == f"{role}@matrixco.test"))
        roles = set(db.scalars(select(ProjectMember.role).where(ProjectMember.user_id == person)))
    assert "approver" not in roles


def test_a_grant_says_why_and_the_reason_is_audited(client, matrix):
    admin = matrix.headers["project_admin"]
    blank = client.post("/api/projects/main/members", headers=admin,
                        json={"email": "viewer@matrixco.test", "role": "engineer", "reason": "  "})
    assert blank.status_code == 400 and "Say why" in blank.json()["detail"]
    missing = client.post("/api/projects/main/members", headers=admin,
                          json={"email": "viewer@matrixco.test", "role": "engineer"})
    assert missing.status_code == 422
    try:
        granted = client.post("/api/projects/main/members", headers=admin,
                              json={"email": "viewer@matrixco.test", "role": "engineer", "reason": "Joins the on-call"})
        assert granted.status_code == 200, granted.text
        assert _last_audit(matrix.org_id, "changed a project role").detail == "viewer to engineer. Why: Joins the on-call"
    finally:
        _set_member_role(matrix.org_id, "viewer@matrixco.test", "viewer")


def test_a_project_member_can_be_removed_with_a_reason_that_is_audited(client, matrix):
    admin = matrix.headers["project_admin"]
    viewer = matrix.headers["viewer"]
    assert client.get("/api/incidents", headers=viewer).status_code == 200
    try:
        unexplained = client.request("DELETE", "/api/projects/main/members", headers=admin,
                                     json={"email": "viewer@matrixco.test", "reason": ""})
        assert unexplained.status_code == 400
        removed = client.request("DELETE", "/api/projects/main/members", headers=admin,
                                 json={"email": "viewer@matrixco.test", "reason": "Left the team"})
        assert removed.status_code == 200, removed.text
        assert "viewer@matrixco.test" not in {row["email"] for row in removed.json()["members"]}
        entry = _last_audit(matrix.org_id, "removed from a project")
        assert entry.target == "viewer@matrixco.test" and entry.detail == "was viewer. Why: Left the team"
        assert client.get("/api/incidents", headers=viewer).status_code == 403
        again = client.request("DELETE", "/api/projects/main/members", headers=admin,
                               json={"email": "viewer@matrixco.test", "reason": "Left the team"})
        assert again.status_code == 404
        own = client.request("DELETE", "/api/projects/main/members", headers=admin,
                             json={"email": "project_admin@matrixco.test", "reason": "Stepping down"})
        assert own.status_code == 403
    finally:
        _set_member_role(matrix.org_id, "viewer@matrixco.test", "viewer")


def test_only_an_account_owner_makes_an_account_owner(client, matrix):
    admin, owner = matrix.headers["account_admin"], matrix.headers["account_owner"]
    invite = client.post("/api/admin/invites", headers=admin,
                         json={"email": "new-owner@matrixco.test", "name": "New owner", "role": "ceo"})
    assert invite.status_code == 403 and "Only an Account owner" in invite.json()["detail"]
    with SessionLocal() as db:
        auditor = db.scalar(select(User).where(User.email == "auditor@matrixco.test"))
        auditor_id, title = auditor.id, auditor.role
    promote = client.patch(f"/api/admin/users/{auditor_id}", headers=admin, json={"role": "ceo"})
    assert promote.status_code == 403
    with SessionLocal() as db:
        assert db.get(User, auditor_id).account_role == "auditor" and db.get(User, auditor_id).role == title
    # An admin still invites everyone else, and an owner may invite an owner.
    assert client.post("/api/admin/invites", headers=admin, json={
        "email": "new-analyst@matrixco.test", "name": "New analyst", "role": "analyst"}).status_code == 200
    assert client.post("/api/admin/invites", headers=owner, json={
        "email": "new-owner@matrixco.test", "name": "New owner", "role": "ceo"}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(Invite.role).where(Invite.email == "new-owner@matrixco.test")) == "ceo"


@dataclass
class _Probe:
    """A WhiteHat that only records which project's agent it ran for."""

    key: str = "matrix-probe"
    available: bool = True
    seen: list | None = None

    def on_log_batch(self, ctx, **_kwargs):
        self.seen.append(ctx.agent.project_id)


def test_an_archived_project_is_read_only_for_account_roles_closed_to_project_roles_and_not_fed(client, matrix, monkeypatch):
    """R-047 (Deva, 2026-10-30): an archived project stays readable for account roles and changes nothing."""
    lead, owner, engineer = matrix.headers["security_lead"], matrix.headers["account_owner"], matrix.headers["engineer"]
    ids = matrix.ids
    probe = _Probe(seen=[])
    monkeypatch.setitem(CATALOG, probe.key, probe)
    reads = ("/api/incidents?project=side", f"/api/incidents/{ids['incident_side']}",
             f"/api/clones/{ids['clone_side']}", "/api/projects/side/settings?project=side")
    with SessionLocal() as db:
        side = _project(db, matrix.org_id, "side")
        main_id = _project(db, matrix.org_id, "main").id
        member = db.scalar(select(User).where(User.email == "engineer@matrixco.test"))
        db.add(ProjectMember(project_id=side.id, user_id=member.id, role="engineer"))
        db.add_all([Agent(org_id=matrix.org_id, project_id=pid, key=probe.key, name="Probe", domain="Test.")
                    for pid in (main_id, side.id)])
        side.archived = True
        db.commit()
    try:
        for path in reads:
            assert client.get(path, headers=lead).status_code == 200, path  # an account role still reads it
            closed = client.get(path, headers=engineer)  # a project role does not see it at all
            assert closed.status_code == 404 and "archived" in closed.json()["detail"], path
        changed = client.patch(f"/api/network/findings/{ids['finding_side']}", headers=lead, json={"status": "resolved"})
        assert changed.status_code == 403 and "read-only" in changed.json()["detail"]
        me = client.get("/api/auth/me?project=side", headers=lead).json()
        assert me["project_archived"] is True
        assert {"see_project", "read_project_audit"} >= {name for name in me["user"]["permissions"]
                                                          if name in ("see_project", "triage", "edit_model", "approve",
                                                                      "read_project_audit", "manage_members")}
        with SessionLocal() as db:
            dispatch(db, matrix.org_id, "on_log_batch", source_host="h", lines=[])
        assert probe.seen == [main_id]  # nothing feeds an archived project
        with SessionLocal() as db:  # the probe is not a real WhiteHat; the project list reads every agent
            db.query(Agent).filter(Agent.org_id == matrix.org_id, Agent.key == probe.key).delete()
            db.commit()
        assert [row["slug"] for row in client.get("/api/projects/archived", headers=lead).json()] == ["side"]
        assert client.get("/api/projects/archived", headers=engineer).json() == []
        assert "side" not in [row["slug"] for row in client.get("/api/projects", headers=lead).json()]
        restored = client.patch("/api/projects/side", headers=owner, json={"archived": False})
        assert restored.status_code == 200
        assert client.get("/api/incidents?project=side", headers=engineer).status_code == 200
    finally:
        with SessionLocal() as db:
            side = _project(db, matrix.org_id, "side")
            side.archived = False
            db.query(ProjectMember).filter(ProjectMember.project_id == side.id,
                                           ProjectMember.role == "engineer").delete()
            db.query(Agent).filter(Agent.org_id == matrix.org_id, Agent.key == probe.key).delete()
            db.commit()


@pytest.mark.parametrize(("method", "path", "body"), [
    ("PUT", "/api/setup/hours", {"timezone": "UTC", "work_start": "09:00", "work_end": "17:00"}),
    ("POST", "/api/factory/practice/reset", None),
])
def test_account_wide_settings_need_an_account_role(client, matrix, method, path, body):
    for role in ("project_admin", "engineer", "security_lead"):
        res = client.request(method, path, headers=matrix.headers[role], json=body)
        assert res.status_code == 403, (role, res.status_code)
    allowed = client.request(method, path, headers=matrix.headers["account_admin"], json=body)
    # The practice reset reaches the engine, which this company does not have, so it stops at 409.
    assert allowed.status_code in (200, 409), allowed.text


def _last_audit(org_id: int, action: str) -> AuditLog:
    with SessionLocal() as db:
        return db.scalar(select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == action)
                         .order_by(AuditLog.id.desc()))


def _set_member_role(org_id: int, email: str, role: str) -> None:
    with SessionLocal() as db:
        person = db.scalar(select(User.id).where(User.email == email))
        main = _project(db, org_id, "main")
        row = db.scalar(select(ProjectMember).where(ProjectMember.project_id == main.id,
                                                    ProjectMember.user_id == person))
        if row is None:
            db.add(ProjectMember(project_id=main.id, user_id=person, role=role))
        else:
            row.role = role
        db.commit()

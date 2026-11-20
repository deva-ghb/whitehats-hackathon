"""Audit and support access by project (checklist AP.6).

An action inside a project is filed under it. A project admin reads only their project's trail;
account roles read every project. A support session the account limits to one project sees nothing else.
"""

from datetime import timedelta

import pytest
from conftest import PASSWORD, login
from sqlalchemy import select

from app.db import SessionLocal, utcnow
from app.models import AuditLog, Incident, NetworkFinding, Organization, Project, ProjectMember, User
from app.projects import ensure_main_project
from app.security import hash_password
from app.services.tenancy import delete_tenant_data

PEOPLE = {
    "owner": ("account_owner", None), "auditor": ("auditor", None),
    "admin_main": (None, ("main", "project_admin")), "admin_side": (None, ("side", "project_admin")),
}


@pytest.fixture
def account(client):
    with SessionLocal() as db:
        _remove(db)
        org = Organization(name="Audit Co", slug="auditco", mfa_required=False, status="live")
        db.add(org)
        db.flush()
        projects = {"main": ensure_main_project(db, org), "side": Project(org_id=org.id, name="Side", slug="side")}
        db.add(projects["side"])
        db.flush()
        for name, (account_role, member) in PEOPLE.items():
            person = User(org_id=org.id, email=f"{name}@auditco.test", name=name.replace("_", " ").title(),
                          role="ceo" if account_role == "account_owner" else "analyst", account_role=account_role,
                          password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at)
            db.add(person)
            db.flush()
            if member:
                db.add(ProjectMember(project_id=projects[member[0]].id, user_id=person.id, role=member[1]))
        ids = {"org": org.id}
        for slug, project in projects.items():
            ids[f"project_{slug}"] = project.id
            finding = NetworkFinding(org_id=org.id, project_id=project.id, key=f"audit-{slug}", kind="exposure",
                                     severity="high", title=f"Port 22 open in {slug}", detail="Audit test.")
            incident = Incident(org_id=org.id, project_id=project.id, ref=f"INC-{project.id}", title=f"{slug} incident",
                                severity="high", status="open", summary="Audit test.", business_impact="None.")
            db.add_all([finding, incident])
            db.flush()
            ids[f"finding_{slug}"] = finding.id
        db.commit()
    yield ids
    with SessionLocal() as db:
        _remove(db)


def _remove(db) -> None:
    org = db.scalar(select(Organization).where(Organization.slug == "auditco"))
    if org is not None:
        delete_tenant_data(db, org)
        db.delete(org)
        db.commit()


def _act_in_both_projects(client, account) -> None:
    for slug in ("main", "side"):
        admin = login(client, f"admin_{slug}@auditco.test")
        res = client.patch(f"/api/network/findings/{account[f'finding_{slug}']}", headers=admin,
                           json={"status": "accepted"})
        assert res.status_code == 200, res.text


def test_an_action_inside_a_project_is_filed_under_it(client, account):
    _act_in_both_projects(client, account)
    owner = login(client, "owner@auditco.test")
    assert client.post("/api/admin/invites", headers=owner, json={"email": "new@auditco.test", "role": "analyst"}
                       ).status_code == 200
    with SessionLocal() as db:
        filed = dict(db.execute(select(AuditLog.target, AuditLog.project_id).where(
            AuditLog.org_id == account["org"], AuditLog.action == "marked network finding accepted")).all())
        invited = db.scalar(select(AuditLog.project_id).where(
            AuditLog.org_id == account["org"], AuditLog.action == "invited user"))
    assert filed == {"Port 22 open in main": account["project_main"], "Port 22 open in side": account["project_side"]}
    assert invited is None  # about the whole account


def test_a_project_admin_reads_only_their_project_audit(client, account):
    _act_in_both_projects(client, account)
    admin_main = login(client, "admin_main@auditco.test")
    rows = client.get("/api/projects/audit", headers=admin_main).json()
    assert [row["target"] for row in rows] == ["Port 22 open in main"]
    refused = client.get("/api/projects/audit?project=side", headers=admin_main)
    assert refused.status_code == 403 and "Project admin" in refused.json()["detail"]
    assert client.get("/api/admin/audit", headers=admin_main).status_code == 403


def test_account_roles_read_every_project_audit(client, account):
    _act_in_both_projects(client, account)
    for person in ("owner", "auditor"):
        headers = login(client, f"{person}@auditco.test")
        side = client.get("/api/projects/audit?project=side", headers=headers).json()
        assert [row["target"] for row in side] == ["Port 22 open in side"], person
        whole = {row["target"] for row in client.get("/api/admin/audit", headers=headers).json()}
        assert {"Port 22 open in main", "Port 22 open in side"} <= whole, person


def _support(client, operator, account, project: str | None) -> dict:
    owner = login(client, "owner@auditco.test")
    grant = {"hours": 2, "reason": "Looking at the side project", **({"project": project} if project else {})}
    res = client.post("/api/setup/support-access", headers=owner, json=grant)
    assert res.status_code == 200, res.text
    session = client.post(f"/api/operator/tenants/{account['org']}/support-session", headers=operator)
    assert session.status_code == 200, session.text
    return {"Authorization": f"Bearer {session.json()['token']}"}


def test_a_support_session_limited_to_one_project_is_refused_in_another(client, operator, account):
    support = _support(client, operator, account, "main")
    assert client.get("/api/incidents", headers=support).status_code == 200
    refused = client.get("/api/incidents?project=side", headers=support)
    assert refused.status_code == 403
    assert refused.json()["detail"] == "This support session is limited to one project by the account."
    assert client.get("/api/models", headers=support).status_code == 403
    assert client.get("/api/auth/me", headers=support).status_code == 200
    assert client.patch(f"/api/network/findings/{account['finding_main']}", headers=support,
                        json={"status": "resolved"}).status_code == 403  # still read-only inside it
    with SessionLocal() as db:
        granted = db.scalar(select(AuditLog.target).where(
            AuditLog.org_id == account["org"], AuditLog.action == "granted WhiteHats support access"))
    assert granted == "2 hours in Main only"


def test_an_unlimited_support_session_reads_every_project_and_then_expires(client, operator, account):
    support = _support(client, operator, account, None)
    assert client.get("/api/incidents?project=side", headers=support).status_code == 200
    with SessionLocal() as db:
        db.get(Organization, account["org"]).support_access_until = utcnow() - timedelta(minutes=1)
        db.commit()
    ended = client.get("/api/incidents", headers=support)
    assert ended.status_code == 403 and "ended" in ended.json()["detail"]


def test_a_support_grant_names_a_project_of_this_account(client, account):
    owner = login(client, "owner@auditco.test")
    res = client.post("/api/setup/support-access", headers=owner,
                      json={"hours": 1, "reason": "Wrong project name", "project": "nowhere"})
    assert res.status_code == 404


def test_main_cannot_be_archived_but_another_project_can(client, account):
    main_admin = login(client, "admin_main@auditco.test")
    side_admin = login(client, "admin_side@auditco.test")
    refused = client.post("/api/projects/main/archive", headers=main_admin, json={"confirm": True})
    assert refused.status_code == 409 and "Main cannot be archived" in refused.json()["detail"]
    assert client.post("/api/projects/side/archive?project=side", headers=side_admin, json={"confirm": True}).status_code == 200
    with SessionLocal() as db:
        assert db.get(Project, account["project_main"]).archived is False
        assert db.get(Project, account["project_side"]).archived is True

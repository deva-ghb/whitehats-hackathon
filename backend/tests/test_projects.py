"""A new account opens one empty Main project. The demo is four projects, its home project the Fintech shop."""

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    Agent,
    ChatMessage,
    Clone,
    Incident,
    NetworkFinding,
    Organization,
    PostureSnapshot,
    Project,
    ProjectMember,
    Remediation,
    SensePoint,
    Signal,
    User,
)

FRESH = {
    "name": "Fresh Co", "slug": "freshco", "plan": "growth", "region": "in-south", "domain": "freshco.test",
    "mfa_required": True, "retention_log_days": 30, "retention_run_days": 365, "retention_audit_days": 730,
    "ai_policy": {"providers": ["azure_openai", "rules"], "zero_retention": True, "agreed": True},
    "contract": {"order_ref": "FR-1", "owner_name": "Ada Fresh", "owner_email": "ada@freshco.test",
                 "security_contact": "Sec desk", "pilot": True, "pass_marks": "one fix"},
    "contracted_agents": [],
}

_EMPTY = (Incident, Signal, Remediation, Clone, ChatMessage, NetworkFinding, SensePoint)


def test_new_account_starts_with_one_empty_main_project(client, operator):
    created = client.post("/api/operator/tenants", headers=operator, json=FRESH)
    assert created.status_code == 200, created.text
    org_id = created.json()["id"]
    with SessionLocal() as db:
        operator_id = db.scalar(select(User.id).where(User.email == "operator@whitehats.example"))
        projects = db.scalars(select(Project).where(Project.org_id == org_id)).all()
        assert len(projects) == 1
        project = projects[0]
        assert project.name == "Main" and project.slug == "main"
        assert project.criticality == "medium" and project.archived is False
        assert project.created_by == operator_id
        for model in _EMPTY:
            count = db.scalar(select(func.count()).select_from(model).where(model.org_id == org_id))
            assert count == 0, model.__tablename__
        agents = db.scalars(select(Agent).where(Agent.org_id == org_id)).all()
        assert agents
        assert all(agent.project_id == project.id for agent in agents)
        members = db.scalar(select(func.count()).select_from(ProjectMember).where(
            ProjectMember.project_id == project.id))
        assert members == 0


DEMO_PROJECTS = {"main": "Fintech shop", "health-platform": "Health platform", "saas-with-ai": "SaaS with AI",
                 "whole-estate": "Whole estate"}


def test_demo_seed_is_four_projects_with_mapped_roles(client):
    """AP.7: spec 0A's demo account. The home project keeps slug main, so old links still open it."""
    assert client
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        assert org.name == "Acme (demo)"
        projects = {p.slug: p for p in db.scalars(select(Project).where(Project.org_id == org.id))}
        assert {slug: p.name for slug, p in projects.items()} == DEMO_PROJECTS
        assert not any(p.archived for p in projects.values())
        for model in (Incident, Signal, Remediation, Agent):
            rows = db.scalars(select(model).where(model.org_id == org.id)).all()
            assert rows, model.__tablename__
            assert {row.project_id for row in rows} <= {p.id for p in projects.values()}
        refs = dict(db.execute(select(Incident.ref, Incident.project_id).where(Incident.org_id == org.id)).all())
        main, health, saas = projects["main"].id, projects["health-platform"].id, projects["saas-with-ai"].id
        assert {ref: pid for ref, pid in refs.items() if ref in ("INC-2208", "INC-2211", "INC-2214", "INC-2190",
                                                                 "INC-2216", "INC-2217")} == {
            "INC-2208": main, "INC-2211": main, "INC-2214": main, "INC-2190": main,
            "INC-2216": health, "INC-2217": saas}
        estate_incidents = db.scalar(select(func.count(Incident.id)).where(
            Incident.project_id == projects["whole-estate"].id))
        assert estate_incidents == 0
        for model in (PostureSnapshot, Clone, ChatMessage, NetworkFinding):
            rows = db.scalars(select(model).where(model.org_id == org.id)).all()
            assert {row.project_id for row in rows} <= {p.id for p in projects.values()}
        users = {user.email.split("@")[0]: user for user in db.scalars(select(User).where(User.org_id == org.id))}
        every = dict.fromkeys(DEMO_PROJECTS, "approver")
        assert users["ceo"].account_role == "account_owner" and _by_slug(db, users["ceo"]) == every
        assert users["ciso"].account_role == "security_lead" and _by_slug(db, users["ciso"]) == {}
        assert users["analyst"].account_role is None
        assert _by_slug(db, users["analyst"]) == dict.fromkeys(DEMO_PROJECTS, "engineer")
        assert users["engineer"].account_role is None and _by_slug(db, users["engineer"]) == {"main": "engineer"}
        assert users["health.engineer"].account_role is None
        assert _by_slug(db, users["health.engineer"]) == {"health-platform": "engineer"}
        assert users["auditor"].account_role == "auditor" and _by_slug(db, users["auditor"]) == {}
        assert users["approver"].account_role is None and _by_slug(db, users["approver"]) == {"main": "approver"}
        platform = db.scalar(select(Organization).where(Organization.is_platform.is_(True)))
        mains = db.scalars(select(Project).where(Project.org_id == platform.id)).all()
        assert len(mains) == 1 and mains[0].name == "Main" and mains[0].slug == "main"
        operator = db.scalar(select(User).where(User.org_id == platform.id))
        assert operator.role == "operator" and operator.account_role is None
        assert _roles(db, operator) == []


def _by_slug(db, user):
    return dict(db.execute(select(Project.slug, ProjectMember.role).join(ProjectMember)
                           .where(ProjectMember.user_id == user.id)).all())


def _roles(db, user):
    return list(db.scalars(select(ProjectMember.role).where(ProjectMember.user_id == user.id)))


def test_changing_a_role_moves_the_account_and_project_roles_with_it(client, ceo):
    """CEO to CISO to engineer to analyst: each step leaves only what the new role gives, plus grants made by hand."""
    with SessionLocal() as db:
        analyst = db.scalar(select(User).where(User.email == "analyst@acme.example"))
        org_id, analyst_id = analyst.org_id, analyst.id
        demo = list(db.scalars(select(Project.id).where(Project.org_id == org_id)))
        side = Project(org_id=org_id, name="Side", slug="side-role-test")
        db.add(side)
        db.flush()
        side_id = side.id
        db.add(ProjectMember(project_id=side_id, user_id=analyst_id, role="project_admin"))
        db.commit()
    try:
        approver = dict.fromkeys(demo, "approver") | {side_id: "project_admin"}
        assert _change(client, ceo, analyst_id, "ceo") == ("account_owner", approver)
        assert _change(client, ceo, analyst_id, "ciso") == ("security_lead", {side_id: "project_admin"})
        assert _change(client, ceo, analyst_id, "engineer") == (None, {side_id: "project_admin"})
        engineer = dict.fromkeys(demo, "engineer") | {side_id: "project_admin"}
        assert _change(client, ceo, analyst_id, "analyst") == (None, engineer)
    finally:
        with SessionLocal() as db:
            db.query(ProjectMember).filter(ProjectMember.project_id == side_id).delete()
            db.query(Project).filter(Project.id == side_id).delete()
            db.commit()


def test_an_engineer_promoted_and_demoted_keeps_their_projects(client, ceo):
    with SessionLocal() as db:
        engineer_id = db.scalar(select(User.id).where(User.email == "engineer@acme.example"))
        org_id = db.scalar(select(User.org_id).where(User.id == engineer_id))
        demo = list(db.scalars(select(Project.id).where(Project.org_id == org_id)))
        main = db.scalar(select(Project.id).where(Project.org_id == org_id, Project.slug == "main"))
    try:
        # An analyst is an engineer in every project; going back to engineer keeps every project they were in.
        assert _change(client, ceo, engineer_id, "analyst") == (None, dict.fromkeys(demo, "engineer"))
        assert _change(client, ceo, engineer_id, "engineer") == (None, dict.fromkeys(demo, "engineer"))
    finally:
        with SessionLocal() as db:
            db.query(ProjectMember).filter(ProjectMember.user_id == engineer_id,
                                           ProjectMember.project_id != main).delete()
            db.commit()


def _change(client, headers, user_id, role):
    res = client.patch(f"/api/admin/users/{user_id}", headers=headers, json={"role": role})
    assert res.status_code == 200, res.text
    with SessionLocal() as db:
        user = db.get(User, user_id)
        members = dict(db.execute(select(ProjectMember.project_id, ProjectMember.role)
                                  .where(ProjectMember.user_id == user_id)).all())
        return user.account_role, members


def test_account_export_holds_its_projects_members_and_every_table_it_owns(client):
    import json
    from pathlib import Path

    from app.db import Base
    from app.services.tenancy import EXPORT_LEFT_OUT, export

    assert client
    owned = {table.name for table in Base.metadata.sorted_tables if {"org_id", "project_id"} & set(table.c.keys())}
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        main = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
        ceo_id = db.scalar(select(User.id).where(User.email == "ceo@acme.example"))
        path, _digest = export(db, org)
    try:
        tables = json.loads(Path(path).read_text())["tables"]
    finally:
        Path(path).unlink()
    assert owned - set(EXPORT_LEFT_OUT) <= set(tables), sorted(owned - set(EXPORT_LEFT_OUT) - set(tables))
    assert "project_members" in tables
    assert sorted(row["slug"] for row in tables["projects"]) == sorted(DEMO_PROJECTS)
    assert {(row["user_id"], row["role"]) for row in tables["project_members"]} >= {(ceo_id, "approver")}
    assert main.id in {row["project_id"] for row in tables["project_members"]}

"""AP.7: the demo account as four projects, the people who show access control, and the Whole estate union."""

import pytest
from conftest import login
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal
from app.models import Incident, Organization, Project, ProjectMember, Remediation, Signal, User

ALL = ["Fintech shop", "Health platform", "SaaS with AI", "Whole estate"]
SLUG = {"Fintech shop": "main", "Health platform": "health-platform", "SaaS with AI": "saas-with-ai",
        "Whole estate": "whole-estate"}
ACTIONS = ("edit_model", "triage", "approve", "manage_members", "read_project_audit")
# Spec 0A, read for each demo person: the projects they open, and what they may do in each.
EXPECTED = {
    "ceo@acme.example": (ALL, {"triage", "approve", "read_project_audit"}),
    "ciso@acme.example": (ALL, {"triage", "approve", "read_project_audit"}),
    "analyst@acme.example": (ALL, {"edit_model", "triage"}),
    "engineer@acme.example": (["Fintech shop"], {"edit_model", "triage"}),
    "health.engineer@acme.example": (["Health platform"], {"edit_model", "triage"}),
    "auditor@acme.example": (ALL, {"read_project_audit"}),
    "approver@acme.example": (["Fintech shop"], {"triage", "approve"}),
}


@pytest.mark.parametrize("email", list(EXPECTED))
def test_each_demo_person_opens_exactly_the_projects_and_actions_spec_0a_gives(client, email):
    projects, actions = EXPECTED[email]
    headers = login(client, email)
    seen = [row["name"] for row in client.get("/api/projects", headers=headers).json()]
    assert seen == projects
    for name in ALL:
        me = client.get(f"/api/auth/me?project={SLUG[name]}", headers=headers).json()
        held = {action for action in ACTIONS if action in me["user"]["permissions"]}
        assert held == (actions if name in projects else set()), name
        incidents = client.get(f"/api/incidents?project={SLUG[name]}", headers=headers)
        assert incidents.status_code == (200 if name in projects else 403), name


def _pending_fix(project_slug: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Remediation.id).join(Project, Project.id == Remediation.project_id).where(
            Project.slug == project_slug, Remediation.status == "pending",
            Remediation.origin == "sample").order_by(Remediation.id))


def _incident(ref: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Incident.id).where(Incident.ref == ref))


def test_the_server_refuses_what_each_new_demo_person_may_not_do(client):
    approver = login(client, "approver@acme.example")
    health = login(client, "health.engineer@acme.example")
    auditor = login(client, "auditor@acme.example")
    # The Approver approves in the Fintech shop only.
    refused = client.post(f"/api/remediations/{_pending_fix('health-platform')}/approve", headers=approver)
    assert refused.status_code == 403 and "Health platform" in refused.json()["detail"]
    # The health engineer triages there, but cannot approve, and cannot reach the Fintech shop at all.
    assert client.patch(f"/api/incidents/{_incident('INC-2216')}", headers=health, json={}).status_code == 200
    assert client.post(f"/api/remediations/{_pending_fix('health-platform')}/approve",
                       headers=health).status_code == 403
    assert client.patch(f"/api/incidents/{_incident('INC-2208')}", headers=health, json={}).status_code == 403
    # The auditor reads every project and its audit trail, and changes nothing.
    for name in ALL:
        assert client.get(f"/api/projects/audit?project={SLUG[name]}", headers=auditor).status_code == 200
    assert client.patch(f"/api/incidents/{_incident('INC-2217')}", headers=auditor, json={}).status_code == 403
    assert client.post(f"/api/remediations/{_pending_fix('main')}/approve", headers=auditor).status_code == 403


def test_whole_estate_holds_every_open_finding_and_the_account_counts_each_once(client):
    """Other tests add live findings, so this checks the sample rows by set, not the whole count by number."""
    from app.services import posture

    roll = client.get("/api/account/overview", headers=login(client, "auditor@acme.example")).json()
    counts = roll["findings"]["projects"]
    with SessionLocal() as db:
        acme = db.scalar(select(Organization.id).where(Organization.slug == "acme"))  # other companies have a main too
        ids = dict(db.execute(select(Project.slug, Project.id).where(
            Project.org_id == acme, Project.slug.in_(list(counts)))).all())
        products = [ids["main"], ids["health-platform"], ids["saas-with-ai"]]

        def sample_keys(project_ids):
            query = posture._open_signals(project_ids, Signal.resource, Signal.title).where(
                Signal.origin == "sample", Signal.resource != "")
            return db.execute(query).all()

        in_products, in_estate = sample_keys(products), sample_keys([ids["whole-estate"]])
        # The Whole estate holds each open sample finding once, and the same real resource and title as its
        # product project, so the account rule (findings_once) counts the eight of them once, not sixteen times.
        assert len(in_estate) == len(set(in_estate)) == 8 and set(in_estate) == set(in_products)
        assert len(set(in_products + in_estate)) == 8
        assert roll["findings"]["account"] == posture.findings_once(db, list(ids.values()))["account"]
    assert roll["findings"]["account"] < sum(counts.values())
    cards = {row["slug"]: row for row in client.get("/api/projects", headers=login(client)).json()}
    assert {slug: row["open_findings"] for slug, row in cards.items()} == counts


@pytest.fixture
def demo_db(tmp_path):
    """A fresh demo of its own, so findings other tests feed into the shared one do not change the numbers."""
    from app import seed

    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed._demo(db)
        db.commit()
        yield db
    engine.dispose()


def test_the_whole_estate_is_listed_but_not_weighed_a_second_time_in_the_account_score(demo_db):
    from app.services import posture

    org = demo_db.scalar(select(Organization).where(Organization.slug == "acme"))
    projects = list(demo_db.scalars(select(Project).where(Project.org_id == org.id).order_by(Project.name)))
    roll = posture.roll_up(demo_db, org.id, projects)
    rows = {row["slug"]: row for row in roll["projects"]}
    assert not rows["whole-estate"]["in_account_score"]
    products = [rows[slug] for slug in ("main", "health-platform", "saas-with-ai")]
    assert all(row["in_account_score"] for row in products)
    weights = {"high": 3, "medium": 2, "low": 1}
    total = sum(weights[row["criticality"]] for row in products)
    weighted = sum(row["score"] * weights[row["criticality"]] for row in products)
    assert roll["account"]["score"] == (weighted + total // 2) // total
    assert roll["findings"]["account"] == 8


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A database of its own holding the demo as it was seeded before AP.7: one Main, no resources."""
    from app import seed

    engine = create_engine(f"sqlite:///{tmp_path / 'older-demo.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(seed, "shape_demo", lambda db, org, now: None)
    with Session(engine) as db:
        seed._demo(db)
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        org.name = "Acme Fintech (demo)"
        for row in db.scalars(select(Signal).where(Signal.org_id == org.id)):
            row.resource = ""
        db.commit()
    monkeypatch.undo()
    yield engine
    engine.dispose()


def test_a_demo_seeded_before_ap7_is_reshaped_once_on_startup(fresh):
    from app.seed import reshape_older_demo

    with Session(fresh) as db:
        assert [p.name for p in db.scalars(select(Project))] == ["Main"]
        assert reshape_older_demo(db) is True
        assert reshape_older_demo(db) is False
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        assert org.name == "Acme (demo)"
        names = sorted(db.scalars(select(Project.name).where(Project.org_id == org.id)))
        assert names == ALL
        estate = db.scalar(select(Project.id).where(Project.slug == "whole-estate"))
        mirrored = db.scalar(select(func.count(Signal.id)).where(Signal.project_id == estate))
        # Five open fintech findings (the resolved sign-in burst is left out), two health, one SaaS.
        assert mirrored == 8
        people = {u.email: u.id for u in db.scalars(select(User).where(User.org_id == org.id))}
        assert {"health.engineer@acme.example", "auditor@acme.example", "approver@acme.example"} <= set(people)
        grants = db.scalar(select(func.count(ProjectMember.id)).where(
            ProjectMember.user_id == people["ceo@acme.example"]))
        assert grants == 4

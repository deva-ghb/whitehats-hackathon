"""Connections are set up once per account and attached to projects with a scope (checklist AP.3)."""

import pytest
from conftest import PASSWORD, login
from sqlalchemy import select

from app.connectors import PROTOTYPE_CONNECTORS, routing
from app.connectors.base import Discovery
from app.connectors.scope import Resource, check_scope, in_scope, split
from app.db import SessionLocal
from app.models import Agent, Connector, Organization, Project, ProjectMember, Signal, User
from app.projects import ensure_main_project
from app.security import hash_password
from app.services.tenancy import delete_tenant_data
from app.whitehats import dispatch, enable

PAYMENTS = {"tags": {"team": "payments"}}
HEALTH = {"tags": {"team": "health"}}
REPO = "https://github.com/acme/shop"


def _bucket(name: str, team: str) -> dict:
    return {"id": name, "type": "storage.bucket@1", "name": name, "environment": "prod",
            "properties": {"arn": f"arn:aws:s3:::{name}", "account": "509594260364", "region": "ap-south-1",
                           "tags": {"team": team}},
            "provenance": {"kind": "imported", "source": "aws"}}


# ---------------------------------------------------------------- the rules (no database)

def test_a_resource_is_in_scope_only_when_every_named_filter_matches():
    bucket = Resource("arn:aws:s3:::pay", account="1", region="ap-south-1", tags={"team": "payments", "env": "prod"})
    assert in_scope({}, bucket)
    assert in_scope(PAYMENTS, bucket)
    assert in_scope({"regions": ["ap-south-1", "us-east-1"], "tags": {"env": "prod"}}, bucket)
    assert not in_scope(HEALTH, bucket)
    assert not in_scope({"regions": ["us-east-1"]}, bucket)
    assert not in_scope({"accounts": ["2"]}, bucket)
    assert not in_scope({"tags": {"owner": "ada"}}, bucket)


def test_a_scope_names_only_what_its_kind_of_connection_can_narrow():
    assert check_scope("Cloud logs", {"regions": [" us-east-1 ", "us-east-1"], "tags": {"team": "pay"}}) == {
        "regions": ["us-east-1"], "tags": {"team": "pay"}}
    assert check_scope("Code", {"repos": [REPO]}) == {"repos": [REPO]}
    for category, raw, said in (
        ("Code", {"regions": ["us-east-1"]}, "cannot be narrowed by regions"),
        ("Cloud logs", {"regions": "us-east-1"}, "regions must be a list of names"),
        ("Cloud logs", {"tags": ["team"]}, "tags must map each tag name"),
        ("Cloud logs", ["regions"], "a set of named filters"),
        ("Identity", {}, "not attached to projects"),
    ):
        with pytest.raises(ValueError, match=said):
            check_scope(category, raw)


def test_one_discovery_lands_each_resource_only_in_the_projects_that_match():
    found = Discovery(
        nodes=[_bucket("pay-receipts", "payments"), _bucket("health-records", "health")],
        edges=[{"from": "pay-receipts", "to": "health-records", "type": "reads"}],
    )
    shares = split(found, [(1, PAYMENTS), (2, HEALTH), (3, {})])
    assert [node["id"] for node in shares[1].nodes] == ["pay-receipts"]
    assert [node["id"] for node in shares[2].nodes] == ["health-records"]
    assert [node["id"] for node in shares[3].nodes] == ["pay-receipts", "health-records"]
    assert shares[1].edges == [] and shares[2].edges == []
    assert len(shares[3].edges) == 1


# ---------------------------------------------------------------- attached in the database

@pytest.fixture
def account(client):
    with SessionLocal() as db:
        _remove(db)
        org = Organization(name="Scope Co", slug="scopeco", mfa_required=False)
        db.add(org)
        db.flush()
        main = ensure_main_project(db, org)
        side = Project(org_id=org.id, name="Health", slug="health")
        db.add(side)
        db.flush()
        rows = {key: Connector(org_id=org.id, key=key, name=name, category=category) for key, name, category in (
            ("cloudwatch", "AWS CloudWatch Logs", "Cloud logs"), ("github", "GitHub", "Code"),
            ("okta", "Okta", "Identity"))}
        db.add_all(rows.values())
        person = User(org_id=org.id, email="admin@scopeco.test", name="Scope Admin", role="analyst",
                      account_role="account_admin", password_hash=hash_password(PASSWORD),
                      terms_accepted_at=org.created_at)
        engineer = User(org_id=org.id, email="eng@scopeco.test", name="Scope Engineer", role="analyst",
                        password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at)
        db.add_all([person, engineer])
        db.flush()
        db.add(ProjectMember(project_id=main.id, user_id=engineer.id, role="engineer"))
        db.commit()
        ids = {"org": org.id, "main": main.id, "health": side.id, **{key: row.id for key, row in rows.items()}}
    yield ids
    with SessionLocal() as db:
        _remove(db)


def _remove(db) -> None:
    org = db.scalar(select(Organization).where(Organization.slug == "scopeco"))
    if org is not None:
        delete_tenant_data(db, org)
        db.delete(org)
        db.commit()


def test_one_aws_connection_puts_each_resource_only_in_its_project(client, account):
    admin = login(client, "admin@scopeco.test")
    cw = account["cloudwatch"]
    assert client.put(f"/api/connectors/{cw}/scope", headers=admin, json={"scope": PAYMENTS}).status_code == 200
    assert client.put(f"/api/connectors/{cw}/scope?project=health", headers=admin,
                      json={"scope": HEALTH}).status_code == 200
    listed = client.get(f"/api/connectors/{cw}/projects", headers=admin).json()
    assert listed == {"account_wide": False, "projects": [
        {"project": "main", "scope": PAYMENTS}, {"project": "health", "scope": HEALTH}]}
    with SessionLocal() as db:
        attached = routing.attachments(db, db.get(Connector, cw))
    shares = split(Discovery(nodes=[_bucket("pay-receipts", "payments"), _bucket("health-records", "health")],
                             edges=[]), attached)
    assert [node["id"] for node in shares[account["main"]].nodes] == ["pay-receipts"]
    assert [node["id"] for node in shares[account["health"]].nodes] == ["health-records"]


def test_a_push_to_a_shared_repo_is_a_finding_in_both_projects_and_one_for_the_account(client, account):
    admin = login(client, "admin@scopeco.test")
    gh = account["github"]
    for project in ("main", "health"):
        res = client.put(f"/api/connectors/{gh}/scope?project={project}", headers=admin,
                         json={"scope": {"repos": [REPO]}})
        assert res.status_code == 200, res.text
    push = Resource(identity=REPO, repo=REPO)
    with SessionLocal() as db:
        raised = routing.raise_finding(db, db.get(Connector, gh), push, severity="high",
                                       title="A hard-coded key was pushed")
        db.commit()
        assert sorted(row.project_id for row in raised) == sorted([account["main"], account["health"]])
        assert routing.account_finding_count(db, account["org"]) == 1

    assert client.delete(f"/api/connectors/{gh}/scope?project=health", headers=admin).status_code == 200
    with SessionLocal() as db:
        again = routing.raise_finding(db, db.get(Connector, gh), push, severity="high",
                                      title="A second key was pushed")
        db.commit()
        assert [row.project_id for row in again] == [account["main"]]
        kept = db.scalars(select(Signal.title).where(Signal.project_id == account["health"])).all()
        assert kept == ["A hard-coded key was pushed"]  # detaching leaves what the project already holds
        assert routing.account_finding_count(db, account["org"]) == 2


def test_log_lines_reach_only_the_whitehats_of_projects_in_scope(account):
    burst = [{"source": "sshd", "message": f"Failed password for root from 203.0.113.{i}"} for i in range(6)]
    with SessionLocal() as db:
        connector = db.get(Connector, account["cloudwatch"])
        connector.config = {"role_arn": "arn:aws:iam::509594260364:role/reader", "region": "ap-south-1"}
        routing.attach(db, connector, db.get(Project, account["main"]), {"regions": ["ap-south-1"]}, "test")
        routing.attach(db, connector, db.get(Project, account["health"]), {"regions": ["us-east-1"]}, "test")
        for project in ("main", "health"):
            enable(db, account["org"], "identity", project_id=account[project])
        projects = routing.projects_for(db, connector, routing.connection_resource(connector))
        assert projects == [account["main"]]
        dispatch(db, account["org"], "on_log_batch", project_ids=projects, source_host="aws", lines=burst)
        db.commit()
        homes = db.scalars(select(Signal.project_id).join(Agent).where(Agent.key == "identity",
                                                                       Signal.org_id == account["org"])).all()
    assert homes == [account["main"]]


def test_an_account_wide_connection_feeds_every_project_and_cannot_be_attached(client, account):
    admin = login(client, "admin@scopeco.test")
    okta = account["okta"]
    refused = client.put(f"/api/connectors/{okta}/scope", headers=admin, json={"scope": {}})
    assert refused.status_code == 400 and "every project" in refused.json()["detail"]
    listed = client.get(f"/api/connectors/{okta}/projects", headers=admin).json()
    assert listed["account_wide"] is True
    assert {row["project"] for row in listed["projects"]} == {"main", "health"}


def test_attaching_is_checked_refused_and_audited(client, account):
    admin = login(client, "admin@scopeco.test")
    engineer = login(client, "eng@scopeco.test")
    cw = account["cloudwatch"]
    bad = client.put(f"/api/connectors/{cw}/scope", headers=admin, json={"scope": {"repos": ["x"]}})
    assert bad.status_code == 400 and "cannot be narrowed by repos" in bad.json()["detail"]
    assert client.put(f"/api/connectors/{cw}/scope", headers=engineer, json={"scope": {}}).status_code == 403
    assert client.delete(f"/api/connectors/{cw}/scope?project=health", headers=admin).status_code == 404
    assert client.put(f"/api/connectors/{cw}/scope", headers=admin, json={"scope": PAYMENTS}).status_code == 200
    audit = client.get("/api/admin/audit", headers=admin).json()
    assert any(row["action"] == "attached connection" and "Main" in row["detail"] for row in audit)


def test_a_new_connection_starts_attached_to_main_with_everything_in_scope(client):
    assert client
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        slugs = dict(db.execute(select(Project.id, Project.slug).where(Project.org_id == org.id)).all())
        # Account-wide connections reach every demo project. Every other one starts in Main with everything;
        # the demo also attaches the ones the prototype reads to the Whole estate (AP.7).
        for row in db.scalars(select(Connector).where(Connector.org_id == org.id)):
            reached = {slugs[pid]: scope for pid, scope in routing.attachments(db, row)}
            if routing.is_account_wide(row):
                assert reached == {slug: {} for slug in slugs.values()}, row.key
            elif row.key in PROTOTYPE_CONNECTORS:
                assert reached == {"main": {}, "whole-estate": {}}, row.key
            else:
                assert reached == {"main": {}}, row.key

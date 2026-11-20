"""Account home and project settings (checklist AP.4).

Creates land in the demo company. The test deletes them before it returns, so later tests
still see Main alone. Archiving would hide a project and leave the row behind.
"""

from conftest import login
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    AccessPath,
    Agent,
    AuditLog,
    Clone,
    CloneVersion,
    CredentialRef,
    Edge,
    Group,
    Node,
    Project,
    ProjectConnection,
    ProjectMember,
    SensePoint,
)
from app.services.clones import list_clones


def _remove(client, ceo, slug):
    """Archive through the API, then delete the row and everything that points at it."""
    client.post(f"/api/projects/{slug}/archive?project={slug}", headers=ceo, json={"confirm": True})
    with SessionLocal() as db:
        project = db.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            return
        pid = project.id
        for model in (Node, Edge, Group, AccessPath, CredentialRef, SensePoint, Agent):
            db.execute(delete(model).where(model.project_id == pid))
        db.execute(delete(CloneVersion).where(CloneVersion.project_id == pid))
        db.execute(delete(Clone).where(Clone.project_id == pid))
        db.execute(delete(AuditLog).where(AuditLog.project_id == pid))
        db.execute(delete(ProjectMember).where(ProjectMember.project_id == pid))
        db.execute(delete(ProjectConnection).where(ProjectConnection.project_id == pid))
        db.delete(project)
        db.commit()


def test_three_kinds_of_project_and_a_viewer_who_cannot_approve(client, ceo):
    slugs = []
    try:
        empty = client.post("/api/projects", headers=ceo, json={"name": "Quiet desk", "kind": "empty"}).json()
        slugs.append(empty["slug"])
        assert empty["slug"] == "quiet-desk"
        assert empty["open_findings"] == 0
        assert empty["connection_health"] == "none"
        assert empty["score_measured"] is False
        with SessionLocal() as db:
            # It starts with the company's WhiteHats, so a connection's data reaches someone from day one.
            keys = set(db.scalars(select(Agent.key).join(Project, Project.id == Agent.project_id)
                                  .where(Project.slug == "quiet-desk")))
            assert "cloud" in keys

        main = client.get("/api/overview", headers=ceo).json()
        assert main["kpis"]["open_incidents"] > 0
        quiet = client.get("/api/overview?project=quiet-desk", headers=ceo).json()
        assert quiet["kpis"]["open_incidents"] == 0
        assert quiet["kpis"]["open_network_findings"] == 0

        health = client.post("/api/projects", headers=ceo, json={
            "name": "Health desk", "kind": "template", "template": "health",
        })
        assert health.status_code == 200, health.text
        slugs.append(health.json()["slug"])
        with SessionLocal() as db:
            project = db.scalar(select(Project).where(Project.slug == health.json()["slug"]))
            clones = list_clones(db, project.org_id, project.id)
        assert clones and clones[0]["clone"] == "preventive-health"

        connectors = client.get("/api/connectors", headers=ceo).json()
        cloud = next(row for row in connectors if row["category"] == "Cloud logs")
        scoped = client.post("/api/projects", headers=ceo, json={
            "name": "Scoped desk", "kind": "connection", "connector_id": cloud["id"],
            "scope": {"regions": ["ap-southeast-2"]},
        })
        assert scoped.status_code == 200, scoped.text
        scoped_slug = scoped.json()["slug"]
        slugs.append(scoped_slug)
        settings = client.get(f"/api/projects/{scoped_slug}/settings?project={scoped_slug}", headers=ceo).json()
        assert settings["connections"][0]["scope"]["regions"] == ["ap-southeast-2"]
        assert settings["can"]["manage_members"] is True

        invited = client.post(f"/api/projects/{empty['slug']}/members?project={empty['slug']}", headers=ceo,
                              json={"email": "analyst@acme.example", "role": "viewer", "reason": "Reads this project"})
        assert invited.status_code == 200, invited.text
        assert any(row["email"] == "analyst@acme.example" and row["role"] == "viewer"
                   for row in invited.json()["members"])

        analyst = login(client, "analyst@acme.example")
        mine = {row["slug"] for row in client.get("/api/projects", headers=analyst).json()}
        assert empty["slug"] in mine and scoped_slug not in mine
        person = client.get(f"/api/auth/me?project={empty['slug']}", headers=analyst).json()["user"]
        assert "approve" not in person["permissions"]
        assert "see_project" in person["permissions"]
        refused = client.get(f"/api/overview?project={scoped_slug}", headers=analyst)
        assert refused.status_code == 403
    finally:
        for slug in slugs:
            _remove(client, ceo, slug)


def test_renaming_and_two_approver_review(client, ceo):
    created = client.post("/api/projects", headers=ceo, json={
        "name": "Rename me", "kind": "empty", "criticality": "low",
    }).json()
    slug = created["slug"]
    try:
        renamed = client.patch(f"/api/projects/{slug}", headers=ceo, json={
            "name": "Renamed desk", "criticality": "high", "environments": ["production", "staging"],
        })
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Renamed desk"
        assert renamed.json()["criticality"] == "high"
        policy = client.patch(f"/api/projects/{slug}/policy?project={slug}", headers=ceo, json={"two_approver": True})
        assert policy.status_code == 200, policy.text
        assert policy.json()["two_approver"] is True
        home = client.get("/api/projects", headers=ceo).json()
        row = next(item for item in home if item["slug"] == slug)
        assert row["criticality"] == "high"
        assert {"slug", "score", "open_findings", "connection_health", "last_activity"} <= set(row)
    finally:
        _remove(client, ceo, slug)


def test_a_viewer_cannot_open_a_project_they_were_not_given(client, ceo):
    """The home lists only projects the person can see, and Main's incidents stay on Main."""
    created = client.post("/api/projects", headers=ceo, json={"name": "Hidden desk", "kind": "empty"}).json()
    try:
        with SessionLocal() as db:
            project = db.scalar(select(Project).where(Project.slug == created["slug"]))
            assert db.scalar(select(Clone.id).where(Clone.project_id == project.id)) is None
        analyst = login(client, "analyst@acme.example")
        assert created["slug"] not in {row["slug"] for row in client.get("/api/projects", headers=analyst).json()}
    finally:
        _remove(client, ceo, created["slug"])

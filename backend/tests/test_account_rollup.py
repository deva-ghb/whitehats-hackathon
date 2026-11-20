"""Account roll-up (checklist AP.5).

The account score weights each project's score by criticality. A finding on one
resource counts once for the account, however many projects hold it.
"""

from conftest import login
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Agent, AuditLog, Incident, Project, ProjectConnection, ProjectMember, Signal

WEIGHTS = {"high": 3, "medium": 2, "low": 1}


def _remove(client, ceo, slug):
    client.post(f"/api/projects/{slug}/archive?project={slug}", headers=ceo, json={"confirm": True})
    with SessionLocal() as db:
        project = db.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            return
        pid = project.id
        db.execute(delete(Signal).where(Signal.project_id == pid))
        db.execute(delete(Agent).where(Agent.project_id == pid))
        db.execute(delete(Incident).where(Incident.project_id == pid))
        db.execute(delete(AuditLog).where(AuditLog.project_id == pid))
        db.execute(delete(ProjectMember).where(ProjectMember.project_id == pid))
        db.execute(delete(ProjectConnection).where(ProjectConnection.project_id == pid))
        db.delete(project)
        db.commit()


def test_weighted_score_names_the_lowest_project_and_counts_a_shared_finding_once(client, ceo):
    slugs = []
    try:
        main_before = client.get("/api/overview", headers=ceo).json()["posture"]["score"]
        before = client.get("/api/account/overview", headers=ceo).json()
        high = client.post("/api/projects", headers=ceo, json={
            "name": "Payments", "kind": "empty", "criticality": "high",
        }).json()
        low = client.post("/api/projects", headers=ceo, json={
            "name": "Sandbox", "kind": "empty", "criticality": "low",
        }).json()
        slugs.extend([high["slug"], low["slug"]])
        with SessionLocal() as db:
            payments = db.scalar(select(Project).where(Project.slug == high["slug"]))
            sandbox = db.scalar(select(Project).where(Project.slug == low["slug"]))
            db.add(Incident(org_id=payments.org_id, project_id=payments.id, ref="AP5-PAY",
                            title="Payments outage", severity="critical", status="open"))
            shared = {"severity": "low", "title": "Open bucket", "resource": "arn:aws:s3:::shared-bucket"}
            db.add(Signal(org_id=payments.org_id, project_id=payments.id, **shared))
            db.add(Signal(org_id=sandbox.org_id, project_id=sandbox.id, **shared))
            db.add(Signal(org_id=payments.org_id, project_id=payments.id, severity="low",
                          title="Payments only", resource="arn:aws:s3:::payments-only"))
            db.commit()

        assert client.get("/api/overview", headers=ceo).json()["posture"]["score"] == main_before
        payments_score = client.get(f"/api/overview?project={high['slug']}", headers=ceo).json()["posture"]["score"]
        sandbox_score = client.get(f"/api/overview?project={low['slug']}", headers=ceo).json()["posture"]["score"]
        assert payments_score == sandbox_score - 90

        rolled = client.get("/api/account/overview", headers=ceo).json()
        by_slug = {row["slug"]: row for row in rolled["projects"]}
        assert by_slug[high["slug"]]["score"] == payments_score
        assert by_slug[low["slug"]]["score"] == sandbox_score
        # Sandbox only shares a resource with Payments, so both count; the demo's Whole estate is a union view.
        assert {row["slug"] for row in rolled["projects"] if not row["in_account_score"]} == {"whole-estate"}
        counted = [row for row in rolled["projects"] if row["in_account_score"]]
        total = sum(WEIGHTS[row["criticality"]] for row in counted)
        weighted = sum(row["score"] * WEIGHTS[row["criticality"]] for row in counted)
        assert rolled["account"]["score"] == (weighted + total // 2) // total
        lowest = min(rolled["projects"], key=lambda row: (row["score"], row["name"]))
        assert rolled["lowest"] == {"slug": lowest["slug"], "name": lowest["name"], "score": lowest["score"]}

        assert rolled["findings"]["account"] == before["findings"]["account"] + 2
        assert rolled["findings"]["projects"][high["slug"]] == 2
        assert rolled["findings"]["projects"][low["slug"]] == 1

        report = client.get(f"/api/reports/board?project={high['slug']}", headers=ceo).json()
        assert report["facts"]["posture"]["score"] == payments_score
        assert report["facts"]["lowest"] is None
        account = client.get("/api/reports/board?account=true", headers=ceo).json()
        assert account["facts"]["posture"]["score"] == rolled["account"]["score"]
        assert account["facts"]["lowest"]["slug"] == rolled["lowest"]["slug"]
        assert account["facts"]["findings_once"] == rolled["findings"]["account"]
        assert account["narrative"]
    finally:
        for slug in slugs:
            _remove(client, ceo, slug)


def test_a_viewer_sees_the_roll_up_of_only_their_projects(client, ceo):
    quiet = client.post("/api/projects", headers=ceo, json={"name": "Quiet rollup", "kind": "empty"}).json()
    hidden = client.post("/api/projects", headers=ceo, json={"name": "Hidden rollup", "kind": "empty"}).json()
    try:
        invited = client.post(f"/api/projects/{quiet['slug']}/members?project={quiet['slug']}", headers=ceo,
                              json={"email": "analyst@acme.example", "role": "viewer", "reason": "Reads this project"})
        assert invited.status_code == 200, invited.text
        analyst = login(client, "analyst@acme.example")
        slugs = {row["slug"] for row in client.get("/api/account/overview", headers=analyst).json()["projects"]}
        assert quiet["slug"] in slugs and hidden["slug"] not in slugs
    finally:
        _remove(client, ceo, quiet["slug"])
        _remove(client, ceo, hidden["slug"])

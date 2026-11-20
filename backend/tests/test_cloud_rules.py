"""KS.2: a business rule for the cloud, stated by an approver, judged against the map."""

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    Clone,
    CloneVersion,
    Node,
    Organization,
    Project,
    ProjectMember,
    Signal,
    SphereDecision,
    SphereVersion,
    User,
)
from app.services import cloud_rules


def _map(db, project, nodes):
    clone = Clone(org_id=project.org_id, project_id=project.id, slug="rules-test", environment="prod", library_version="1")
    db.add(clone)
    db.flush()
    version = CloneVersion(org_id=project.org_id, project_id=project.id, clone_id=clone.id, number=1, author="t",
                           reason="t", environment="prod", library_version="1")
    db.add(version)
    db.flush()
    for key, kind, name, props in nodes:
        db.add(Node(org_id=project.org_id, project_id=project.id, clone_version_id=version.id, node_key=key,
                    type=f"{kind}@1", name=name, environment="prod", properties=props))
    db.commit()
    return version


def test_the_judgement_keeps_breaks_or_does_not_know():
    assert cloud_rules.judge(10, ">=", 50) is False and cloud_rules.judge("80", ">=", 50) is True
    assert cloud_rules.judge(None, ">=", 50) is None and cloud_rules.judge("fast", "<=", 5) is None
    assert cloud_rules.judge("set", "set", None) and cloud_rules.judge("none", "set", None) is False
    assert cloud_rules.judge("Private", "==", "private") and cloud_rules.judge("x", "!=", "x") is False
    assert cloud_rules.problems({"on": [], "property": "", "op": "~"}) == [
        "Say which things on the map the rule is about.", "Say which setting the rule is about.",
        "The comparison must be one of >=, <=, ==, !=, set, unset."]


def test_an_approver_states_a_rule_the_map_breaks_it_and_putting_it_right_closes_the_finding(client, ceo):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = Project(org_id=org.id, name="Rules test", slug="rules-test")  # its own: other tests draw maps too
        db.add(project)
        db.flush()
        ceo_user = db.scalar(select(User).where(User.email == "ceo@acme.example"))
        db.add(ProjectMember(project_id=project.id, user_id=ceo_user.id, role="approver"))
        db.commit()
        pid, oid = project.id, org.id
        version = _map(db, project, [
            ("api", "edge.api-gateway", "payments api", {"arn": "arn:aws:apigateway:x::/apis/a1", "throttling_rps": 10}),
            ("api2", "edge.api-gateway", "admin api", {"arn": "arn:aws:apigateway:x::/apis/a2"}),
            ("bucket", "data.s3", "receipts", {})])
    try:
        settings = client.get("/api/sphere/cloud-settings?project=rules-test", headers=ceo).json()
        gateway = next(k for k in settings if k["type"] == "edge.api-gateway")
        assert "throttling_rps" in gateway["settings"] and "arn" not in gateway["settings"]
        assert {"payments api", "admin api"} <= {t["name"] for t in gateway["things"]}
        assert gateway["label"] == "Amazon API Gateway"
        bad = client.post("/api/sphere/cloud-rules?project=rules-test", headers=ceo, json={
            "text": "The payment API allows enough requests", "expect": {"on": ["edge.api-gateway"],
                                                                       "property": "throttling_rps", "op": ">="}})
        assert bad.status_code == 422 and "need a number" in bad.json()["detail"]
        done = client.post("/api/sphere/cloud-rules?project=rules-test", headers=ceo, json={
            "text": "The payment API allows at least 50 requests a second", "concept": "Payment", "protects": "money",
            "expect": {"on": ["edge.api-gateway"], "property": "throttling_rps", "op": ">=", "value": 50}}).json()
        assert done["status"] == "done"
        with SessionLocal() as db:
            open_ = [(s.title, s.severity, s.resource) for s in db.scalars(select(Signal).where(
                Signal.project_id == pid, Signal.fingerprint.like("rule-%"), Signal.closed_at.is_(None)))]
            # the admin API's rate is not on the map: unknown, not a breach
            assert open_ == [("Breaks a business rule: The payment API allows at least 50 requests a second", "high",
                              "arn:aws:apigateway:x::/apis/a1")]
            node = db.scalar(select(Node).where(Node.clone_version_id == version.id, Node.node_key == "api"))
            node.properties = {**node.properties, "throttling_rps": 80}
            db.commit()
            assert cloud_rules.evaluate(db, oid, pid)["closed"] == 1
        sphere = client.get("/api/sphere?project=rules-test", headers=ceo).json()
        rule = next(n for n in sphere["nodes"] if n.get("expect"))
        assert rule["status"] == "known" and rule["key"].startswith("rule:cloud-")
        gone = client.post("/api/sphere/cloud-rules/forget?project=rules-test", headers=ceo, json={"keys": [rule["key"]]}).json()
        assert gone["status"] == "done"
        assert not [n for n in client.get("/api/sphere?project=rules-test", headers=ceo).json()["nodes"] if n.get("expect")]
    finally:
        with SessionLocal() as db:
            db.execute(delete(Signal).where(Signal.project_id == pid, Signal.fingerprint.like("rule-%")))
            db.execute(delete(SphereVersion).where(SphereVersion.project_id == pid))
            db.execute(delete(SphereDecision).where(SphereDecision.project_id == pid))
            clone = db.scalar(select(Clone).where(Clone.project_id == pid, Clone.slug == "rules-test"))
            db.execute(delete(Node).where(Node.clone_version_id == version.id))
            db.execute(delete(CloneVersion).where(CloneVersion.clone_id == clone.id))
            db.delete(clone)
            for row in db.scalars(select(Signal).where(Signal.project_id == pid)):
                db.delete(row)
            from app.models import Incident, IncidentEvent
            for inc in db.scalars(select(Incident).where(Incident.project_id == pid)):
                db.execute(delete(IncidentEvent).where(IncidentEvent.incident_id == inc.id))
                db.delete(inc)
            db.flush()
            from app.db import Base
            for table in reversed(Base.metadata.sorted_tables):  # whatever else the project gathered (agents, audit)
                if "project_id" in table.c and table.name != "projects":
                    db.execute(table.delete().where(table.c.project_id == pid))
            db.delete(db.get(Project, pid))
            db.commit()

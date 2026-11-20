"""BH.2: Ask Big Hat reads what the business depends on (the knowledge sphere) and what security audits found."""

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Organization, Project, Signal, SphereVersion
from app.services import briefing


def test_big_hat_is_given_the_sphere_rules_and_the_open_audit_findings(client, ceo):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id).order_by(Project.id))
        db.add(SphereVersion(org_id=org.id, project_id=project.id, number=99, actor="t", reason="t", ops=[
            {"op": "put_node", "node": {"key": "concept:order", "kind": "Concept", "status": "known", "text": "Order"}},
            {"op": "put_node", "node": {"key": "rule:refund", "kind": "Rule", "status": "known", "protects": "money",
                                        "text": "A refund may never exceed what was paid."}},
            {"op": "put_node", "node": {"key": "rule:hint", "kind": "Rule", "status": "proposed",
                                        "text": "An order is created only for a valid customer."}}]))
        db.add(Signal(org_id=org.id, project_id=project.id, severity="high", title="Refund amount is not checked",
                      source="audit", audit_id=7))
        db.add(Signal(org_id=org.id, project_id=project.id, severity="high", title="Closed audit finding",
                      source="audit", audit_id=7, closed_reason="fixed",
                      closed_at=__import__("app.db", fromlist=["utcnow"]).utcnow()))
        db.commit()
        try:
            sphere, audit = briefing.knowledge_sections(db, org.id, briefing.one_project(project.id, account_data=True))
            hint = "- Proposed rule, not yet confirmed: An order is created only for a valid customer."
            assert sphere.text.splitlines()[:2] == [
                "- Known rule, protects money: A refund may never exceed what was paid.", hint]
            assert "Refund amount is not checked" in audit.text and "Closed audit finding" not in audit.text
            assert briefing.knowledge_sections(db, org.id, briefing.WHOLE_ACCOUNT) == []
            answer = client.post("/api/assistant/ask", json={"question": "Can refunds go wrong?"}, headers=ceo).json()
            assert {"What the business depends on (knowledge sphere)",
                    "Open security audit findings"} <= set(answer["meta"]["included"])
        finally:
            db.execute(delete(SphereVersion).where(SphereVersion.number == 99))
            db.execute(delete(Signal).where(Signal.audit_id == 7, Signal.project_id == project.id))
            db.commit()

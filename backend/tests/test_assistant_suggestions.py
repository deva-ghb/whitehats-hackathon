"""Suggested questions follow the company's records, and the offline answer lists those facts."""

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    Agent,
    AuditLog,
    ChatMessage,
    Incident,
    IncidentEvent,
    LLMCall,
    Organization,
    PostureSnapshot,
    User,
)
from app.security import hash_password


def _wipe(slug: str) -> None:
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == slug))
        if org is None:
            return
        incident_ids = list(db.scalars(select(Incident.id).where(Incident.org_id == org.id)))
        if incident_ids:
            db.execute(delete(IncidentEvent).where(IncidentEvent.incident_id.in_(incident_ids)))
        db.execute(delete(Incident).where(Incident.org_id == org.id))
        db.execute(delete(ChatMessage).where(ChatMessage.org_id == org.id))
        db.execute(delete(LLMCall).where(LLMCall.org_id == org.id))
        db.execute(delete(Agent).where(Agent.org_id == org.id))
        db.execute(delete(AuditLog).where(AuditLog.org_id == org.id))
        db.execute(delete(PostureSnapshot).where(PostureSnapshot.org_id == org.id))
        db.execute(delete(User).where(User.org_id == org.id))
        db.delete(org)
        db.commit()


def test_briefing_names_whitehats_waiting_for_data(client):
    from conftest import PASSWORD, add_person, login

    from app.projects import ensure_main_project
    from app.services.briefing import WHOLE_ACCOUNT, sections
    from app.whitehats import CATALOG, enable

    _wipe("waitco")
    with SessionLocal() as db:
        org = Organization(name="Wait Co", slug="waitco", mfa_required=False)
        db.add(org)
        db.flush()
        ensure_main_project(db, org)
        add_person(db, User(org_id=org.id, email="ceo@waitco.test", name="Wait CEO", role="ceo",
                    password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at))
        for key, hat in CATALOG.items():
            if hat.available:
                enable(db, org.id, key)
        db.commit()
    try:
        headers = login(client, "ceo@waitco.test")
        summary = client.get("/api/overview/summary?refresh=true", headers=headers).json()
        with SessionLocal() as db:
            org_id = db.scalar(select(Organization.id).where(Organization.slug == "waitco"))
            packed = " ".join(section.text for section in sections(db, org_id, WHOLE_ACCOUNT))
        text = summary["text"]
        for name in ("Identity", "Endpoint", "Network", "Cloud Posture", "Insider Threat"):
            assert name in packed, name
            assert name in text, name
        assert "Waiting for data:" in packed
        assert "Waiting for data:" in text
        assert "Dark WhiteHats" not in packed
        assert "No WhiteHat is dark" not in text
    finally:
        _wipe("waitco")


def test_suggestions_follow_the_company_and_skip_incidents_when_there_are_none(client):
    from conftest import PASSWORD, add_person, login

    _wipe("quietco")
    with SessionLocal() as db:
        org = Organization(name="Quiet Co", slug="quietco", mfa_required=False)
        db.add(org)
        db.flush()
        add_person(db, User(org_id=org.id, email="ceo@quietco.test", name="Quiet CEO", role="ceo",
                    password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at))
        db.commit()
    try:
        headers = login(client, "ceo@quietco.test")
        empty = client.get("/api/assistant/suggestions", headers=headers).json()
        assert empty
        blob = " ".join(empty).lower()
        assert "incident" not in blob
        assert "inc-" not in blob

        with SessionLocal() as db:
            org_id = db.scalar(select(Organization.id).where(Organization.slug == "quietco"))
            db.add(Agent(org_id=org_id, key="network", name="Network", domain="ports", status="dark"))
            db.commit()
        dark = client.get("/api/assistant/suggestions", headers=headers).json()
        assert any("Network" in item for item in dark)
        assert "incident" not in " ".join(dark).lower()
        assert dark != empty

        with SessionLocal() as db:
            org_id = db.scalar(select(Organization.id).where(Organization.slug == "quietco"))
            db.add(Incident(org_id=org_id, ref="INC-9001", title="A quiet finding", severity="high", status="open"))
            db.commit()
        with_incident = client.get("/api/assistant/suggestions", headers=headers).json()
        assert any("INC-9001" in item for item in with_incident)
        assert with_incident != dark

        answer = client.post("/api/assistant/ask", headers=headers, json={"question": "What is going on?"}).json()
        assert answer["meta"]["fallback"] is True
        assert "No model is connected" in answer["text"]
        assert "INC-9001" in answer["text"]
        assert "Posture" in answer["text"]
        assert "Not watching: Network." in answer["text"]
        assert "No WhiteHat is dark" not in answer["text"]
    finally:
        _wipe("quietco")

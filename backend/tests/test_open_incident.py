"""An incident is opened by a person from signals they choose. Nothing groups them on its own."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Incident, IncidentEvent, Organization, Signal


def test_a_person_opens_an_incident_from_chosen_signals(client, ceo):
    with SessionLocal() as db:
        org_id = db.scalar(select(Organization.id).where(Organization.slug == "acme"))
        rows = [
            Signal(org_id=org_id, severity="high", title="Hand opened signal one", origin="sample",
                   source="whitehat:network", detail="Seen in the test."),
            Signal(org_id=org_id, severity="medium", title="Hand opened signal two", origin="live",
                   source="whitehat:endpoint", detail="Seen in the test."),
        ]
        db.add_all(rows)
        db.commit()
        ids = [row.id for row in rows]
    created_id = None
    try:
        short = client.post("/api/incidents", headers=ceo, json={
            "title": "short", "severity": "high", "signal_ids": ids,
        })
        assert short.status_code == 400
        missing = client.post("/api/incidents", headers=ceo, json={
            "title": "A finding that is not in this company", "severity": "high", "signal_ids": [999999],
        })
        assert missing.status_code == 404
        listed = client.get("/api/signals", headers=ceo).json()
        taken = next(row["id"] for row in listed if row["incident_id"])
        busy = client.post("/api/incidents", headers=ceo, json={
            "title": "Should not take a signal that is taken", "severity": "low", "signal_ids": [taken],
        })
        assert busy.status_code == 409
        opened = client.post("/api/incidents", headers=ceo, json={
            "title": "Two findings opened by hand", "severity": "high", "signal_ids": ids,
        })
        assert opened.status_code == 200, opened.text
        body = opened.json()
        created_id = body["id"]
        assert body["origin"] == "live"
        assert body["status"] == "open"
        assert {row["id"] for row in body["signals"]} == set(ids)
        assert any(event["text"].startswith("Opened by hand from 2 signal") for event in body["timeline"])
        again = client.post("/api/incidents", headers=ceo, json={
            "title": "Those signals are already linked", "severity": "high", "signal_ids": ids,
        })
        assert again.status_code == 409
        free = client.get("/api/signals?unlinked=1&days=30", headers=ceo).json()
        assert all(row["id"] not in ids for row in free)
    finally:
        with SessionLocal() as db:
            db.query(Signal).filter(Signal.id.in_(ids)).delete()
            if created_id:
                ref = db.get(Incident, created_id).ref
                db.query(IncidentEvent).filter(IncidentEvent.incident_id == created_id).delete()
                db.query(AuditLog).filter(AuditLog.target == ref, AuditLog.action == "opened incident").delete()
                db.query(Incident).filter(Incident.id == created_id).delete()
            db.commit()


def _signal(title: str) -> int:
    with SessionLocal() as db:
        org_id = db.scalar(select(Organization.id).where(Organization.slug == "acme"))
        row = Signal(org_id=org_id, severity="high", title=title, origin="live", source="whitehat:network")
        db.add(row)
        db.commit()
        return row.id


def _forget(incident_id: int | None, signal_id: int) -> None:
    with SessionLocal() as db:
        db.query(Signal).filter(Signal.id == signal_id).delete()
        if incident_id:
            ref = db.get(Incident, incident_id).ref
            db.query(IncidentEvent).filter(IncidentEvent.incident_id == incident_id).delete()
            db.query(AuditLog).filter(AuditLog.target == ref, AuditLog.action == "opened incident").delete()
            db.query(Incident).filter(Incident.id == incident_id).delete()
        db.commit()


def test_a_reference_taken_at_the_same_moment_moves_to_the_next_number(client, ceo, monkeypatch):
    from app import incident_refs

    real_next_ref = incident_refs.taken_next
    picks = []

    def raced(db, org_id):
        # The first pick is a number another request has just used.
        picks.append("INC-2208" if not picks else real_next_ref(db, org_id))
        return picks[-1]

    monkeypatch.setattr(incident_refs, "taken_next", raced)
    signal_id, created = _signal("Raced reference signal"), None
    try:
        opened = client.post("/api/incidents", headers=ceo, json={
            "title": "Opened while another incident took its number", "severity": "high", "signal_ids": [signal_id],
        })
        assert opened.status_code == 200, opened.text
        created = opened.json()["id"]
        assert opened.json()["ref"] == picks[-1] != "INC-2208"
        refs = client.get("/api/incidents", headers=ceo).json()
        assert sum(1 for row in refs if row["ref"] == "INC-2208") == 1
    finally:
        _forget(created, signal_id)


def test_the_database_refuses_a_second_incident_with_the_same_reference():
    import pytest
    from sqlalchemy.exc import IntegrityError

    with SessionLocal() as db:
        org_id = db.scalar(select(Organization.id).where(Organization.slug == "acme"))
        db.add(Incident(org_id=org_id, ref="INC-2208", title="A copy of the reference", severity="low"))
        with pytest.raises(IntegrityError):
            db.commit()

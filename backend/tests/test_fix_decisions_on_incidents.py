"""Deciding a fix that belongs to an incident writes the decision on the incident's timeline."""

import pytest
from sqlalchemy import select
from test_factory_outcomes import _fix


@pytest.fixture
def two_approvers():
    from app.db import SessionLocal
    from app.models import Organization, Project

    def main(db):
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        return db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))

    with SessionLocal() as db:
        main(db).two_approver = True
        db.commit()
    yield
    with SessionLocal() as db:
        main(db).two_approver = False
        db.commit()


def _timeline(client, ceo, iid):
    return [e["text"] for e in client.get(f"/api/incidents/{iid}", headers=ceo).json()["timeline"]]


def test_rejecting_a_fix_on_an_incident_records_why(client, ceo, incident):
    rid = _fix(incident, "Reject this fix")
    res = client.post(f"/api/remediations/{rid}/reject", headers=ceo, json={"reason": "Needs a maintenance window"})
    assert res.status_code == 200, res.text
    assert "Rejected: Reject this fix. Needs a maintenance window" in _timeline(client, ceo, incident)


def test_the_first_of_two_approvals_on_an_incident_is_recorded(client, ceo, incident, two_approvers):
    rid = _fix(incident, "Critical fix on an incident", risk="critical")
    res = client.post(f"/api/remediations/{rid}/approve", headers=ceo)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "pending"
    assert ("First approval: Critical fix on an incident. A second Approver must decide."
            in _timeline(client, ceo, incident))

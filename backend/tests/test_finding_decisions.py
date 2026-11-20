"""FD.1-3: an approver calls a finding a false positive or accepts its risk; a person says something is fixed and
WhiteHats checks again before anything is resolved."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import login
from sqlalchemy import delete, select

from app.db import SessionLocal, utcnow
from app.models import (
    FindingDecision,
    Incident,
    IncidentEvent,
    Organization,
    Project,
    Remediation,
    Signal,
    SphereDecision,
)
from app.services import finding_decisions, verify


@pytest.fixture
def place(client):
    """Acme's main project; everything made here is removed afterwards."""
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
        made = {"signals": [], "incidents": [], "fixes": []}
        yield db, org, project, made
        db.rollback()
        db.execute(delete(FindingDecision).where(FindingDecision.project_id == project.id))
        db.execute(delete(SphereDecision).where(SphereDecision.project_id == project.id,
                                                SphereDecision.kind.in_(("decided", "reversed"))))
        db.execute(delete(Remediation).where(Remediation.id.in_(made["fixes"])))
        db.execute(delete(Signal).where(Signal.id.in_(made["signals"]) | Signal.fingerprint.like("fd-test-%")))
        db.execute(delete(IncidentEvent).where(IncidentEvent.incident_id.in_(made["incidents"])))
        db.execute(delete(Incident).where(Incident.id.in_(made["incidents"])))
        db.commit()


def _signal(db, org, project, made, fingerprint="fd-test-1", source="whitehats:code", **kw):
    s = Signal(org_id=org.id, project_id=project.id, severity="high", title=kw.pop("title", "A secret in settings"),
               entity="shop/settings.py:3", source=source, fingerprint=fingerprint, origin="live", **kw)
    db.add(s)
    db.commit()
    made["signals"].append(s.id)
    return s


def test_a_false_positive_closes_its_twins_and_is_never_raised_again_until_withdrawn(place, client):
    db, org, project, made = place
    first = _signal(db, org, project, made)
    twin = _signal(db, org, project, made)  # the same finding, raised again on another branch
    approver = login(client, "ceo@acme.example")
    refused = client.post(f"/api/signals/{first.id}/false-positive?project=main", headers=approver,
                          json={"reason": "short"})
    assert refused.status_code == 422
    done = client.post(f"/api/signals/{first.id}/false-positive?project=main", headers=approver,
                       json={"reason": "It is a test fixture key, never used anywhere."})
    assert done.status_code == 200, done.text
    for s in (first, twin):
        db.refresh(s)
        assert s.closed_at is not None and s.closed_reason == "false positive"
    again = _signal(db, org, project, made)  # any detector raising it again: saved closed, alerting no one
    assert again.closed_at is not None and again.closed_reason == "false positive"
    other = _signal(db, org, project, made, fingerprint="fd-test-2")
    assert other.closed_at is None  # a different finding is not touched
    assert db.scalar(select(SphereDecision.id).where(SphereDecision.project_id == project.id,
                                                     SphereDecision.kind == "decided")) is not None
    counts = finding_decisions.precision(db, org.id, project.id)["whitehats:code"]
    assert counts["false_positives"] >= 1 and counts["raised"] >= 4

    decision = db.get(FindingDecision, done.json()["decision"])
    assert finding_decisions.withdraw(db, decision, by="Anita Rao") >= 3
    db.commit()
    db.refresh(first)
    assert first.closed_at is None  # it applies no more: the finding is open again
    assert _signal(db, org, project, made).closed_at is None


def test_an_accepted_risk_closes_the_finding_until_its_date_then_asks_again(place, client):
    db, org, project, made = place
    s = _signal(db, org, project, made)
    fix = Remediation(org_id=org.id, project_id=project.id, signal_id=s.id, title="Rotate the key", target="shop",
                      action="Rotate it.", destination="Fix Factory", risk="high")
    db.add(fix)
    db.commit()
    made["fixes"].append(fix.id)
    approver = login(client, "ceo@acme.example")
    past = client.post(f"/api/remediations/{fix.id}/accept-risk?project=main", headers=approver,
                       json={"reason": "The key is rotated next sprint.", "until": str(date.today() - timedelta(days=2))})
    assert past.status_code == 422
    accepted = client.post(f"/api/remediations/{fix.id}/accept-risk?project=main", headers=approver,
                           json={"reason": "The key is rotated next sprint.", "until": str(date.today() + timedelta(days=7))})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    db.refresh(s)
    assert s.closed_reason == "risk accepted"
    decision = db.scalar(select(FindingDecision).where(FindingDecision.signal_id == s.id))
    decision.until = utcnow() - timedelta(minutes=1)
    db.commit()
    assert finding_decisions.expire_due(db) == 1
    db.refresh(s)
    db.refresh(fix)
    assert s.closed_at is None and fix.status == "pending" and "accepted risk ended" in fix.result


def test_a_whole_incident_called_a_false_positive_closes_with_its_findings(place, client):
    db, org, project, made = place
    inc = Incident(org_id=org.id, project_id=project.id, ref="INC-9801", title="A false alarm", severity="high",
                   status="investigating")
    db.add(inc)
    db.commit()
    made["incidents"].append(inc.id)
    s = _signal(db, org, project, made, incident_id=inc.id)
    done = client.post(f"/api/incidents/{inc.id}/false-positive?project=main", headers=login(client, "ceo@acme.example"),
                       json={"reason": "The scanner read a test file."})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "false_positive"
    db.refresh(s)
    assert s.closed_reason == "false positive"
    # It is over: the Open list leaves it out, and the full list puts it with the closed ones, after the live ones.
    still_open = client.get("/api/incidents?status=open&project=main", headers=login(client, "ceo@acme.example")).json()
    assert inc.id not in {i["id"] for i in still_open}
    everything = client.get("/api/incidents?project=main", headers=login(client, "ceo@acme.example")).json()
    statuses = [i["status"] for i in everything]
    first_closed = next(n for n, st in enumerate(statuses) if st in ("resolved", "false_positive"))
    assert all(st in ("resolved", "false_positive") for st in statuses[first_closed:])
    # Withdrawn, the finding opens again and so does the incident: it was not over after all.
    back = client.post(f"/api/signals/{s.id}/reopen?project=main", headers=login(client, "ceo@acme.example"))
    assert back.status_code == 200, back.text
    db.refresh(inc)
    assert inc.status == "open" and inc.resolved_at is None
    assert "withdrew the false positive" in inc.events[-1].text


def test_said_fixed_is_checked_again_and_resolved_or_sent_back(place, client, monkeypatch):
    db, org, project, made = place
    inc = Incident(org_id=org.id, project_id=project.id, ref="INC-9802", title="A leaked key", severity="high",
                   status="investigating")
    db.add(inc)
    db.commit()
    made["incidents"].append(inc.id)
    s = _signal(db, org, project, made, incident_id=inc.id)
    outcome = {"now": verify.STILL}
    monkeypatch.setitem(verify.CHECKERS, "whitehats:code",
                        lambda db, signal: verify.Check(signal, outcome["now"], "the rule ran again"))
    said = client.patch(f"/api/incidents/{inc.id}?project=main", headers=login(client, "ceo@acme.example"),
                        json={"status": "verifying", "note": "We rotated the key and removed it."})
    assert said.status_code == 200 and said.json()["status"] == "verifying"
    assert verify.incident(db, db.get(Incident, inc.id)) == "investigating"  # still there: back, saying so
    assert "Still there" in db.get(Incident, inc.id).events[-1].text
    db.get(Incident, inc.id).status = "verifying"
    db.commit()
    outcome["now"] = verify.GONE
    assert verify.incident(db, db.get(Incident, inc.id)) == "resolved"
    assert db.get(Incident, inc.id).events[-1].text.startswith("Checked again: gone.")
    assert s.id in made["signals"]


def test_a_fix_made_by_hand_is_verified_only_when_its_finding_is_gone(place, client, monkeypatch):
    db, org, project, made = place
    s = _signal(db, org, project, made)
    fix = Remediation(org_id=org.id, project_id=project.id, signal_id=s.id, title="Rotate the key", target="shop",
                      action="Rotate it.", destination="Fix Factory", factory_intent="WH-F-1", risk="high")
    db.add(fix)
    db.commit()
    made["fixes"].append(fix.id)
    approver = login(client, "ceo@acme.example")
    taken = client.post(f"/api/remediations/{fix.id}/by-hand?project=main", headers=approver, json={"note": ""})
    assert taken.status_code == 200, taken.text
    assert taken.json()["status"] == "dispatched" and taken.json()["manual"] and taken.json()["destination"] == "By hand"
    done = client.post(f"/api/remediations/{fix.id}/done?project=main", headers=approver)
    assert done.status_code == 200 and done.json()["status"] == "verifying"
    monkeypatch.setitem(verify.CHECKERS, "whitehats:code",
                        lambda db, signal: verify.Check(signal, verify.STILL, "the rule still fires"))
    assert verify.fix(db, db.get(Remediation, fix.id)) == "dispatched"
    assert "still there" in db.get(Remediation, fix.id).result
    monkeypatch.setitem(verify.CHECKERS, "whitehats:code",
                        lambda db, signal: verify.Check(signal, verify.GONE, "the rule no longer fires"))
    db.get(Remediation, fix.id).status = "verifying"
    db.commit()
    assert verify.fix(db, db.get(Remediation, fix.id)) == "verified"


def test_a_check_that_already_resolved_the_incident_is_recorded_once(place, client, monkeypatch):
    db, org, project, made = place
    inc = Incident(org_id=org.id, project_id=project.id, ref="INC-9803", title="A public bucket", severity="high",
                   status="verifying")
    db.add(inc)
    db.commit()
    made["incidents"].append(inc.id)
    _signal(db, org, project, made, incident_id=inc.id)

    def check_and_resolve(_db, signal):  # as Prowler's check does: it closes the finding, the sweep resolves
        other = SessionLocal()
        row = other.get(Incident, inc.id)
        row.status = "resolved"
        other.add(IncidentEvent(incident_id=inc.id, project_id=project.id, kind="status", actor="WhiteHats",
                                text="Status changed from verifying to resolved"))
        other.commit()
        other.close()
        return verify.Check(signal, verify.GONE, "Prowler finds the setting safe now")

    monkeypatch.setitem(verify.CHECKERS, "whitehats:code", check_and_resolve)
    assert verify.incident(db, db.get(Incident, inc.id)) == "resolved"
    db.expire_all()
    changes = [e.text for e in db.get(Incident, inc.id).events if e.kind == "status"]
    assert changes == ["Status changed from verifying to resolved"]  # once, not twice


def test_a_fix_whose_finding_is_closed_is_not_offered_and_comes_back_when_the_finding_reopens(client, ceo):
    from sqlalchemy import select as _select

    from app.db import SessionLocal, utcnow
    from app.models import Organization, Project, Remediation, Signal
    with SessionLocal() as db:
        org = db.scalar(_select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(_select(Project).where(Project.org_id == org.id, Project.slug == "main"))
        finding = Signal(org_id=org.id, project_id=project.id, severity="high", title="Closed weakness", source="audit",
                         closed_at=utcnow(), closed_reason="put right")
        db.add(finding)
        db.flush()
        fix = Remediation(org_id=org.id, project_id=project.id, signal_id=finding.id, title="Fix: closed weakness",
                          target="shop/orders.py:1", action="change the code", destination="Fix Factory", risk="high", status="pending")
        db.add(fix)
        db.commit()
        fid, sid = fix.id, finding.id
    try:
        listed = lambda: [r["id"] for r in client.get("/api/remediations?status=pending", headers=ceo).json()]
        queued = lambda: [d["id"] for d in client.get("/api/overview", headers=ceo).json()["decisions"] if d.get("kind") == "fix"]
        assert fid not in listed() and fid not in queued()
        with SessionLocal() as db:
            db.get(Signal, sid).closed_at = None
            db.commit()
        assert fid in listed() and fid in queued()
    finally:
        with SessionLocal() as db:
            db.delete(db.get(Remediation, fid))
            db.delete(db.get(Signal, sid))
            db.commit()

"""Approvals may only report a simulation or a send that actually happened."""

import re

from sqlalchemy import select

SENT = re.compile(r"\bsent\b", re.IGNORECASE)


def _add(title, **fields) -> int:
    from app.db import SessionLocal
    from app.models import Organization, Remediation

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        row = Remediation(org_id=org.id, title=title, target="unit-test", risk="low", **fields)
        db.add(row)
        db.commit()
        return row.id


def _rows(client, ceo):
    res = client.get("/api/remediations", headers=ceo)
    assert res.status_code == 200, res.text
    return res.json()


def _one(client, ceo, rid):
    return next(row for row in _rows(client, ceo) if row["id"] == rid)


def _arm(monkeypatch, fake):
    monkeypatch.setattr("app.routers.operate.factory.serves", lambda org: True)
    monkeypatch.setattr("app.routers.operate.factory.call", fake)


def test_no_simulated_first_without_a_linked_run(client, ceo, monkeypatch):
    _arm(monkeypatch, lambda *args, **kwargs: {"summary": {}, "runs": []})
    poison = "Simulated first: 1 code change · tests pass · 0 services affected"
    rid = _add("Poisoned impact string", destination="Okta", action="Remove the extra admin role.",
               simulated_impact=poison)
    row = _one(client, ceo, rid)
    assert row["simulated_impact"] == poison
    for item in _rows(client, ceo):
        banner = item["impact_banner"]
        assert banner == {"text": "Impact not simulated yet", "run_id": None}
        assert "Simulated first" not in banner["text"]
        if item["simulated_impact"]:
            assert item["simulated_impact"] not in banner["text"]
    overview = client.get("/api/overview", headers=ceo).json()
    for item in overview["decisions"]:
        assert poison not in item.get("detail", "")
        assert "tests pass" not in item.get("detail", "")


def test_simulated_first_quotes_the_linked_run(client, ceo, monkeypatch):
    honest = "run-honest-9"

    def fake(org, method, path, body=None, timeout=None):
        assert method == "GET"
        if path == "/api/runs":
            return {"summary": {}, "runs": [
                {"id": honest, "item_id": "WH-INT-HONEST", "status": "verified", "finished_at": "2026-10-22T01:00:00"},
                {"id": "run-empty", "item_id": "WH-INT-EMPTY", "status": "verified",
                 "finished_at": "2026-10-22T00:30:00"},
                {"id": "run-queued", "item_id": "WH-INT-QUEUED", "status": "running", "finished_at": None},
            ]}
        if path == f"/api/runs/{honest}":
            return {"run_id": honest, "row": {"id": honest}, "verification": [
                {"check": "Proof of weakness", "before": "failed", "after": "passed"},
                {"check": "Project tests", "before": "passed", "after": "passed"},
            ]}
        return {"run_id": path.rsplit("/", 1)[-1], "verification": []}

    _arm(monkeypatch, fake)
    poison = "Simulated first: 1 code change · tests pass · 0 services affected"
    linked = _add("Linked by intent", destination="Fix Factory", factory_intent="WH-INT-HONEST",
                  action="Parameterise the query.", simulated_impact=poison)
    stored = _add("Linked by run id", destination="Fix Factory", factory_intent="WH-INT-OTHER",
                  factory_run_id=honest, action="Parameterise the query.", simulated_impact=poison)
    empty = _add("Finished run with no checks", destination="Fix Factory", factory_intent="WH-INT-EMPTY",
                 action="Change a file.", simulated_impact=poison)
    queued = _add("Run still going", destination="Fix Factory", factory_intent="WH-INT-QUEUED",
                  action="Change a file.", simulated_impact=poison)
    other = _add("No run at all", destination="Slack", action="Post the steps.", simulated_impact=poison)
    expected = ("Simulated first: Proof of weakness failed before, passed after"
                " · Project tests passed before, passed after")
    for rid in (linked, stored):
        banner = _one(client, ceo, rid)["impact_banner"]
        assert banner["text"] == expected
        assert banner["run_id"] == honest
        assert poison not in banner["text"]
    for rid in (empty, queued, other):
        banner = _one(client, ceo, rid)["impact_banner"]
        assert banner == {"text": "Impact not simulated yet", "run_id": None}
        assert "Simulated first" not in banner["text"]


def test_non_factory_approval_is_manual_and_records_who_finished_it(client, ceo, engineer):
    incidents = client.get("/api/incidents", headers=ceo).json()
    incident_id = next(i["id"] for i in incidents if i["ref"] == "INC-2211")
    waiting = _add("Not approved yet", destination="Okta", action="Do not mark this done yet.")
    assert client.post(f"/api/remediations/{waiting}/done", headers=ceo).status_code == 409
    for destination in ("Okta", "Firewall runbook", "Slack"):
        steps = f"Do this in {destination} and record the outcome."
        title = f"Manual {destination} fix"
        rid = _add(title, destination=destination, action=steps, incident_id=incident_id,
                   simulated_impact="Signed intent sent to nowhere")
        approved = client.post(f"/api/remediations/{rid}/approve", headers=ceo)
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "dispatched" and body["manual"] is True and body["factory_run_id"] is None
        assert body["decided_by"] == "Anita Rao"
        assert body["result"] == f"Manual: carry this out in {destination}.\n{steps}"
        assert SENT.search(body["result"]) is None
        assert "Watching" not in body["result"] and "Signed intent" not in body["result"]
        done = client.post(f"/api/remediations/{rid}/done", headers=ceo)
        assert done.status_code == 200, done.text
        finished = done.json()
        assert finished["status"] == "done" and finished["done_by"] == "Anita Rao" and finished["done_at"]
        assert finished["result"] == body["result"]
        assert SENT.search(finished["result"]) is None
        assert client.post(f"/api/remediations/{rid}/done", headers=ceo).status_code == 409
        timeline = client.get(f"/api/incidents/{incident_id}", headers=ceo).json()["timeline"]
        assert any(e["text"] == f"Approved: {title} via {destination}" and SENT.search(e["text"]) is None
                   for e in timeline)
        assert any(e["text"] == f"Marked done: {title}" and e["actor"] == "Anita Rao" for e in timeline)
    rid = _add("Engineer closes it", destination="Slack", action="Tell the channel the agent is back.")
    assert client.post(f"/api/remediations/{rid}/approve", headers=ceo).status_code == 200
    closed = client.post(f"/api/remediations/{rid}/done", headers=engineer)
    assert closed.status_code == 200, closed.text
    assert closed.json()["done_by"] == "Kavya Iyer" and closed.json()["status"] == "done"


def test_factory_approval_claims_sent_only_when_a_run_starts(client, ceo, monkeypatch):
    def started(org, method, path, body=None, timeout=None):
        if method == "GET":
            return {"summary": {}, "runs": []}
        assert (method, path, body) == ("POST", "/api/runs", {"intent": "WH-INT-HONEST-SEND", "mode": "live"})
        return {"run": "run-honest-1"}

    monkeypatch.setattr("app.routers.operate.factory.call", started)
    rid = _add("Really send", destination="Fix Factory", factory_intent="WH-INT-HONEST-SEND",
               action="Parameterise the query.", simulated_impact="Simulated first: tests pass")
    approved = client.post(f"/api/remediations/{rid}/approve", headers=ceo)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["factory_run_id"] == "run-honest-1" and body["manual"] is False
    assert body["result"] == ("Sent to Fix Factory as run run-honest-1. "
                              "It will prove the fix before anyone merges it.")
    assert client.post(f"/api/remediations/{rid}/done", headers=ceo).status_code == 409
    assert _one(client, ceo, rid)["status"] == "dispatched"


def test_factory_approval_without_a_run_id_is_not_sent(client, ceo, monkeypatch):
    monkeypatch.setattr("app.routers.operate.factory.call", lambda *args, **kwargs: {"ok": True})
    missing = _add("Factory forgot the run", destination="Fix Factory", factory_intent="WH-INT-NORUN",
                   action="Change the code.")
    refused = client.post(f"/api/remediations/{missing}/approve", headers=ceo)
    assert refused.status_code == 502
    left = _one(client, ceo, missing)
    assert left["status"] == "pending" and left["factory_run_id"] is None
    assert SENT.search(left["result"] or "") is None


def test_factory_approval_when_the_engine_is_down_is_not_sent(client, ceo, monkeypatch):
    from fastapi import HTTPException

    from app.config import settings

    if settings.factory_token:
        def offline_engine(org, method, path, body=None, timeout=None):
            raise HTTPException(503, "The Fix Factory engine is not running.")

        monkeypatch.setattr("app.routers.operate.factory.call", offline_engine)
    offline = _add("Engine is down", destination="Fix Factory", factory_intent="WH-INT-OFFLINE",
                   action="Change the code.")
    down = client.post(f"/api/remediations/{offline}/approve", headers=ceo)
    assert down.status_code in (409, 503), down.text
    stayed = _one(client, ceo, offline)
    assert stayed["status"] == "pending" and stayed["factory_run_id"] is None
    assert SENT.search(stayed["result"] or "") is None

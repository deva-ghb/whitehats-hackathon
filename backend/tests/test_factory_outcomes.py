"""A fix sent to Fix Factory moves on when its run finishes, so the incident can be resolved."""

import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

RECORDED = json.loads((Path(__file__).parent / "recordings/fix_factory_runs.json").read_text())
MERGED = RECORDED["merged"]
DECLINED = RECORDED["declined"]
MERGED_ID = MERGED["row"]["id"]
DECLINED_ID = DECLINED["row"]["id"]
NOTE = "Containment is in place for this test"


def _awaiting_approval(detail: dict) -> dict:
    """The merged recording as it looked before a person approved it in Fix Factory."""
    early = copy.deepcopy(detail)
    early["row"].update(approval="pending", approved_at=None, merged=None)
    early["events"] = [e for e in early["events"] if e["stage"] != "approve"]
    return early


class RecordedEngine:
    """Answers like the Fix Factory engine, from the recorded runs."""

    def __init__(self, runs: dict):
        self.runs = runs
        self.offline = False

    def __call__(self, org, method, path, body=None, timeout=None):
        if self.offline:
            raise HTTPException(503, "The Fix Factory engine is not running.")
        if (method, path) == ("POST", "/api/runs"):
            return {"run": MERGED_ID}
        if (method, path) == ("GET", "/api/runs"):
            return {"summary": {}, "runs": [d["row"] for d in self.runs.values()]}
        run_id = path.removeprefix("/api/runs/").removesuffix("/approve")
        if method == "POST" and path.endswith("/approve"):
            self.runs[run_id] = MERGED
            return {"message": "Approved and merged into demo at 38696a9e58"}
        if method == "GET" and run_id in self.runs:
            return self.runs[run_id]
        raise HTTPException(404, f"unknown run {run_id}")


@pytest.fixture
def engine(monkeypatch):
    fake = RecordedEngine({MERGED_ID: _awaiting_approval(MERGED), DECLINED_ID: DECLINED})
    monkeypatch.setattr("app.services.factory.serves", lambda org: True)
    monkeypatch.setattr("app.services.factory.call", fake)
    return fake


def _fix(incident_id: int, title: str, **fields) -> int:
    from app.db import SessionLocal
    from app.models import Incident, Remediation

    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        row = Remediation(org_id=inc.org_id, project_id=inc.project_id, incident_id=incident_id, title=title,
                          target="customer-api", action="Parameterise the query.", destination="Fix Factory",
                          **fields)
        db.add(row)
        db.commit()
        return row.id


def _resolve(client, ceo, iid):
    """A person says it is fixed; WhiteHats checks again (FD.2), as its worker would, and resolves it."""
    from app.db import SessionLocal
    from app.models import Incident
    from app.services import verify

    said = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"status": "verifying", "note": NOTE})
    if said.status_code != 200:
        return said
    with SessionLocal() as db:
        verify.incident(db, db.get(Incident, iid))
    return client.get(f"/api/incidents/{iid}", headers=ceo)


def _fix_out(client, ceo, iid, rid):
    body = client.get(f"/api/incidents/{iid}", headers=ceo).json()
    return body, next(r for r in body["remediations"] if r["id"] == rid)


def test_a_merged_run_verifies_the_fix_and_unblocks_resolve(client, ceo, engine, incident):
    rid = _fix(incident, "Fix SQL injection in user lookup", factory_intent="WH-INT-2208-1")
    approved = client.post(f"/api/remediations/{rid}/approve", headers=ceo)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "dispatched"

    _, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "dispatched"
    blocked = _resolve(client, ceo, incident)
    assert blocked.status_code == 409 and "not verified yet" in blocked.json()["detail"]

    decided = client.post(f"/api/factory/runs/{MERGED_ID}/approve", headers=ceo)
    assert decided.status_code == 200, decided.text
    body, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "verified"
    assert fix["result"] == f"Fix Factory run {MERGED_ID}: Approved and merged into demo at 38696a9e58."
    assert fix["done_at"] and fix["done_by"] is None
    assert any(e["text"] == "Fix Factory proved and delivered: Fix SQL injection in user lookup"
               and e["actor"] == "Fix Factory" for e in body["timeline"])

    resolved = _resolve(client, ceo, incident)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"


def test_an_outcome_is_recorded_once(client, ceo, engine, incident):
    engine.runs[MERGED_ID] = MERGED
    rid = _fix(incident, "Already merged", factory_intent="WH-INT-2208-1", factory_run_id=MERGED_ID,
               status="dispatched")
    for _ in range(2):
        _fix_out(client, ceo, incident, rid)
    body, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "verified"
    assert sum(1 for e in body["timeline"] if e["actor"] == "Fix Factory") == 1


def test_a_declined_run_marks_the_fix_failed_and_says_why(client, ceo, engine, incident):
    rid = _fix(incident, "Expire idle login sessions", factory_intent="WH-INT-2216-1",
               factory_run_id=DECLINED_ID, status="dispatched")
    rows = client.get("/api/remediations", headers=ceo).json()
    fix = next(r for r in rows if r["id"] == rid)
    assert fix["status"] == "failed"
    assert fix["result"].startswith(f"Fix Factory run {DECLINED_ID} did not fix this: it judged the fix out of "
                                    "scope. A person needs to take it from here. This repository contains no "
                                    "session management")
    body, _ = _fix_out(client, ceo, incident, rid)
    assert any(e["text"] == "Fix Factory could not fix: Expire idle login sessions" for e in body["timeline"])
    resolved = _resolve(client, ceo, incident)
    assert resolved.status_code == 200
    assert any(e["text"] == "Warning: called fixed by a person although the automated fix failed: "
                            "Expire idle login sessions" for e in resolved.json()["timeline"])
    assert resolved.json()["status"] == "resolved"


def test_resolving_without_a_failed_fix_adds_no_warning(client, ceo, incident):
    resolved = _resolve(client, ceo, incident)
    assert resolved.status_code == 200
    assert not any(e["text"].startswith("Warning:") for e in resolved.json()["timeline"])


def test_resolve_reads_the_outcome_without_a_screen_first(client, ceo, engine, incident):
    engine.runs[MERGED_ID] = MERGED
    _fix(incident, "Merged while nobody looked", factory_intent="WH-INT-2208-1", factory_run_id=MERGED_ID,
         status="dispatched")
    resolved = _resolve(client, ceo, incident)
    assert resolved.status_code == 200, resolved.text


def test_a_run_still_waiting_for_a_person_keeps_the_fix_unverified(client, ceo, engine, incident):
    rid = _fix(incident, "Waiting in Fix Factory", factory_intent="WH-INT-2208-1", factory_run_id=MERGED_ID,
               status="dispatched")
    _, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "dispatched"
    assert _resolve(client, ceo, incident).status_code == 409


def test_an_offline_engine_leaves_the_fix_as_it_was(client, ceo, engine, incident):
    engine.offline = True
    rid = _fix(incident, "Engine is down", factory_intent="WH-INT-2208-1", factory_run_id=MERGED_ID,
               status="dispatched")
    res = client.get(f"/api/incidents/{incident}", headers=ceo)
    assert res.status_code == 200
    fix = next(r for r in res.json()["remediations"] if r["id"] == rid)
    assert fix["status"] == "dispatched"
    assert _resolve(client, ceo, incident).status_code == 409


@pytest.mark.parametrize(("row", "expected"), [
    ({"status": "running", "approval": None}, None),
    ({"status": "verified", "approval": "pending"}, None),
    ({"status": "verified", "approval": "approved"}, "verified"),
    ({"status": "review_flagged", "approval": "approved"}, "verified"),
    ({"status": "verified", "approval": "rejected"}, "failed"),
    ({"status": "verified", "approval": "withdrawn"}, "failed"),
    ({"status": "failed", "approval": None}, "failed"),
    ({"status": "ci_failed", "approval": None}, "failed"),
    ({"status": "declined", "approval": None}, "failed"),
    ({"status": "not_reproduced", "approval": None}, "failed"),
    ({"status": "error", "approval": None}, "failed"),
])
def test_run_outcome_maps_every_final_state(row, expected):
    from app.services.fix_outcomes import run_outcome

    outcome = run_outcome({"row": {"id": "run-1", **row}, "events": [], "note": ""})
    assert (outcome.status if outcome else None) == expected


def test_the_run_list_leaves_out_runs_started_from_another_project(client, ceo, engine):
    from app.db import SessionLocal
    from app.models import Organization, Project, Remediation

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        side = Project(org_id=org.id, name="Run Side", slug="run-side")
        db.add(side)
        db.flush()
        fix = Remediation(org_id=org.id, project_id=side.id, title="Side fix", target="side-api", action="Fix it.",
                          destination="Fix Factory", status="dispatched", factory_run_id=MERGED_ID)
        db.add(fix)
        db.commit()
        side_id, fix_id = side.id, fix.id
    try:
        runs = client.get("/api/factory/runs", headers=ceo).json()["runs"]
        assert MERGED_ID not in {run["id"] for run in runs}
        assert DECLINED_ID in {run["id"] for run in runs}
    finally:
        with SessionLocal() as db:
            db.query(Remediation).filter(Remediation.id == fix_id).delete()
            db.query(Project).filter(Project.id == side_id).delete()
            db.commit()

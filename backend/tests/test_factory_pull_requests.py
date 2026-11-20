"""A fix Fix Factory split into ordered pull requests: what the fix says while they wait, and after.

Plays back real engine answers from its pull request line (tests/recordings/fix_factory_split_runs.json).
No text may claim a pull request, a CI result or a merge the engine has not recorded (R-018).
"""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from test_factory_outcomes import _fix, _fix_out, _resolve

RECORDED = json.loads((Path(__file__).parent / "recordings/fix_factory_split_runs.json").read_text())
OPENED, MERGED, CI_FAILED = RECORDED["opened"], RECORDED["merged"], RECORDED["ci_failed"]
RUN_ID = OPENED["row"]["id"]
RUNNING = {"row": {"id": RUN_ID, "status": "running", "approval": None, "finished_at": None},
           "events": [], "pieces": []}
TITLE = "Log every error path with its request id"


class SplitEngine:
    """Answers like the engine for one split run, which moves on when the test says so."""

    def __init__(self):
        self.run = None

    def __call__(self, org, method, path, body=None, timeout=None):
        if (method, path) == ("POST", "/api/runs"):
            self.run = RUNNING
            return {"run": RUN_ID}
        if (method, path) == ("GET", "/api/runs"):
            return {"summary": {}, "runs": [self.run["row"]] if self.run else []}
        if method == "GET" and path == f"/api/runs/{RUN_ID}" and self.run:
            return self.run
        raise HTTPException(404, "not found")


@pytest.fixture
def engine(monkeypatch):
    fake = SplitEngine()
    monkeypatch.setattr("app.services.factory.serves", lambda org: True)
    monkeypatch.setattr("app.services.factory.call", fake)
    return fake


def _apply(client, ceo, incident_id):
    rid = _fix(incident_id, TITLE, factory_intent="WH-INT-LOG-1")
    applied = client.post(f"/api/remediations/{rid}/approve", headers=ceo)
    assert applied.status_code == 200, applied.text
    return rid


def test_apply_fix_says_only_that_it_was_sent_until_pull_requests_exist(client, ceo, engine, incident):
    rid = _apply(client, ceo, incident)
    _, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "dispatched"
    assert fix["result"] == f"Sent to Fix Factory as run {RUN_ID}. It will prove the fix before anyone merges it."
    assert "pull request" not in fix["result"]


def test_opened_pull_requests_are_listed_in_merge_order(client, ceo, engine, incident):
    rid = _apply(client, ceo, incident)
    engine.run = OPENED
    listed = client.get("/api/remediations", headers=ceo)  # the Approvals list is the first screen to read it
    assert listed.status_code == 200, listed.text
    for _ in range(2):  # a second read changes nothing and adds no second timeline line
        body, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "dispatched"
    assert fix["result"] == (
        f"Fix Factory run {RUN_ID} opened 3 pull requests, and CI passed on each. "
        "Merge them in this order on GitHub:\n"
        "1. #1 shop-api: https://github.example/acme/shop/pull/1\n"
        "2. #2 payments-worker: https://github.example/acme/shop/pull/2\n"
        "3. #3 infrastructure: https://github.example/acme/shop/pull/3"
    )
    opened = [e for e in body["timeline"] if e["text"] == f"Fix Factory opened 3 pull requests: {TITLE}"]
    assert len(opened) == 1 and opened[0]["actor"] == "Fix Factory"
    assert _resolve(client, ceo, incident).status_code == 409  # not merged, so not verified


def test_merging_every_pull_request_verifies_the_fix(client, ceo, engine, incident):
    rid = _apply(client, ceo, incident)
    engine.run = OPENED
    _fix_out(client, ceo, incident, rid)
    engine.run = MERGED
    _, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "verified"
    assert fix["result"] == (f"Fix Factory run {RUN_ID}: Merged on GitHub: #1 (shop-api), #2 (payments-worker), "
                             "#3 (infrastructure).")
    assert _resolve(client, ceo, incident).status_code == 200


def test_failed_ci_is_never_reported_as_passed(client, ceo, engine, incident):
    rid = _apply(client, ceo, incident)
    engine.run = CI_FAILED
    _, fix = _fix_out(client, ceo, incident, rid)
    assert fix["status"] == "failed"
    assert fix["result"].startswith(f"Fix Factory run {CI_FAILED['row']['id']} did not fix this: CI did not pass "
                                    "on its pull requests. A person needs to take it from here. CI did not pass "
                                    "on #2 (payments-worker).")
    assert "CI passed" not in fix["result"]


def test_the_ci_claim_needs_every_pull_request_to_have_passed():
    from app.services.fix_outcomes import opened_pull_requests

    unfinished = json.loads(json.dumps(OPENED))
    unfinished["pieces"][2]["ci"] = None
    opened = opened_pull_requests(unfinished)
    assert opened.count == 3
    assert opened.result.startswith(f"Fix Factory run {RUN_ID} opened 3 pull requests. Merge them in this order")
    partly = json.loads(json.dumps(OPENED))
    partly["pieces"][2]["pull_request"] = None
    assert opened_pull_requests(partly) is None  # half a fix is never listed as opened


def test_a_draft_says_it_is_not_proven_and_needs_a_thorough_review_and_merging_it_verifies_after_review():
    from app.services.fix_outcomes import opened_pull_requests, run_outcome

    draft = json.loads(json.dumps(OPENED))
    draft["row"]["status"] = "draft"
    draft["note"] = "Not proven: the proof still fails with every piece applied."
    opened = opened_pull_requests(draft)
    assert opened.count == 3
    assert opened.result.startswith(f"Fix Factory run {RUN_ID} could not prove a fix, so it opened its best attempt "
                                    "as 3 draft pull requests that need a thorough human review.")
    assert opened.result.endswith("Not proven: the proof still fails with every piece applied.")
    assert run_outcome(draft) is None  # waiting for a person, not failed
    draft["row"]["approval"] = "approved"
    merged = run_outcome(draft)
    assert merged.status == "verified" and "after a person reviewed the draft it could not prove" in merged.result

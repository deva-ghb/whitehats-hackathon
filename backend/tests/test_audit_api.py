"""AUD.6: the screens read a project's audits and one audit as it runs; a person who may triage asks for one."""

import json

from sqlalchemy import delete

from app.db import SessionLocal
from app.models import Audit


def test_an_audit_is_listed_and_read_with_its_progress_and_records(client, ceo, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(json.dumps({"at": 1.0, "step": "hunt", "message": "8 hunters"}) + "\n")
    (run_dir / "findings.json").write_text(json.dumps([{"verdict": "rejected", "title": "Not reachable",
                                                         "fingerprint": "a:b", "trace": [{"file": "a.py", "line": 3}],
                                                         "reason": "the gatekeeper refuses it"}]))
    audit_id = client.post("/api/audits", json={}, headers=ceo).json()["id"]  # in the project the person is in
    with SessionLocal() as db:
        row = db.get(Audit, audit_id)
        row.status, row.step, row.run_dir = "running", "hunt", str(run_dir)
        db.commit()
    try:
        listed = client.get("/api/audits", headers=ceo).json()["audits"]
        assert any(a["id"] == audit_id and a["step"] == "hunt" for a in listed)
        one = client.get(f"/api/audits/{audit_id}", headers=ceo).json()
        assert one["events"][0]["message"] == "8 hunters"
        assert one["records"][0] == {"verdict": "rejected", "title": "Not reachable", "fingerprint": "a:b",
                                     "severity": None, "where": "a.py:3", "reason": "the gatekeeper refuses it",
                                     "blockers": []}
        assert client.get("/api/audits/999999", headers=ceo).status_code == 404
    finally:
        with SessionLocal() as db:
            db.execute(delete(Audit).where(Audit.id == audit_id))
            db.commit()


def test_asking_for_an_audit_queues_it_with_who_asked(client, ceo):
    answer = client.post("/api/audits", json={"reason": "before the bank's questionnaire", "scope": ["/shop"]},
                         headers=ceo)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    try:
        assert body["status"] == "queued" and body["trigger"] == "request" and body["scope"] == ["shop"]
        assert "before the bank's questionnaire" in body["trigger_detail"]
    finally:
        with SessionLocal() as db:
            db.execute(delete(Audit).where(Audit.id == body["id"]))
            db.commit()

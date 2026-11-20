"""FX.1: a fix's own page says what was found (with the code lines as checked), what will change and what it touches,
and says plainly when one of those is not known."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import ChangeEvent, Connector, Organization, Remediation, Signal
from app.projects import main_project_id
from app.services import fix_details

REPO = "acme/shop"
RECEIPTS = "\n".join(f"line {n}" for n in range(1, 31)).replace("line 12", "return store.get(Key=receipt_id)")
SUSPECT = {"engine": "house-rules", "rule_id": "whitehats.storage-key", "title": "Caller value used as an object key",
           "evidence": {"path": "shop/receipts.py", "start_line": 12, "end_line": 12, "resource": ""},
           "severity": "high", "cwe": ["CWE-22"], "status": "open", "note": ""}
TWIN = {"nodes": [{"id": "shop-api", "name": "shop-api", "type": "service.backend", "criticality": "high",
                   "properties": {"code_repo": REPO, "code_folder": "shop"}},
                  {"id": "receipts", "name": "receipts bucket", "type": "storage.object", "data_classification": "pii",
                   "properties": {}},
                  {"id": "elsewhere", "name": "elsewhere", "type": "service.backend", "properties": {}}],
        "edges": [{"from": "shop-api", "to": "receipts", "type": "reads"}]}


class Files:
    def __init__(self):
        self.asked = []

    def file_at(self, repo, path, sha):
        self.asked.append((repo, path, sha))
        return RECEIPTS.encode(), ""


@pytest.fixture
def fix(monkeypatch):
    files = Files()
    monkeypatch.setattr(fix_details, "_github", lambda db, org_id: files)
    monkeypatch.setattr(fix_details, "_graphs", lambda db, project_id: [TWIN])
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        pid = main_project_id(db, org.id)
        connector = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "github"))
        change = ChangeEvent(org_id=org.id, connector_id=connector.id, project_ids=[pid], repo=REPO,
                             branch="demo/S02", head_sha="a" * 40, confirmations=[{
                                 "suspect": SUSPECT, "status": "confirmed", "severity": "high",
                                 "explanation": "Any caller can name another customer's receipt.", "model": "luna"}])
        db.add(change)
        db.flush()
        signal = Signal(org_id=org.id, project_id=pid, severity="high", title=SUSPECT["title"],
                        detail="shop/receipts.py:12 (CWE-22). Any caller can name another customer's receipt.",
                        entity="shop/receipts.py:12", resource=f"https://github.com/{REPO}", source="whitehats:code",
                        fingerprint="whitehats.storage-key|shop/receipts.py|", branch="demo/S02", change_id=change.id)
        db.add(signal)
        db.flush()
        fix = Remediation(org_id=org.id, project_id=pid, signal_id=signal.id, title="Stop naming receipts by key",
                          target="shop-api", action="Fix Factory will write a proof, then the fix.",
                          destination="Fix Factory", risk="high")
        manual = Remediation(org_id=org.id, project_id=pid, title="Rotate the key", target="nowhere-on-the-twin",
                             action="Open the provider console.\nRevoke the key.", destination="Runbook", risk="high")
        db.add_all([fix, manual])
        db.commit()
        yield {"fix": fix.id, "manual": manual.id, "files": files}
        db.execute(delete(Remediation).where(Remediation.id.in_([fix.id, manual.id])))
        db.execute(delete(Signal).where(Signal.id == signal.id))
        db.execute(delete(ChangeEvent).where(ChangeEvent.id == change.id))
        db.commit()


def test_a_fix_from_a_finding_shows_the_finding_the_code_as_checked_and_what_the_model_said(client, ceo, fix):
    res = client.get(f"/api/remediations/{fix['fix']}", headers=ceo)
    assert res.status_code == 200, res.text
    body = res.json()
    finding = body["evidence"]["finding"]
    assert body["evidence"]["kind"] == "finding"
    assert (finding["path"], finding["line"], finding["repo"], finding["branch"]) == ("shop/receipts.py", 12, REPO,
                                                                                    "demo/S02")
    assert (finding["engine"], finding["rule"], finding["cwe"]) == ("house-rules", "whitehats.storage-key", ["CWE-22"])
    assert finding["model"] == {"status": "confirmed", "text": "Any caller can name another customer's receipt.",
                                "model": "luna"}
    assert fix["files"].asked == [(REPO, "shop/receipts.py", "a" * 40)]  # the commit it was found in, not the branch
    lines = finding["code"]["lines"]
    assert [line["number"] for line in lines] == list(range(8, 17))
    assert [line["text"] for line in lines if line["flagged"]] == ["return store.get(Key=receipt_id)"]


def test_it_touches_the_service_holding_the_code_and_its_neighbours_only(client, ceo, fix):
    touches = client.get(f"/api/remediations/{fix['fix']}", headers=ceo).json()["touches"]
    assert touches["on_twin"] is True
    assert [(n["id"], n["why"]) for n in touches["where"]] == [("shop-api", "its code holds shop/receipts.py")]
    assert [(n["id"], n["why"], n["data"]) for n in touches["near"]] == [("receipts", "reads", "pii")]


def test_a_fix_factory_fix_that_has_not_run_says_what_will_happen(client, ceo, fix):
    change = client.get(f"/api/remediations/{fix['fix']}", headers=ceo).json()["change"]
    assert change == {"kind": "planned", "summary": "Fix Factory will write a proof, then the fix.", "ran": False,
                      "run_id": None}


def test_a_manual_fix_lists_its_steps_and_says_when_nothing_on_the_twin_matches(client, ceo, fix):
    body = client.get(f"/api/remediations/{fix['manual']}", headers=ceo).json()
    assert body["change"] == {"kind": "manual", "destination": "Runbook",
                              "steps": ["Open the provider console.", "Revoke the key."]}
    assert body["evidence"] == {"kind": "none"}
    assert body["touches"]["on_twin"] is False
    assert "nowhere-on-the-twin" in body["touches"]["why"]


def test_a_weekly_scan_finding_says_the_model_was_not_asked(monkeypatch):
    assert fix_details._model_words(None)["status"] == "not asked"
    assert fix_details._model_words({"status": "confirmed", "error": "timeout"})["status"] == "no answer"


def test_a_fix_that_is_not_there_is_not_found(client, ceo):
    assert client.get("/api/remediations/987654", headers=ceo).status_code == 404


def test_the_explanation_leaves_out_the_place_the_page_shows_apart():
    assert fix_details._without_place("shop/receipts.py:12 (CWE-22). Any caller can name it.", "shop/receipts.py:12") \
        == "Any caller can name it."
    assert fix_details._without_place("shop/a.py:3. Found by the weekly scan.", "shop/a.py:3") == "Found by the weekly scan."
    assert fix_details._without_place("Something else.", "shop/a.py:3") == "Something else."

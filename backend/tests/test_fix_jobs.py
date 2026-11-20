"""FF.SRV: a live finding is offered as a fix at once, Apply Fix sends Fix Factory an intent made from the finding,
the proof is WhiteHats running the same rule again, and the fix closes with its finding."""

import pytest
from conftest import login
from sqlalchemy import delete, select
from test_changes import HEAD, LEAKY, REPO, _diff, _push_line, _record
from test_findings import CLEAN_LINES, NEXT, judge, modify, script  # noqa: F401 - script is a fixture

from app.changes import findings
from app.config import settings
from app.db import SessionLocal
from app.decisions_api.decide import decide
from app.models import Project, Remediation, Signal
from app.services import fix_jobs

RULE_PATH = "shop/settings.py"


@pytest.fixture
def cleanup():
    with SessionLocal() as db:  # an earlier test's open finding would be updated, not opened, and offer nothing
        db.execute(delete(Signal).where(Signal.source == findings.SOURCE))
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(delete(Remediation).where(Remediation.factory_intent.like(f"{fix_jobs.INTENT_PREFIX}%")))
        db.commit()


@pytest.fixture
def token():
    object.__setattr__(settings, "factory_token", "dispatch-token")  # Settings is frozen everywhere else
    yield "dispatch-token"
    object.__setattr__(settings, "factory_token", "")


class Engine:
    """Answers like the Fix Factory engine and records what it was sent."""

    def __init__(self, repos):
        self.repos, self.sent = repos, []

    def __call__(self, org, method, path, body=None, timeout=None):
        self.sent.append((method, path, body))
        if (method, path) == ("GET", "/api/repos"):
            return self.repos
        if (method, path) == ("POST", "/api/intents"):
            return {"item": body["id"]}
        if (method, path) == ("POST", "/api/runs"):
            return {"run": f"{body['intent']}-20261101"}
        raise AssertionError(f"unexpected {method} {path}")


PROVING = [{"name": "proving-ground", "source": f"github:{REPO}", "deliver": "pull_request", "base": "main"}]


def _leak(row_id, script):  # noqa: F811
    script.diffs[("main", HEAD)] = _diff(RULE_PATH, LEAKY)
    script.files = {RULE_PATH: LEAKY.encode()}
    _record(row_id, [_push_line(HEAD)])
    judge()


def _suggested() -> list[Remediation]:
    with SessionLocal() as db:
        rows = db.scalars(select(Remediation).where(
            Remediation.factory_intent.like(f"{fix_jobs.INTENT_PREFIX}%")).order_by(Remediation.id)).all()
        db.expunge_all()
        return list(rows)


def test_a_high_finding_is_offered_as_a_fix_once_per_project(row_id, script, cleanup):  # noqa: F811
    _leak(row_id, script)
    fixes = _suggested()
    with SessionLocal() as db:
        found = db.scalars(select(Signal).where(Signal.fingerprint.like(f"%|{RULE_PATH}|"))).all()
        assert {f.signal_id for f in fixes} == {s.id for s in found}  # one per project that holds the repository
    fix = fixes[0]
    assert (fix.status, fix.destination, fix.origin) == ("pending", "Fix Factory", "live")
    assert fix.factory_intent == f"WH-F-{fix.signal_id}" and fix.target == f"{RULE_PATH}:1"
    assert "no longer fires" in fix.action and "pull request" in fix.action
    judge()  # judging again offers nothing new
    assert len(_suggested()) == len(fixes)


def test_the_intent_is_made_from_the_finding_and_proved_by_its_own_rule(row_id, script, cleanup):  # noqa: F811
    _leak(row_id, script)
    with SessionLocal() as db:
        fix = db.get(Remediation, _suggested()[0].id)
        made = fix_jobs.intent(db, fix, PROVING)
        assert made["repo"] == "proving-ground" and made["base"] == "demo/change-1"
        assert made["proof"] == {"recheck": {"rule": made["proof"]["recheck"]["rule"], "scanner": "whitehats",
                                             "path": RULE_PATH}}
        assert made["proof"]["recheck"]["rule"].startswith("whitehats.")
        assert f"{RULE_PATH}:1" in made["summary"] and HEAD[:7] in made["summary"]
        with pytest.raises(fix_jobs.NotFixable, match="not set up for"):
            fix_jobs.intent(db, fix, [{**PROVING[0], "source": "github:someone/else"}])


def test_apply_fix_sends_the_intent_then_starts_the_run(client, row_id, script, cleanup, monkeypatch):  # noqa: F811
    _leak(row_id, script)
    engine = Engine(PROVING)
    monkeypatch.setattr("app.services.factory.serves", lambda org: True)
    monkeypatch.setattr("app.services.factory.call", engine)
    fix = _suggested()[0]
    with SessionLocal() as db:
        slug = db.get(Project, fix.project_id).slug
    approver = login(client, "ceo@acme.example")
    done = client.post(f"/api/remediations/{fix.id}/approve?project={slug}", headers=approver)
    assert done.status_code == 200, done.text
    assert [(m, p) for m, p, _ in engine.sent] == [("GET", "/api/repos"), ("POST", "/api/intents"), ("POST", "/api/runs")]
    assert engine.sent[1][2]["id"] == fix.factory_intent and engine.sent[2][2]["intent"] == fix.factory_intent
    assert done.json()["status"] == "dispatched" and done.json()["factory_run_id"] == f"{fix.factory_intent}-20261101"


def test_a_repository_the_engine_does_not_have_is_refused_with_the_reason(client, row_id, script, cleanup, monkeypatch):  # noqa: F811
    _leak(row_id, script)
    monkeypatch.setattr("app.services.factory.serves", lambda org: True)
    monkeypatch.setattr("app.services.factory.call", Engine([]))
    fix = _suggested()[0]
    with SessionLocal() as db:
        slug = db.get(Project, fix.project_id).slug
    refused = client.post(f"/api/remediations/{fix.id}/approve?project={slug}", headers=login(client))
    assert refused.status_code == 409 and "A person has to make this change" in refused.json()["detail"]


def test_the_recheck_fails_while_the_rule_fires_and_passes_once_it_does_not(client, token):
    files = {RULE_PATH: LEAKY}
    rule = next(s.rule_id for s in decide(_tree(files), scanners=()).suspects)
    body = {"rule": rule, "scanner": "whitehats", "path": RULE_PATH}
    assert client.post("/api/factory/recheck", json={**body, "files": files}).status_code == 401
    still = client.post("/api/factory/recheck", json={**body, "files": files}, headers={"Authorization": f"Bearer {token}"})
    assert still.status_code == 200 and still.json()["fixed"] is False and still.json()["still"][0]["line"] == 1
    fixed = client.post("/api/factory/recheck", json={**body, "files": {RULE_PATH: CLEAN_LINES}},
                        headers={"Authorization": f"Bearer {token}"})
    assert fixed.json() == {"fixed": True, "message": "", "still": []}
    outside = client.post("/api/factory/recheck", json={**body, "files": {"../etc/passwd": "x"}},
                          headers={"Authorization": f"Bearer {token}"})
    assert outside.status_code == 400


def test_an_engine_that_did_not_run_proves_nothing(monkeypatch):
    from app.decisions_api.suspect import Decision, EngineRun
    monkeypatch.setattr(fix_jobs, "decide", lambda root, scanners, **_: Decision(
        "clean", [], [EngineRun("semgrep", "semgrep:1", 0.1, error="the image could not be pulled")]))
    answer = fix_jobs.recheck("python.rule", "semgrep", "a.py", {"a.py": "x = 1\n"})
    assert answer["fixed"] is False and "did not run" in answer["message"]


def test_the_commit_that_removes_the_finding_closes_its_fix(row_id, script, cleanup):  # noqa: F811
    _leak(row_id, script)
    script.diffs[(HEAD, NEXT)] = modify(RULE_PATH, LEAKY.splitlines(), CLEAN_LINES.splitlines())
    script.files = {RULE_PATH: CLEAN_LINES.encode()}
    _record(row_id, [_push_line(NEXT)])
    judge()
    assert {fix.status for fix in _suggested()} == {"done"}
    assert all("The finding closed" in fix.result for fix in _suggested())


def _tree(files):
    import tempfile
    from pathlib import Path
    root = Path(tempfile.mkdtemp(prefix="wh-test-"))
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def test_a_fix_already_sent_keeps_its_status_for_its_own_run_to_decide(row_id, script, cleanup):  # noqa: F811
    _leak(row_id, script)
    with SessionLocal() as db:
        fix = db.get(Remediation, _suggested()[0].id)
        fix.status, fix.factory_run_id = "dispatched", "WH-F-1-20261101"
        db.commit()
    script.diffs[(HEAD, NEXT)] = modify(RULE_PATH, LEAKY.splitlines(), CLEAN_LINES.splitlines())
    script.files = {RULE_PATH: CLEAN_LINES.encode()}
    _record(row_id, [_push_line(NEXT)])
    judge()
    sent = next(f for f in _suggested() if f.factory_run_id)
    assert sent.status == "dispatched" and "The finding closed" in sent.result  # not "verified" on another fix's merge


def test_a_failed_fix_factory_fix_can_be_tried_again_and_nothing_else_reopens(client, row_id, script, cleanup, monkeypatch):  # noqa: F811
    _leak(row_id, script)
    engine = Engine(PROVING)
    monkeypatch.setattr("app.services.factory.serves", lambda org: True)
    monkeypatch.setattr("app.services.factory.call", engine)
    fix = _suggested()[0]
    with SessionLocal() as db:
        slug = db.get(Project, fix.project_id).slug
        row = db.get(Remediation, fix.id)
        row.status, row.factory_run_id, row.result = "failed", "WH-F-1-old", "no attempt passed every check"
        db.commit()
    again = client.post(f"/api/remediations/{fix.id}/approve?project={slug}", headers=login(client))
    assert again.status_code == 200 and again.json()["status"] == "dispatched"
    assert "Tried again by" in again.json()["result"] and again.json()["factory_run_id"] != "WH-F-1-old"
    assert client.post(f"/api/remediations/{fix.id}/approve?project={slug}", headers=login(client)).status_code == 409


def test_an_audit_findings_fix_is_applied_in_its_own_project_not_only_in_main(client, monkeypatch):
    from app.models import Organization
    from app.routers import operate

    sent = []
    monkeypatch.setattr("app.services.factory.serves", lambda org: True)
    monkeypatch.setattr("app.services.factory.call", lambda org, method, path, body=None: {"run": "WH-A-999-1"})
    monkeypatch.setattr(operate, "_send_finding_fix", lambda db, org, r: sent.append(r.factory_intent))
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug != "main"))
        fix = Remediation(org_id=org.id, project_id=project.id, title="Price from the catalogue", target="shop/orders.py",
                          action="Use the catalogue price.", destination="Fix Factory", factory_intent="WH-A-999",
                          risk="medium")
        db.add(fix)
        db.commit()
        fix_id, slug = fix.id, project.slug
    try:
        done = client.post(f"/api/remediations/{fix_id}/approve?project={slug}", headers=login(client, "ceo@acme.example"))
        assert done.status_code == 200, done.text
        assert sent == ["WH-A-999"] and done.json()["factory_run_id"] == "WH-A-999-1"
    finally:
        with SessionLocal() as db:
            db.execute(delete(Remediation).where(Remediation.id == fix_id))
            db.commit()

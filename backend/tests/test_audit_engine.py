"""AUD.0 to AUD.3: the audit engine paces and retries its model calls, agents finish only with a checked answer and
resume from disk, the ledger it builds passes the method's own validator, and a finished run becomes an audit, a
finding and a fix whose proof is the finding's own proof check."""

import json

import httpx
import pytest
from sqlalchemy import delete, select

from app.audit import agents, azure, conductor, store, tools
from app.db import SessionLocal
from app.models import Audit, Project, Remediation, Signal
from app.services import fix_jobs

# ------------------------------------------------------------------ the model client


def _client(tmp_path, handler, second: bool = False) -> azure.Client:
    client = azure.Client(tmp_path / "usage.jsonl", endpoint="https://example.openai.azure.com/openai/v1", key="k",
                          endpoint_2="https://second.services.ai.azure.com/openai/v1" if second else "",
                          key_2="k2" if second else "")
    client.transport = httpx.MockTransport(handler)
    return client


def _ok(text="done") -> httpx.Response:
    return httpx.Response(200, json={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 400}, "output_tokens": 100}})


def test_rate_limits_and_server_errors_are_retried_with_backoff_then_recorded(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(azure.time, "sleep", slept.append)
    answers = iter([httpx.Response(429, headers={"retry-after-ms": "1500"}, json={"error": {}}),
                    httpx.Response(503, json={"error": {}}), _ok()])
    client = _client(tmp_path, lambda req: next(answers))
    data = client.respond("terra", {"input": [], "max_output_tokens": 100}, "hunter-01")
    assert data["output"][0]["type"] == "message"
    assert slept[0] >= 1.5  # the service's own retry-after is honoured as the least wait
    totals = client.totals()
    assert totals["models"]["terra"]["retries"] == 2 and totals["models"]["terra"]["calls"] == 1
    terra = azure.MODELS["terra"]
    assert totals["cost"] == pytest.approx(terra.cost(1000, 400, 100), abs=1e-4)
    logged = json.loads((tmp_path / "usage.jsonl").read_text().splitlines()[0])
    assert logged["agent"] == "hunter-01" and logged["cached"] == 400


def test_a_call_past_its_deadline_is_abandoned_and_retried(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(azure.time, "sleep", lambda s: None)
    release, calls = threading.Event(), []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            release.wait(5)  # a connection that never answers
        return _ok()

    monkeypatch.setitem(azure.MODELS, "luna", azure.Model("luna", "gpt-6-luna", 0.1, 0.01, 0.5, 125_000, 125, 0.3))
    monkeypatch.setattr(azure, "CONNECT_TIMEOUT_S", 0.1)
    client = _client(tmp_path, handler)
    assert client.respond("luna", {"input": [], "max_output_tokens": 10}, "recon-1a")["output"]
    assert len(calls) == 2 and client.totals()["models"]["luna"]["retries"] == 1
    release.set()


def test_each_timeout_gives_the_next_try_a_longer_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(azure.time, "sleep", lambda s: None)
    deadlines = []

    def post(url, key, body, deadline):
        deadlines.append(deadline)
        if len(deadlines) < 4:
            raise azure.DeadlineExceeded("slow")
        return _ok()

    client = _client(tmp_path, lambda req: _ok())
    monkeypatch.setattr(client, "_post", post)
    client.respond("terra", {"input": [], "max_output_tokens": 10}, "verifier-07")
    base = azure.MODELS["terra"].read_timeout_s
    assert deadlines == [base, base * 2, base * 3, base * 3]


def test_a_bad_request_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(azure.time, "sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(400, json={"error": {"message": "bad tool schema"}})

    with pytest.raises(azure.ModelError, match="400"):
        _client(tmp_path, handler).respond("luna", {"input": []}, "recon-1a")
    assert len(calls) == 1


def test_a_full_minute_makes_the_next_request_wait(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(azure.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(azure.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    pace = azure.Pace(azure.Model("t", "t", 1, 1, 1, tokens_per_minute=1000, requests_per_minute=100))
    pace.acquire(700)
    pace.acquire(700)  # 1,400 tokens do not fit under 80% of 1,000 in one minute
    assert clock[0] >= 160.0


def test_a_refused_call_moves_to_the_next_model_in_its_route(tmp_path):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((req.url.host, body["model"], "reasoning" in body, [i.get("type") for i in body["input"]]))
        if body["model"] == "DeepSeek-V4-Pro":
            return httpx.Response(400, json={"error": {"code": "cyber_policy", "message": "flagged"}})
        return _ok()

    client = _client(tmp_path, handler, second=True)
    body = {"input": [{"type": "reasoning", "encrypted_content": "x"}, {"role": "user", "content": "t"}],
            "reasoning": {"effort": "high"}, "max_output_tokens": 100}
    assert client.respond("strong", body, "verifier-03")["output"]
    # DeepSeek first, on the second resource, without the GPT-only reasoning setting or encrypted reasoning items
    assert seen[0] == ("second.services.ai.azure.com", "DeepSeek-V4-Pro", False, [None])
    # then Terra on the first resource, which gets the reasoning back
    assert seen[1] == ("example.openai.azure.com", "gpt-6.1-terra", True, ["reasoning", None])
    totals = client.totals()["models"]
    assert totals["pro"]["detours"] == 1 and totals["terra"]["calls"] == 1


def test_a_request_larger_than_a_models_minute_skips_it_and_the_real_limit_comes_from_headers(tmp_path):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content)["model"])
        return httpx.Response(200, headers={"x-ratelimit-limit-tokens": "900000", "x-ratelimit-limit-requests": "300",
                                            "x-ratelimit-remaining-tokens": "899000"}, json=_ok().json())

    client = _client(tmp_path, handler, second=True)
    big = {"input": [{"role": "user", "content": "x" * 80_000}], "max_output_tokens": 16_000}  # ~36K > 16K usable
    client.respond("strong", big, "hunter-04")
    assert seen == ["gpt-6.1-terra"]  # DeepSeek's 20K minute cannot hold it, so it was not even tried
    client.respond("pro", {"input": [], "max_output_tokens": 10}, "hunter-04")
    assert client.paces["pro"].tokens_per_minute == 900_000  # learned from the answer: the next big call fits
    client.respond("strong", big, "hunter-04")
    assert seen[-1] == "DeepSeek-V4-Pro"


def test_a_model_that_keeps_throttling_hands_the_call_on(tmp_path, monkeypatch):
    monkeypatch.setattr(azure.time, "sleep", lambda s: None)
    monkeypatch.setattr(azure.Pace, "pause", lambda self, seconds: None)
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(429, json={"error": {}}) if req.url.host.startswith("example") else _ok()

    client = _client(tmp_path, handler)  # no second resource: luna2 and pro are left out of the route
    assert client.route("cheap") == ["luna"]
    with pytest.raises(azure.ModelError):  # the only model in the route keeps its full retries, then fails
        client.respond("cheap", {"input": [], "max_output_tokens": 10}, "recon-1a")
    assert len(seen) == azure.MAX_ATTEMPTS
    seen.clear()
    client = _client(tmp_path, handler, second=True)
    client.respond("cheap", {"input": [], "max_output_tokens": 10}, "recon-1a")
    # three throttled answers, then the same model on the second resource
    assert seen == ["example.openai.azure.com"] * azure.SPILL_AFTER + ["second.services.ai.azure.com"]
    assert client.totals()["models"]["luna2"]["calls"] == 1


# ------------------------------------------------------------------ the agent loop


class Scripted:
    """A stand-in model: each call returns the next scripted output list."""

    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), 0

    def respond(self, model_key, body, agent_id):
        self.calls += 1
        return {"output": self.outputs.pop(0), "usage": {}}


def _call(name, args, call_id):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": call_id}


def test_an_agent_reads_is_told_why_an_answer_fails_and_a_rerun_returns_the_kept_answer(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "app.py").write_text("def handler():\n    return 1\n")
    snap = tools.Snapshot.at(tmp_path / "repo")
    model = Scripted([
        [_call("read_file", {"path": "app.py", "start_line": 1, "end_line": None}, "c1")],
        [_call("submit", {"json": json.dumps({"facts": ""})}, "c2")],
        [_call("submit", {"json": json.dumps({"facts": "app.py:1 defines handler"})}, "c3")],
    ])
    spec = agents.Spec(agent_id="recon-1a", model="luna", instructions="i", task="t", submit_description="s",
                       check=lambda a: [] if a.get("facts") else ["facts must not be empty"])
    run_dir = tmp_path / "run"
    assert agents.run(model, snap, run_dir, spec) == {"facts": "app.py:1 defines handler"}
    lines = [json.loads(x) for x in (run_dir / "agents/recon-1a/transcript.jsonl").read_text().splitlines()]
    assert lines[0]["output"][0]["call"] == "read_file"
    assert agents.run(Scripted([]), snap, run_dir, spec)["facts"]  # resumed from disk: no model call


def test_reading_stays_inside_the_snapshot(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "a.py").write_text("x = 1\n")
    (tmp_path / "secret").write_text("no")
    (tmp_path / "repo" / "link").symlink_to(tmp_path / "secret")
    snap = tools.Snapshot.at(tmp_path / "repo")
    assert snap.files == ["a.py"]
    for bad in ("../secret", "/etc/passwd", "link", ".git/config"):
        with pytest.raises(tools.ToolError):
            tools.read_file(snap, bad)


# ------------------------------------------------------------------ the ledger


def test_the_ledger_built_from_a_plan_passes_the_methods_validator(tmp_path):
    plan = {"excluded_companions": [{"file": "MEMORY-SAFETY-AND-BINARY.md", "reason": "Pure Python, no native code."}],
            "units": [
                {"surface": "GET /orders/{id}", "surface_ref": "shop/http_api.py#GET /orders/{order_id}",
                 "boundary": "Order owner check", "boundary_ref": "shop/orders.py#get_order",
                 "attack_class_ref": "ATTACK-CLASSES.md#Access control", "starting_paths": ["shop/orders.py"]},
                {"surface": "Worker role", "surface_ref": "terraform/modules/worker/main.tf#aws_iam_role.worker",
                 "boundary": "IAM policy", "boundary_ref": "terraform/modules/worker/main.tf#worker policy",
                 "attack_class_ref": "CLOUD-AND-DEPLOYMENT.md#Workload identity overreach",
                 "starting_paths": ["terraform/modules/worker/main.tf"]}]}
    units = conductor._build_units(plan)
    assert units[0]["coverage_id"] < units[1]["coverage_id"]
    assert "%20" in units[0]["coverage_id"] and "::" in units[0]["coverage_id"]
    cloud = next(u for u in units if u["ordinary_attack_class_block"] is None)
    assert "CLOUD-AND-DEPLOYMENT.md#Validation rules" in cloud["selected_companion_blocks"]
    path = tmp_path / "coverage-ledger.json"
    path.write_text(json.dumps([{k: v for k, v in u.items() if k != "priority"} for u in units]))
    assert conductor._node("validate-coverage-ledger.cjs", path) == []


# ------------------------------------------------------------------ a finished run in WhiteHats

FINGERPRINT = "shop/orders.py:get_order:missing-owner-check"
RECORD = {
    "verdict": "confirmed", "fingerprint": FINGERPRINT, "title": "Any customer can read another customer's order",
    "description": "GET /orders/{id} returns the order without checking who owns it.",
    "root_cause": "get_order no longer compares the order's customer with the caller.",
    "intended_behavior": "Only the order's owner can read it.",
    "trace": [{"kind": "entrypoint", "file": "app.py", "line": 1, "scope": "handler", "description": "request"},
              {"kind": "sink", "file": "app.py", "line": 2, "scope": "allowed", "description": "no owner check"}],
    "evidence": [{"file": "app.py", "line": 2, "description": "returns True for anyone"}],
    "conditions": [{"kind": "authentication_level", "description": "any signed-in customer"}],
    "execution": {"attacker_perspective": "a signed-in customer", "payloads": ["order id of another customer"],
                  "instructions": ["call allowed() as another customer"], "observed_result": "allowed() was True"},
    "remediation": {"strategy": "Return not found unless the caller owns the order."},
    "severity": {"likelihood": {"score": "high", "reason": "any customer"},
                 "impact": {"score": "high", "reason": "other customers' orders"}, "overall_severity": "high"},
    "confidence": {"score": "high", "reason": "reproduced locally"}}
PROOF = {"files": [{"name": "check.py", "content": (
    "import sys\nsys.path.insert(0, '/target')\nimport app\n"
    "print('another customer allowed' if app.allowed() else 'another customer refused')\n"
    "sys.exit(1 if app.allowed() else 0)\n")}], "command": "python check.py", "workdir": "",
    "explains": "It asks whether another customer is allowed; it fails while they are."}


@pytest.fixture
def finished_run(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("def handler():\n    return allowed()\ndef allowed():\n    return True\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run-metadata.json").write_text(json.dumps({"target": str(target), "profile": "quick",
                                                          "source_ref": "abc1234", "run_status": "complete"}))
    (run_dir / "summary.json").write_text(json.dumps({"status": "complete", "coverage_percent": 80, "cost": 3.21,
                                                      "counts": {"confirmed": 1, "needs_validation": 0,
                                                                 "rejected": 2}}))
    (run_dir / "findings.json").write_text(json.dumps([RECORD]))
    (run_dir / "proofs.json").write_text(json.dumps({FINGERPRINT: PROOF}))
    (run_dir / "lineage.json").write_text(json.dumps({FINGERPRINT: {"hunter": "hunter-01", "verifier": "verifier-01"}}))
    (run_dir / "agents" / "verifier-01").mkdir(parents=True)
    (run_dir / "agents" / "verifier-01" / "session.json").write_text(json.dumps(
        {"notes": ["Reproduced with customer B reading A's order."]}))
    yield run_dir
    with SessionLocal() as db:
        db.execute(delete(Remediation).where(Remediation.factory_intent.like("WH-A-%")))
        db.execute(delete(Signal).where(Signal.source == store.SOURCE))
        db.execute(delete(Audit).where(Audit.run_dir == str(run_dir)))
        db.commit()


def _import(run_dir):
    with SessionLocal() as db:
        project = db.scalars(select(Project).order_by(Project.id)).first()
        audit = store.import_run(db, project.org_id, project.id, run_dir, "onboarding", "acme/shop", branch="main",
                                 trigger_detail="AWS and GitHub connected")
        return audit.id, project.org_id


def test_a_finished_run_becomes_an_audit_a_finding_and_a_fix_once(client, finished_run):
    audit_id, _ = _import(finished_run)
    _import(finished_run)  # importing again adds nothing
    with SessionLocal() as db:
        audit = db.get(Audit, audit_id)
        assert (audit.status, audit.coverage_percent, audit.cost_usd) == ("complete", 80, 3.21)
        assert audit.counts["rejected"] == 2 and (finished_run / "snapshot" / "app.py").exists()
        signals = db.scalars(select(Signal).where(Signal.audit_id == audit_id)).all()
        assert len(signals) == 1 and signals[0].severity == "high" and signals[0].entity == "app.py:2"
        fixes = db.scalars(select(Remediation).where(Remediation.signal_id == signals[0].id)).all()
        assert len(fixes) == 1 and fixes[0].destination == "Fix Factory" and fixes[0].status == "pending"
        made = fix_jobs.intent(db, fixes[0], [{"name": "shop", "source": "github:acme/shop",
                                               "deliver": "pull_request"}])
    assert made["proof"] == {"recheck": {"rule": FINGERPRINT, "scanner": store.SCANNER, "path": "app.py"}}
    assert made["base"] == "main" and "Return not found" in made["summary"]
    handoff = made["handoff"]
    assert handoff["record"]["fingerprint"] == FINGERPRINT and handoff["proof"]["files"][0]["name"] == "check.py"
    assert handoff["notes"] == ["From the verifier (verifier-01): Reproduced with customer B reading A's order."]


def test_a_repository_the_engine_does_not_have_is_refused_with_the_reason(client, finished_run):
    audit_id, _ = _import(finished_run)
    with SessionLocal() as db:
        fix = db.scalar(select(Remediation).join(Signal, Signal.id == Remediation.signal_id)
                        .where(Signal.audit_id == audit_id))
        with pytest.raises(fix_jobs.NotFixable, match="not set up for acme/shop"):
            fix_jobs.intent(db, fix, [])


@pytest.mark.skipif(bool(tools.sandbox_ready()), reason="needs Docker and the audit sandbox image")
def test_the_proof_check_fails_on_the_audited_code_and_passes_on_the_fix(client, finished_run):
    _, org_id = _import(finished_run)
    with SessionLocal() as db:
        still = store.recheck(db, org_id, FINGERPRINT, {"app.py": (finished_run / "snapshot/app.py").read_text()})
        fixed = store.recheck(db, org_id, FINGERPRINT, {"app.py": "def handler():\n    return allowed()\n"
                                                                 "def allowed():\n    return False\n"})
    assert still["fixed"] is False and "another customer allowed" in still["still"][0]["message"]
    assert fixed["fixed"] is True and "another customer refused" in fixed["message"]
    assert (finished_run / "snapshot/app.py").read_text().endswith("return True\n")  # the snapshot is never changed


def test_the_events_log_records_each_step(tmp_path):
    run = conductor.Run(target=tmp_path, out=tmp_path / "out", repo="r", source_ref="s")
    run.say("map", "4 units planned.", units=[])
    entry = json.loads((tmp_path / "out" / "events.jsonl").read_text())
    assert entry["step"] == "map" and entry["message"] == "4 units planned."


# ------------------------------------------------------------------ triggers (AUD.5)


@pytest.fixture
def project_without_audits(client):
    from app.services import audit_jobs  # noqa: F401 - imported for the monkeypatch targets below
    with SessionLocal() as db:
        project = db.scalars(select(Project).order_by(Project.id.desc())).first()
        db.execute(delete(Audit).where(Audit.project_id == project.id))
        db.commit()
        yield project.id
        db.execute(delete(Audit).where(Audit.project_id == project.id))
        db.commit()


def test_the_first_map_with_code_queues_one_onboarding_audit_and_a_cloud_change_a_focused_one(
        project_without_audits, monkeypatch):
    from types import SimpleNamespace

    from app.services import audit_jobs, auto_map
    monkeypatch.setattr(auto_map, "project_connection_keys", lambda db, project: ["aws", "github"])
    monkeypatch.setattr(audit_jobs, "connection_prints", lambda db, project_id: {  # AUD.6: what the audit reads
        "aws:arn:aws:iam::1:role/r": "AWS account 1 (role r)", "github:acme/shop": "GitHub repository acme/shop"})
    started = []
    monkeypatch.setattr(audit_jobs, "wake", lambda: started.append(1))
    aws, github = SimpleNamespace(key="aws", name="AWS"), SimpleNamespace(key="github", name="GitHub")
    with SessionLocal() as db:
        project = db.get(Project, project_without_audits)
        auto_map._audit_after(db, project, github, "the hourly read")
        auto_map._audit_after(db, project, github, "the hourly read")  # still one onboarding audit
        auto_map._audit_after(db, project, aws, "the hourly read")  # an hourly read is not a change
        auto_map._audit_after(db, project, aws, "AWS saw UpdateStage")
        auto_map._audit_after(db, project, aws, "AWS saw PutBucketPolicy")  # joins the waiting one
        rows = db.scalars(select(Audit).where(Audit.project_id == project.id).order_by(Audit.id)).all()
    assert [(r.trigger, r.status) for r in rows] == [("onboarding", "queued"), ("cloud_change", "queued")]
    assert rows[1].trigger_detail == "AWS saw UpdateStage; AWS saw PutBucketPolicy"
    assert len(started) == 2


def test_a_project_without_code_is_not_audited(project_without_audits, monkeypatch):
    from types import SimpleNamespace

    from app.services import auto_map
    monkeypatch.setattr(auto_map, "project_connection_keys", lambda db, project: ["aws"])
    with SessionLocal() as db:
        auto_map._audit_after(db, db.get(Project, project_without_audits), SimpleNamespace(key="aws", name="AWS"),
                              "AWS saw UpdateStage")
        assert db.scalar(select(Audit.id).where(Audit.project_id == project_without_audits)) is None


def test_a_refused_turn_withholds_the_newest_output_and_the_agent_carries_on(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "notes.md").write_text("how a role could be misused\n")
    snap = tools.Snapshot.at(tmp_path / "repo")

    class Filtered(Scripted):
        def respond(self, model_key, body, agent_id):
            self.calls += 1
            if self.calls == 2:
                raise azure.PolicyBlocked("cyber_policy")
            if self.calls == 3:
                assert any(i.get("output", "").startswith("[Withheld") for i in body["input"])
            return {"output": self.outputs.pop(0), "usage": {}}

    model = Filtered([
        [_call("read_file", {"path": "notes.md", "start_line": 1, "end_line": None}, "c1")],
        [_call("submit", {"json": json.dumps({"facts": "notes.md withheld by the model provider's filter"})}, "c2")],
    ])
    spec = agents.Spec(agent_id="hunter-09", model="terra", instructions="i", task="t", submit_description="s",
                       check=lambda a: [])
    assert "withheld" in agents.run(model, snap, tmp_path / "run", spec)["facts"]
    log = (tmp_path / "run/agents/hunter-09/transcript.jsonl").read_text()
    assert '"withheld": ["read_file' in log


# ------------------------------------------------------------------ sessions


def test_a_crash_mid_conversation_resumes_at_the_saved_turn(tmp_path):
    from app.audit import sessions
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "app.py").write_text("x = 1\n")
    snap = tools.Snapshot.at(tmp_path / "repo")
    spec = agents.Spec(agent_id="hunter-01", model="terra", instructions="i", task="t", submit_description="s",
                       check=lambda a: [])

    class Crashes(Scripted):
        def respond(self, model_key, body, agent_id):
            if self.calls == 2:
                raise KeyboardInterrupt  # the process dies mid-audit
            return super().respond(model_key, body, agent_id)

    first = Crashes([[_call("read_file", {"path": "app.py", "start_line": 1, "end_line": None}, "c1")],
                     [_call("list_files", {"path": ""}, "c2")]])
    with pytest.raises(KeyboardInterrupt):
        agents.run(first, snap, tmp_path / "run", spec)
    saved = sessions.Session.open(tmp_path / "run/agents/hunter-01/session.json", [])
    assert saved.turn == 2 and saved.items[-1]["output"] == "app.py"

    seen = []

    class Resumes(Scripted):
        def respond(self, model_key, body, agent_id):
            seen.append(body["input"])
            return super().respond(model_key, body, agent_id)

    done = agents.run(Resumes([[_call("submit", {"json": json.dumps({"ok": True})}, "c3")]]), snap,
                      tmp_path / "run", spec)
    assert done == {"ok": True} and len(seen) == 1
    assert any(i.get("output", "").startswith("    1  x = 1") for i in seen[0])  # it carried on from turn 2


def test_a_large_conversation_is_compacted_into_a_handoff_note(tmp_path, monkeypatch):
    from app.audit import sessions
    monkeypatch.setattr(sessions, "COMPACT_AT", 500)
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "big.py").write_text("\n".join(f"line_{n} = {n}" for n in range(200)) + "\n")
    snap = tools.Snapshot.at(tmp_path / "repo")
    sent = []
    note = "## Files read\n- big.py: 200 assignments, nothing reaches a sink (big.py:1-200).\n" + "x" * 300

    class WithNotes(Scripted):
        def respond(self, model_key, body, agent_id):
            sent.append((model_key, agent_id, body["input"]))
            if agent_id.endswith("-compact"):
                return {"output": [{"type": "message", "content": [{"type": "output_text", "text": note}]}]}
            return super().respond(model_key, body, agent_id)

    model = WithNotes([[_call("read_file", {"path": "big.py", "start_line": 1, "end_line": None}, "c1")],
                       [_call("submit", {"json": json.dumps({"ok": True})}, "c2")]])
    spec = agents.Spec(agent_id="hunter-02", model="terra", instructions="i", task="t", submit_description="s",
                       check=lambda a: [])
    assert agents.run(model, snap, tmp_path / "run", spec) == {"ok": True}
    compact_call = next(s for s in sent if s[1] == "hunter-02-compact")
    assert compact_call[0] == "cheap"
    after = sent[-1][2]
    assert len(after) == 3 and after[2]["content"].startswith("Handoff note 1") and note in after[2]["content"]
    saved = sessions.Session.open(tmp_path / "run/agents/hunter-02/session.json", [])
    assert saved.compactions == 1 and saved.notes == [note]


def test_a_large_task_alone_does_not_compact_every_turn(tmp_path):
    from app.audit import sessions
    big_task = {"role": "user", "content": "x" * (sessions.COMPACT_AT * 5)}
    s = sessions.Session(path=tmp_path / "s.json", items=[{"role": "developer", "content": "i"}, big_task])
    assert not sessions.due(s)
    s.items.append({"type": "function_call_output", "call_id": "c", "output": "y" * (sessions.COMPACT_AT * 4)})
    assert sessions.due(s)


def test_leads_ending_at_the_same_sink_are_merged_and_units_relinked(tmp_path):
    run = conductor.Run(target=tmp_path, out=tmp_path / "out", repo="r", source_ref="s")
    sink = {"kind": "sink", "file": "gatekeeper/authorizer.py", "line": 40, "scope": "expected_token",
            "description": "cached"}
    candidates = {
        "a:token-cache": {"proposed_verdict": "needs_validation", "trace": [sink], "_units": ["u1"]},
        "b:rotated-token": {"proposed_verdict": "confirmed", "trace": [{**sink, "line": 41, "scope": "Expected_Token "}],
                            "_units": ["u2"]},
        "c:other": {"proposed_verdict": "confirmed", "trace": [{**sink, "scope": "handler"}], "_units": ["u3"]},
    }
    ledger = {u: {"result_fingerprints": [fp]} for u, fp in (("u1", "a:token-cache"), ("u2", "b:rotated-token"),
                                                            ("u3", "c:other"))}
    kept = conductor.merge_duplicates(run, ledger, candidates)
    assert sorted(kept) == ["b:rotated-token", "c:other"]  # the proposed confirmation is kept
    assert kept["b:rotated-token"]["_units"] == ["u1", "u2"]
    assert ledger["u1"]["result_fingerprints"] == ["b:rotated-token"]


def test_a_retried_agent_starts_a_fresh_conversation(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "a.py").write_text("x = 1\n")
    snap = tools.Snapshot.at(tmp_path / "repo")
    run = conductor.Run(target=tmp_path / "repo", out=tmp_path / "out", repo="r", source_ref="s")
    spec = agents.Spec(agent_id="critic-1", model="terra", instructions="i", task="t", submit_description="s",
                       check=lambda a: [], max_turns=1)
    looping = Scripted([[_call("list_files", {"path": ""}, f"c{n}")] for n in range(5)]
                       + [[_call("submit", {"json": json.dumps({"ok": True})}, "done")]])

    class Client(Scripted):
        def totals(self):
            return {"cost": 0.0}

    client = Client(looping.outputs)
    assert conductor._guarded(run, client, snap, spec) == {"ok": True}  # the second attempt began again and finished
    kept = sorted(p.name for p in (tmp_path / "out/agents/critic-1").iterdir())
    assert any(n.startswith("session.failed-") for n in kept) and "result.json" in kept


def test_confirmed_findings_reached_two_ways_are_merged_after_verification(tmp_path):
    """From a real run: verifiers came back with the order lookup twice and the unsigned payment jobs twice."""
    def rec(fp, sev, steps, verdict="confirmed"):
        return {"fingerprint": fp, "verdict": verdict, "severity": {"overall_severity": sev},
                "trace": [{"file": f, "scope": s} for f, s in steps]}
    auth = ("gatekeeper/authorizer.py", "authorize")
    jobs = [auth, ("shop/http_api.py", "_post_webhook"), ("shop/webhook.py", "accept_webhook"),
            ("worker/jobs.py", "handle_record"), ("worker/aws_gateway.py", "update_order_status")]
    records = [
        rec("orders-a", "high", [("shop/http_api.py", "ROUTES"), ("shop/http_api.py", "_get_order and _get_receipt"),
                                 ("shop/orders.py", "get_order"), ("shop/http_api.py", "_get_receipt")]),
        rec("orders-b", "high", [("terraform/modules/api/main.tf", "default API route"), auth,
                                 ("shop/http_api.py", "_get_order and _get_receipt"), ("shop/orders.py", "get_order"),
                                 ("shop/receipts.py", "read_receipt")]),
        rec("jobs-a", "high", jobs), rec("jobs-b", "high", jobs[:]),
        rec("authorizer", "high", [auth, ("gatekeeper/authorizer.py", "cache")]),  # shares one step only: kept
        rec("rejected", None, jobs, verdict="rejected"),
    ]
    ledger = {"u1": {"result_fingerprints": ["orders-a", "jobs-b"]}, "u2": {"result_fingerprints": []}}
    run = conductor.Run(target=tmp_path, repo="r", source_ref="x", out=tmp_path)
    kept = conductor.merge_confirmed(run, ledger, records)
    assert sorted(r["fingerprint"] for r in kept) == ["authorizer", "jobs-a", "orders-b", "rejected"]
    assert ledger["u1"]["result_fingerprints"] == ["jobs-a", "orders-b"]
    assert json.loads((tmp_path / "merged-findings.json").read_text()) == {"orders-a": "orders-b", "jobs-b": "jobs-a"}


# ------------------------------------------------------------------ AUD.5: the AI review of a pull request


def test_a_pull_requests_review_follows_its_newest_commit_while_it_waits(project_without_audits, monkeypatch):
    from app.services import audit_jobs
    monkeypatch.setattr(audit_jobs, "wake", lambda: None)
    with SessionLocal() as db:
        org = db.get(Project, project_without_audits).org_id
        first = audit_jobs.request(db, org, project_without_audits, "code_change", "PR #7 at aaa",
                                   ["shop/orders.py"], change_id=1, head_sha="a" * 40, pull_number=7, branch="feat")
        again = audit_jobs.request(db, org, project_without_audits, "code_change", "PR #7 at bbb",
                                   ["shop/http_api.py", "shop/orders.py"], change_id=2, head_sha="b" * 40,
                                   pull_number=7, branch="feat")
        other = audit_jobs.request(db, org, project_without_audits, "code_change", "PR #8",
                                   ["worker/jobs.py"], change_id=3, head_sha="c" * 40, pull_number=8, branch="x")
        assert again.id == first.id and other.id != first.id
        assert (again.head_sha, again.change_id, again.profile) == ("b" * 40, 2, "change")
        assert again.scope == ["shop/http_api.py", "shop/orders.py"]


def _confirmed(title, path, line, severity="high"):
    return {"verdict": "confirmed", "fingerprint": f"fp-{line}", "title": title, "description": "Any customer sets "
            "their own price.", "root_cause": "The price comes from the request.", "severity": {
                "overall_severity": severity}, "trace": [{"kind": "sink", "file": path, "line": line,
                                                          "scope": "create_order", "description": "stored"}]}


def test_the_review_check_reports_confirmed_weaknesses_without_blocking_and_comments_on_their_lines():
    from app.services import audit_jobs
    conclusion, title, summary, annotations, comments = audit_jobs.review_output(
        [_confirmed("Customers set their own order price", "shop/orders.py", 46),
         {"verdict": "rejected", "fingerprint": "no", "title": "Not real"}], "complete")
    assert conclusion == "neutral" and title == "1 security weakness confirmed in this change"
    assert "Customers set their own order price" in summary and "Not real" not in summary
    assert annotations[0]["path"] == "shop/orders.py" and annotations[0]["annotation_level"] == "failure"
    assert comments[0]["key"] == "review:fp-46" and comments[0]["line"] == 46
    conclusion, title, *_ = audit_jobs.review_output([], "complete")
    assert (conclusion, title) == ("success", "No security weakness confirmed in this change")


def test_a_pull_request_is_reviewed_after_its_scanners_check_but_fix_factorys_own_are_not(monkeypatch):
    from types import SimpleNamespace

    from app.changes import process
    from app.services import audit_jobs
    asked = []
    monkeypatch.setattr(audit_jobs, "enabled", lambda: True)
    monkeypatch.setattr(audit_jobs, "request", lambda db, *a, **k: asked.append((a, k)))
    from app.db import SessionLocal

    change = SimpleNamespace(pull_number=12, files=["shop/orders.py"], project_ids=[5], org_id=1, id=987654,
                             repo="o/r", branch="feature/prices", head_sha="d" * 40)
    with SessionLocal() as db:
        process._review(db, change)
        (args, kwargs), = asked
        assert args[:3] == (1, 5, "code_change") and kwargs["pull_number"] == 12 and kwargs["head_sha"] == "d" * 40
        for skip in ({"branch": "fix-factory/42"}, {"pull_number": None}, {"files": []}):
            asked.clear()
            process._review(db, SimpleNamespace(**{**vars(change), **skip}))
            assert asked == []


def test_a_review_plans_around_the_change_and_keeps_only_leads_in_the_changed_files():
    task = conductor._plan_task("tree", "facts", ["shop/orders.py"], "Pull request #12", [], diff="+ price_cents")
    assert "This reviews one pull request (Pull request #12)" in task and "+ price_cents" in task
    assert "Between 1 and 8 units" in task
    assert conductor._under("shop/orders.py", "shop/orders.py") and conductor._under("shop/a/b.py", "shop")
    assert not conductor._under("shopping/x.py", "shop")
    one_unit = {"architecture_md": "x" * 500, "units": [{}]}
    assert not any("units must be" in p for p in conductor._check_plan(one_unit, None, 1, 8))
    assert any("units must be" in p for p in conductor._check_plan(one_unit, None))


def test_an_audit_cut_off_by_a_restart_is_queued_again_and_the_queue_is_woken(project_without_audits, monkeypatch):
    from app.services import audit_jobs
    woken = []
    monkeypatch.setattr(audit_jobs, "wake", lambda: woken.append(1))
    with SessionLocal() as db:
        org = db.get(Project, project_without_audits).org_id
        db.add(Audit(org_id=org, project_id=project_without_audits, trigger="onboarding", status="running",
                     step="hunt"))
        db.commit()
        assert audit_jobs.resume_after_restart(db) == 1
        row = db.scalar(select(Audit).where(Audit.project_id == project_without_audits))
        assert (row.status, row.step) == ("queued", "queued") and woken == [1]


def test_a_pull_requests_review_is_not_held_up_by_a_long_audit(project_without_audits, monkeypatch):
    from app.services import audit_jobs
    ran = []
    monkeypatch.setattr(audit_jobs, "run_one", lambda audit_id: ran.append(audit_id) or _finish(audit_id))

    def _finish(audit_id):
        with SessionLocal() as db:
            db.get(Audit, audit_id).status = "complete"
            db.commit()

    with SessionLocal() as db:
        org = db.get(Project, project_without_audits).org_id
        long_one = Audit(org_id=org, project_id=project_without_audits, trigger="cloud_change", status="queued")
        review = Audit(org_id=org, project_id=project_without_audits, trigger="code_change", status="queued")
        db.add_all([long_one, review])
        db.commit()
        ids = (long_one.id, review.id)
    audit_jobs._drain(audit_jobs.REVIEWS)
    assert ran == [ids[1]]  # the review ran; the long audit is left to its own queue

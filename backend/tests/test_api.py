"""End-to-end API behaviour on the seeded demo organisation."""

import time

from sqlalchemy import select

from app.services.llm import Section, pack_context
from app.services.loganalyzer import mask_line


def test_login_rejects_bad_password(client):
    r = client.post("/api/auth/login", json={"email": "ceo@acme.example", "password": "nope"})
    assert r.status_code == 401
    assert client.get("/api/overview").status_code == 401


def test_overview_has_score_and_decisions(client, ceo):
    o = client.get("/api/overview", headers=ceo).json()
    assert 0 <= o["posture"]["score"] <= 1000
    assert any(d["kind"] == "fix" for d in o["decisions"])
    assert "collectors_online" in o["kpis"]


def test_pending_fixes_keep_newest_id_first_when_proposed_together(client, ceo):
    rem = client.get("/api/remediations", headers=ceo).json()
    pending = [r["id"] for r in rem if r["status"] == "pending"]
    assert pending == sorted(pending, reverse=True)


def test_engineer_cannot_approve_fixes(client, engineer):
    rem = client.get("/api/remediations", headers=engineer).json()
    pending = next(r for r in rem if r["status"] == "pending" and r["destination"] != "Fix Factory")
    assert client.post(f"/api/remediations/{pending['id']}/approve", headers=engineer).status_code == 403


def test_ceo_approves_a_fix_and_incident_records_it(client, ceo):
    rem = client.get("/api/remediations", headers=ceo).json()
    r = next(r for r in rem if r["title"] == "Disable the user lookup route")
    out = client.post(f"/api/remediations/{r['id']}/approve", headers=ceo).json()
    assert out["status"] == "dispatched" and out["decided_by"] == "Anita Rao"
    inc = client.get(f"/api/incidents/{r['incident_id']}", headers=ceo).json()
    assert any("Approved: Disable the user lookup route" in e["text"] for e in inc["timeline"])
    assert client.post(f"/api/remediations/{r['id']}/approve", headers=ceo).status_code == 409


def test_mask_line_collapses_variable_parts():
    a = mask_line("Failed password for invalid user admin from 10.0.0.7 port 51122 ssh2")
    b = mask_line("Failed password for invalid user admin from 192.168.4.20 port 40001 ssh2")
    assert a == b and "<*>" in a


def test_pack_context_respects_budget():
    secs = [Section("A", "x " * 400, 1), Section("B", "y " * 4000, 2), Section("C", "z " * 100, 3)]
    p = pack_context(secs, 500)
    assert p.tokens <= 520 and "A" in p.included and "B" in p.trimmed and "C" in p.dropped


def test_log_collector_ingest_digest_and_signals(client, ceo):
    out = client.post("/api/collectors", headers=ceo, json={"name": "test-logs", "kind": "log", "mode": "docker",
                                                             "context_budget": 200}).json()
    token = {"Authorization": f"Bearer {out['token']}"}
    assert "DaemonSet" in out["manifests"]["kubernetes"] and out["token"] not in out["manifests"]["kubernetes"].split(
        "stringData")[0]
    lines = [{"source": "/var/log/auth.log", "message": f"Failed password for invalid user admin from 10.0.0.{i} port {4000+i} ssh2"}
             for i in range(300)]
    lines += [{"source": "/var/log/app.log", "message": f"GET /health 200 in {i}ms"} for i in range(500)]
    assert client.post("/api/ingest/logs", headers=token, json={"host": "web-1", "lines": lines}).json()["accepted"] == 800
    d = client.post("/api/logs/digest", headers=ceo, json={"minutes": 60, "budget": 400}).json()
    assert d["lines"] >= 800 and d["templates"] <= 10 and d["digest_tokens"] <= 420
    assert d["raw_tokens"] > d["digest_tokens"] * 5
    assert d["ai_summary"]
    sigs = client.get("/api/signals", headers=ceo).json()
    assert any(s["source"] == "whitehat:identity" for s in sigs)  # burst of failed sign-ins
    assert any(s["source"] == "whitehat:endpoint" for s in sigs)  # security shape in the digest
    assert client.post("/api/ingest/logs", headers={"Authorization": "Bearer nope"}, json={"lines": []}).status_code == 401


def test_network_snapshot_creates_findings_and_resolves(client, ceo):
    out = client.post("/api/collectors", headers=ceo, json={"name": "test-net", "kind": "network", "mode": "kubernetes"}).json()
    token = {"Authorization": f"Bearer {out['token']}"}
    conns = [{"proto": "tcp", "laddr": "0.0.0.0", "lport": 6379, "state": "LISTEN", "process": "redis-server", "pid": 9},
             {"proto": "tcp", "laddr": "10.0.0.5", "lport": 55000, "raddr": "203.0.113.9", "rport": 4444,
              "state": "ESTABLISHED", "process": "curl", "pid": 10}]
    client.post("/api/ingest/network", headers=token, json={"host": "db-1", "connections": conns})
    net = client.get("/api/network/overview", headers=ceo).json()
    titles = [f["title"] for f in net["findings"] if f["status"] == "open"]
    assert any("Redis" in t for t in titles) and any("4444" in t for t in titles)
    client.post("/api/ingest/network", headers=token, json={"host": "db-1", "connections": []})
    net = client.get("/api/network/overview", headers=ceo).json()
    assert all(f["status"] == "resolved" for f in net["findings"] if f["host"] == "db-1")


def test_assistant_falls_back_without_a_model_key(client, ceo):
    r = client.post("/api/assistant/ask", headers=ceo, json={"question": "What needs my attention today?"}).json()
    assert r["meta"]["fallback"] is True and "Posture" in r["text"]
    assert r["meta"]["context_tokens"] <= r["meta"]["budget"] + 20
    models = client.get("/api/models", headers=ceo).json()
    azure = next(m for m in models["deployments"] if m["deployment"] == "gpt-6-luna")
    assert azure["status"] == "needs_key" and azure["has_key"] is False


def test_model_key_is_write_only(client, ceo):
    m = client.post("/api/models", headers=ceo, json={"name": "test", "provider": "openai_compatible",
                                                      "endpoint": "http://127.0.0.1:9", "deployment": "x",
                                                      "api_key": "sk-test-1234567890", "enabled": False}).json()
    assert m["has_key"] and m["key_hint"].endswith("7890") and "sk-test" not in str(m)
    t = client.post(f"/api/models/{m['id']}/test", headers=ceo).json()
    assert t["ok"] is False and t["status"] == "error"


def test_local_collector_really_runs(client, ceo):
    out = client.post("/api/collectors", headers=ceo, json={"name": "local-proc", "kind": "process", "mode": "local",
                                                             "interval_s": 5}).json()
    cid = out["collector"]["id"]
    # The agent posts back to WH_PUBLIC_URL, which no server listens on in tests, so we only check it started.
    time.sleep(1.5)
    c = client.get(f"/api/collectors/{cid}", headers=ceo).json()
    assert c["pid"] and c["status"] == "running"
    stopped = client.post(f"/api/collectors/{cid}/stop", headers=ceo).json()["collector"]
    assert stopped["status"] == "stopped" and stopped["pid"] is None


def test_board_report_and_plan(client, ceo):
    rep = client.get("/api/reports/board", headers=ceo).json()
    assert rep["narrative"] and rep["facts"]["posture"]["score"] >= 0
    plan = client.get("/api/admin/plan", headers=ceo).json()
    assert plan["current"] == "growth" and plan["usage"]["seats"] >= 4


def test_azure_error_names_only_what_is_missing():
    from app.models import ModelDeployment
    from app.services.llm import azure_gap

    ready = ModelDeployment(endpoint="https://whitehats.openai.azure.com", deployment="gpt-6-luna")
    assert azure_gap(ready, "") == "Add the API key."
    assert azure_gap(ready, "present") == ""
    bare = ModelDeployment(endpoint="", deployment="")
    message = azure_gap(bare, "")
    assert message == "Add the API key, the endpoint and the deployment name."


def test_local_azure_key_is_sealed_and_not_returned(client, ceo, monkeypatch):
    from app.db import SessionLocal
    from app.models import ModelDeployment
    from app.seed import apply_local_azure, resource_root

    monkeypatch.setenv("WH_ENV", "development")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-test-key-not-real")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/openai/v1")
    assert resource_root("https://example.openai.azure.com/openai/v1") == "https://example.openai.azure.com"
    with SessionLocal() as db:
        apply_local_azure(db)
        db.commit()
    models = client.get("/api/models", headers=ceo).json()
    azure = next(m for m in models["deployments"] if m["deployment"] == "gpt-6-luna")
    assert azure["has_key"] is True
    assert azure["endpoint"] == "https://example.openai.azure.com"
    assert "unit-test-key-not-real" not in str(models)
    with SessionLocal() as db:
        dep = db.scalar(select(ModelDeployment).where(ModelDeployment.deployment == "gpt-6-luna"))
        dep.secret_enc = ""
        dep.status = "needs_key"
        dep.endpoint = "https://whitehats.openai.azure.com"
        db.commit()


def test_production_does_not_seal_the_env_key(client, ceo, monkeypatch):
    from app.db import SessionLocal
    from app.seed import apply_local_azure

    monkeypatch.setenv("WH_ENV", "production")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-test-key-not-real")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/openai/v1")
    with SessionLocal() as db:
        apply_local_azure(db)
        db.commit()
    models = client.get("/api/models", headers=ceo).json()
    azure = next(m for m in models["deployments"] if m["deployment"] == "gpt-6-luna")
    assert azure["has_key"] is False


def test_people_investigate_and_say_fixed_and_whitehats_resolves(client, ceo):
    from app.db import SessionLocal
    from app.models import Incident

    rows = client.get("/api/incidents", headers=ceo).json()
    iid = next(i["id"] for i in rows if i["ref"] == "INC-2208")
    resolved = client.patch(f"/api/incidents/{iid}", headers=ceo,
                            json={"status": "resolved", "note": "Closing it now please"})
    assert resolved.status_code == 422  # resolved is WhiteHats' to say, after checking (FD.2)
    short = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"status": "verifying", "note": "short"})
    assert short.status_code == 400
    blocked = client.patch(f"/api/incidents/{iid}", headers=ceo,
                           json={"status": "verifying", "note": "Everything is finished now"})
    assert blocked.status_code == 409 and "Still waiting" in blocked.json()["detail"]
    with SessionLocal() as db:
        db.get(Incident, iid).status = "open"
        db.commit()
    try:
        moved = client.patch(f"/api/incidents/{iid}", headers=ceo,
                             json={"status": "investigating", "note": "The probing host is being looked at"})
        assert moved.status_code == 200 and moved.json()["status"] == "investigating"
        assert any(e["text"] == "The probing host is being looked at" for e in moved.json()["timeline"])
        undone = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"undo": True})
        assert undone.status_code == 200 and undone.json()["status"] == "open"
    finally:
        with SessionLocal() as db:
            db.get(Incident, iid).status = "investigating"
            db.commit()


def test_undo_matches_the_user_once_and_a_dispatched_fix_blocks_resolve(client, ceo):
    from datetime import timedelta
    from pathlib import Path

    from conftest import login

    from app.db import SessionLocal, utcnow
    from app.models import Incident, Remediation
    from app.routers.operate import UNDO_SECONDS

    assert UNDO_SECONDS == 10
    toast = Path(__file__).resolve().parents[2].joinpath("frontend/src/pages/Incidents.jsx").read_text()
    assert "ms: 10000" in toast
    ciso = login(client, "ciso@acme.example")
    note = "Isolated the host before the undo checks"
    with SessionLocal() as db:
        inc = db.scalar(select(Incident).where(Incident.ref == "INC-2208"))
        iid = inc.id
        before = {e.id for e in inc.events}
        saved = [(r.id, r.status) for r in db.scalars(select(Remediation).where(Remediation.incident_id == iid))]

    def undone_count():
        timeline = client.get(f"/api/incidents/{iid}", headers=ceo).json()["timeline"]
        return sum(1 for event in timeline if event["text"].startswith("Undone."))

    try:
        with SessionLocal() as db:
            for rid, _status in saved:
                db.get(Remediation, rid).status = "rejected"
            db.get(Remediation, saved[0][0]).status = "dispatched"
            db.commit()
        blocked = client.patch(f"/api/incidents/{iid}", headers=ceo,
                               json={"status": "verifying", "note": "Everything is finished now"})
        assert blocked.status_code == 409 and "not verified yet" in blocked.json()["detail"]
        with SessionLocal() as db:
            db.get(Remediation, saved[0][0]).status = "rejected"
            db.commit()
        moved = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"status": "verifying", "note": note})
        assert moved.status_code == 200 and moved.json()["status"] == "verifying"

        other = client.patch(f"/api/incidents/{iid}", headers=ciso, json={"undo": True})
        assert other.status_code == 409 and "no status change" in other.json()["detail"]
        with SessionLocal() as db:
            row = db.get(Incident, iid)
            change = next(e for e in reversed(list(row.events)) if e.text.startswith("Status changed from "))
            change.actor = "Meera Pillai"
            db.commit()
        spoofed = client.patch(f"/api/incidents/{iid}", headers=ciso, json={"undo": True})
        assert spoofed.status_code == 409 and "no status change" in spoofed.json()["detail"]

        with SessionLocal() as db:
            db.get(Incident, iid).status = "open"
            db.commit()
        away = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"undo": True})
        assert away.status_code == 409 and "no status change" in away.json()["detail"]
        with SessionLocal() as db:
            db.get(Incident, iid).status = "verifying"
            db.commit()

        before_undo = undone_count()
        restored = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"undo": True})
        assert restored.status_code == 200 and restored.json()["status"] == "investigating"
        again = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"undo": True})
        assert again.status_code == 409
        assert undone_count() == before_undo + 1

        again_moved = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"status": "verifying", "note": note})
        assert again_moved.status_code == 200
        with SessionLocal() as db:
            row = db.get(Incident, iid)
            old = utcnow() - timedelta(seconds=UNDO_SECONDS + 1)
            for event in row.events:
                if event.text.startswith("Status changed from "):
                    event.at = old
            db.commit()
        late = client.patch(f"/api/incidents/{iid}", headers=ceo, json={"undo": True})
        assert late.status_code == 409 and "few seconds" in late.json()["detail"]
        assert undone_count() == before_undo + 1
    finally:
        with SessionLocal() as db:
            row = db.get(Incident, iid)
            row.status = "investigating"
            row.resolved_at = None
            for event in list(row.events):
                if event.id not in before:
                    db.delete(event)
            for rid, status in saved:
                db.get(Remediation, rid).status = status
            db.commit()


def test_own_role_cannot_be_changed_and_a_role_change_is_audited(client, ceo):
    """The last account owner is protected in test_permissions.py."""
    users = client.get("/api/admin/users", headers=ceo).json()
    me = next(u for u in users if u["email"] == "ceo@acme.example")
    meera = next(u for u in users if u["email"] == "ciso@acme.example")
    own = client.patch(f"/api/admin/users/{me['id']}", headers=ceo, json={"role": "analyst"})
    assert own.status_code == 409 and "own role" in own.json()["detail"]
    try:
        changed = client.patch(f"/api/admin/users/{meera['id']}", headers=ceo, json={"role": "analyst"})
        assert changed.status_code == 200
        audit = client.get("/api/admin/audit", headers=ceo).json()
        row = next(a for a in audit if a["action"] == "changed role" and a["target"] == "Meera Pillai")
        assert row["actor"] == "Anita Rao" and row["detail"] == "ciso to analyst"
    finally:
        client.patch(f"/api/admin/users/{meera['id']}", headers=ceo, json={"role": "ciso"})


def test_briefing_count_comes_from_stored_rows(client, ceo):
    """The expected count is counted from the tables, not from the list the API just returned."""
    from sqlalchemy import func

    from app.db import SessionLocal
    from app.models import Collector, Organization, Project, Remediation
    from app.services import deploy

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        main = db.scalar(select(Project.id).where(Project.org_id == org.id, Project.slug == "main"))
        # The Overview is Main's (the Fintech shop's); the other demo projects keep their own fixes.
        pending = db.scalar(select(func.count()).select_from(Remediation).where(
            Remediation.project_id == main, Remediation.status == "pending"))
        collectors = db.scalars(select(Collector).where(Collector.org_id == org.id)).all()
        stopped = sum(1 for collector in collectors if deploy.health(collector) in ("offline", "crashed"))
    expected = pending + stopped
    assert expected >= 1
    overview = client.get("/api/overview", headers=ceo).json()
    assert len(overview["decisions"]) == expected
    text = client.get("/api/overview/summary?refresh=true", headers=ceo).json()["text"]
    assert f"{expected} decisions wait for you" in text


def test_the_briefing_is_written_again_when_its_facts_change_and_reused_while_they_do_not(client, ceo, monkeypatch):
    from app.db import SessionLocal
    from app.models import Incident, Organization
    from app.projects import main_project_id
    from app.services import llm

    written = []
    real = llm.complete
    monkeypatch.setattr("app.routers.core.complete", lambda *args, **kwargs: written.append(1) or real(*args, **kwargs))
    first = client.get("/api/overview/summary?refresh=true", headers=ceo).json()
    assert client.get("/api/overview/summary", headers=ceo).json() == first and len(written) == 1  # same facts: reused
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        incident = Incident(org_id=org.id, project_id=main_project_id(db, org.id), ref="INC-TEST", severity="critical",
                            title="Briefing test: a new critical incident")
        db.add(incident)
        db.commit()
        opened = incident.id
    try:
        score = client.get("/api/overview", headers=ceo).json()["posture"]["score"]
        text = client.get("/api/overview/summary", headers=ceo).json()["text"]
        assert len(written) == 2 and f"Posture is {score}/1000" in text and text != first["text"]
    finally:
        with SessionLocal() as db:
            db.delete(db.get(Incident, opened))
            db.commit()


def _remove_company(slug):
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import AuditLog, LLMCall, Organization, PostureSnapshot, Remediation, User

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == slug))
        if org is None:
            return
        db.execute(delete(LLMCall).where(LLMCall.org_id == org.id))
        db.execute(delete(AuditLog).where(AuditLog.org_id == org.id))
        db.execute(delete(PostureSnapshot).where(PostureSnapshot.org_id == org.id))
        db.execute(delete(Remediation).where(Remediation.org_id == org.id))
        db.execute(delete(User).where(User.org_id == org.id))
        db.delete(org)
        db.commit()


def test_ten_decisions_are_counted_in_full(client):
    from conftest import PASSWORD, add_person, login

    from app.db import SessionLocal
    from app.models import Organization, Remediation, User
    from app.security import hash_password

    _remove_company("countco")
    with SessionLocal() as db:
        org = Organization(name="Count Co", slug="countco", mfa_required=False)
        db.add(org)
        db.flush()
        add_person(db, User(org_id=org.id, email="ceo@countco.test", name="Count CEO", role="ceo",
                    password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at))
        for i in range(10):
            db.add(Remediation(org_id=org.id, title=f"Extra decision {i}", target="unit-test",
                               action="Do the extra thing.", destination="Okta", status="pending"))
        db.commit()
    try:
        headers = login(client, "ceo@countco.test")
        overview = client.get("/api/overview", headers=headers).json()
        assert len(overview["decisions"]) == 10
        text = client.get("/api/overview/summary?refresh=true", headers=headers).json()["text"]
        assert "10 decisions wait for you" in text
        assert "8 decisions wait for you" not in text
    finally:
        _remove_company("countco")


def test_proven_fixes_are_one_decision_in_the_shared_count(client, ceo, monkeypatch):
    monkeypatch.setattr("app.services.decisions.factory.status", lambda org: {
        "connected": True, "summary": {"verified": 3, "approved": 0},
    })
    overview = client.get("/api/overview", headers=ceo).json()
    titles = [item["title"] for item in overview["decisions"]]
    assert titles.count("3 proven code fixes waiting for a person") == 1
    text = client.get("/api/overview/summary?refresh=true", headers=ceo).json()["text"]
    assert f"{len(overview['decisions'])} decisions wait for you" in text


def test_one_proven_fix_is_named_in_the_singular(client, ceo, monkeypatch):
    monkeypatch.setattr("app.services.decisions.factory.status", lambda org: {
        "connected": True, "summary": {"verified": 1, "approved": 0},
    })
    titles = [item["title"] for item in client.get("/api/overview", headers=ceo).json()["decisions"]]
    assert "1 proven code fix waiting for a person" in titles


def test_signals_older_than_seven_days_are_listed_as_earlier(client, ceo):
    from datetime import timedelta

    from app.db import SessionLocal, utcnow
    from app.models import Organization, Signal

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        db.add(Signal(org_id=org.id, severity="low", title="An old quiet signal", origin="sample",
                      created_at=utcnow() - timedelta(days=10)))
        db.commit()
    overview = client.get("/api/overview", headers=ceo).json()
    assert all(item["title"] != "An old quiet signal" for item in overview["signals"])
    assert any(item["title"] == "An old quiet signal" for item in overview["earlier_signals"])


def test_approvals_come_in_pages_with_how_many_there_are_in_all(client, ceo):
    whole = client.get("/api/remediations", headers=ceo).json()
    assert isinstance(whole, list)  # without a page, the whole list as before
    waiting = [r for r in whole if r["status"] == "pending"]
    first = client.get("/api/remediations?status=pending&page=1&size=2", headers=ceo).json()
    assert first["total"] == len(waiting) and first["page"] == 1 and first["size"] == 2
    assert [r["id"] for r in first["items"]] == [r["id"] for r in waiting[:2]]
    decided = [r for r in whole if r["status"] != "pending"]
    last = client.get("/api/remediations?status=decided&page=999&size=2", headers=ceo).json()
    assert last["page"] == max(1, -(-len(decided) // 2))  # past the end: the last page
    assert all(r["status"] != "pending" for r in last["items"])

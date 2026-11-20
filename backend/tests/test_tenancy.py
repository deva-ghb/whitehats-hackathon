"""Operator-led tenant onboarding, end to end, plus isolation, support access, AI policy and cloud logs."""

import json
from datetime import timedelta
from pathlib import Path

import boto3
import pytest
from conftest import code_for, finish
from moto import mock_aws

NEW = {
    "name": "Globex Payments", "slug": "globex", "plan": "growth", "region": "in-south", "domain": "globex.test",
    "mfa_required": True, "retention_log_days": 30, "retention_run_days": 365, "retention_audit_days": 730,
    "ai_policy": {"providers": ["azure_openai", "rules"], "zero_retention": True, "agreed": True},
    "contract": {"order_ref": "GLX-1", "owner_name": "Gita Rao", "owner_email": "gita@globex.test",
                 "security_contact": "Sec desk +91 90000 11111", "pilot": True, "pass_marks": "3 fixes in 30 days"},
    "contracted_agents": ["identity", "network", "endpoint", "cloud"],
}


@pytest.fixture(scope="module")
def globex(client, operator):
    r = client.post("/api/operator/tenants", headers=operator, json=NEW)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_customers_cannot_use_the_operator_console(client, ceo):
    assert client.get("/api/operator/tenants", headers=ceo).status_code == 403


def test_operator_must_use_two_step_sign_in(client):
    r = client.post("/api/auth/login", json={"email": "operator@whitehats.example", "password": "WhiteHats!2026"}).json()
    assert r["step"] in ("mfa_setup", "mfa") and "token" not in r


def test_new_company_starts_as_draft_with_all_checks(client, operator, globex):
    t = client.get(f"/api/operator/tenants/{globex}", headers=operator).json()
    assert t["settings"]["status"] == "draft"
    keys = {c["key"] for c in t["readiness"]["checks"]}
    assert {"contract", "dpa", "domain", "mfa", "admin_ready", "cloud_logs", "alerts", "signoff"} <= keys
    by = {c["key"]: c for c in t["readiness"]["checks"]}
    assert by["contacts"]["status"] == "passed" and by["region"]["status"] == "passed"
    assert by["ai_policy"]["status"] == "passed" and by["tenant_key"]["status"] == "passed"
    assert by["admin_ready"]["status"] == "failed"
    assert t["settings"]["domain_record"].startswith("whitehats-verify=")
    assert not t["readiness"]["ready_for_signoff"]


def test_manual_checks_and_waivers_are_recorded(client, operator, globex):
    for key in ("contract", "dpa", "pass_marks"):
        r = client.post(f"/api/operator/tenants/{globex}/checks/{key}", headers=operator,
                        json={"action": "pass", "note": "Signed copy in the deal room"})
        assert r.status_code == 200
    # Automatic checks cannot be ticked by hand, and waivers need a real reason.
    assert client.post(f"/api/operator/tenants/{globex}/checks/domain", headers=operator,
                       json={"action": "pass"}).status_code == 409
    assert client.post(f"/api/operator/tenants/{globex}/checks/domain", headers=operator,
                       json={"action": "waive", "note": "no"}).status_code == 400
    r = client.post(f"/api/operator/tenants/{globex}/checks/domain", headers=operator,
                    json={"action": "waive", "note": "Test domain cannot carry DNS records"}).json()
    assert {c["key"]: c for c in r["checks"]}["domain"]["status"] == "waived"


def test_go_live_is_blocked_until_ready(client, operator, globex):
    r = client.post(f"/api/operator/tenants/{globex}/go-live", headers=operator)
    assert r.status_code == 409 and "First admin" in r.json()["detail"]


def test_invite_only_for_company_domain_then_admin_joins_with_two_step(client, operator, globex):
    assert client.post(f"/api/operator/tenants/{globex}/invites", headers=operator,
                       json={"email": "someone@gmail.com"}).status_code == 400
    inv = client.post(f"/api/operator/tenants/{globex}/invites", headers=operator,
                      json={"email": "gita@globex.test", "name": "Gita Rao", "role": "ciso"}).json()
    token = inv["link"].rsplit("/", 1)[1]
    info = client.get(f"/api/invites/{token}").json()
    assert info["company"] == "Globex Payments" and info["mfa_required"] is True
    assert client.post(f"/api/invites/{token}/accept", json={"name": "Gita Rao", "password": "short"}).status_code == 422
    res = client.post(f"/api/invites/{token}/accept", json={"name": "Gita Rao", "password": "Globex-Secure-2026"}).json()
    assert res["step"] == "mfa_setup"  # the company requires two-step sign-in
    holder = {}
    done = finish(client, res, holder)
    assert done["needs_terms"] is True and done["org"]["status"] == "onboarding"
    h = {"Authorization": f"Bearer {done['token']}"}
    client.post("/api/auth/terms", headers=h)
    # The invitation cannot be reused, and a wrong code is refused.
    assert client.post(f"/api/invites/{token}/accept", json={"name": "Xavier", "password": "Another-Pass-2026"}).status_code == 409
    step = client.post("/api/auth/login", json={"email": "gita@globex.test", "password": "Globex-Secure-2026"}).json()
    assert step["step"] == "mfa"
    assert client.post("/api/auth/mfa", json={"step_token": step["step_token"], "code": "000000"}).status_code == 401
    ok = client.post("/api/auth/mfa", json={"step_token": step["step_token"], "code": code_for(holder["secret"])})
    assert ok.status_code == 200
    me = client.get("/api/auth/me", headers=h).json()
    assert me["setup"]["status"] == "onboarding" and me["mfa"] is True
    pytest.globex_admin = h


def test_customer_admin_invites_teammates_under_the_same_rules(client, operator, globex):
    h = pytest.globex_admin
    assert client.post("/api/admin/invites", headers=h, json={"email": "x@other.test", "role": "analyst"}).status_code == 400
    inv = client.post("/api/admin/invites", headers=h, json={"email": "Ravi@globex.test", "role": "analyst"})
    assert inv.status_code == 200 and "/invite/whi_" in inv.json()["link"]
    # No second open invitation for the same person, and the operator sees who sent it.
    assert client.post("/api/admin/invites", headers=h, json={"email": "ravi@globex.test", "role": "analyst"}).status_code == 409
    row = next(i for i in client.get("/api/admin/invites", headers=h).json() if i["email"] == "ravi@globex.test")
    assert row["created_by"] == "Gita Rao"
    assert client.post(f"/api/admin/invites/{row['id']}/revoke", headers=h).status_code == 200
    token = inv.json()["link"].rsplit("/", 1)[1]
    assert client.get(f"/api/invites/{token}").status_code == 404
    # Unknown roles are refused. The first admin owns the account, so they may request a plan change.
    assert client.post("/api/admin/invites", headers=h, json={"email": "z@globex.test", "role": "bogus"}).status_code == 422
    r = client.put("/api/admin/plan", headers=h, json={"plan": "enterprise"})
    assert r.status_code == 200, r.text
    t = client.get(f"/api/operator/tenants/{globex}", headers=operator).json()
    assert t["settings"]["plan"] == "growth"


def test_seat_limit_counts_open_invitations(client, ceo):
    from app.db import SessionLocal
    from app.models import Organization
    from app.services import invites

    with SessionLocal() as db:
        org = db.query(Organization).filter_by(slug="acme").one()
        org.plan = "starter"  # 5 seats
        db.commit()
    try:
        made = 0
        for n in range(10):
            r = client.post("/api/admin/invites", headers=ceo, json={"email": f"seat{n}@acme.example", "role": "analyst"})
            if r.status_code == 402:
                break
            made += 1
        with SessionLocal() as db:
            org = db.query(Organization).filter_by(slug="acme").one()
            assert len(invites.open_invites(db, org)) == made
        assert r.status_code == 402
        req = client.put("/api/admin/plan", headers=ceo, json={"plan": "growth"}).json()
        assert "Request sent" in req["message"]
    finally:
        with SessionLocal() as db:
            org = db.query(Organization).filter_by(slug="acme").one()
            org.plan = "growth"
            for i in invites.open_invites(db, org):
                db.delete(i)
            db.commit()


def test_fix_factory_engine_serves_only_its_own_company(client, ceo):
    h = pytest.globex_admin
    st = client.get("/api/factory/status", headers=h).json()
    assert st["connected"] is False and "not set up for your company" in st["message"]
    assert client.get("/api/factory/runs", headers=h).status_code == 409
    assert client.get("/api/factory/inbox", headers=h).status_code == 409
    assert not any(d["kind"] == "factory" for d in client.get("/api/overview", headers=h).json()["decisions"])
    # The demo company owns the engine here; without a running engine it is told how to start it instead.
    assert "not set up for your company" not in client.get("/api/factory/status", headers=ceo).json().get("message", "")


def test_a_company_with_nothing_reporting_does_not_score_as_safe(client):
    posture = client.get("/api/overview", headers=pytest.globex_admin).json()["posture"]
    blind = next(b for b in posture["breakdown"] if b["area"] == "Blind spots")
    assert blind["penalty"] >= 400 and "Nothing is reporting yet" in blind["detail"]
    assert posture["grade"] != "Strong"


def test_tenants_cannot_see_each_others_data(client, ceo):
    h = pytest.globex_admin
    acme_incidents = client.get("/api/incidents", headers=ceo).json()
    assert acme_incidents and client.get("/api/incidents", headers=h).json() == []
    acme_id = acme_incidents[0]["id"]
    assert client.get(f"/api/incidents/{acme_id}", headers=h).status_code == 404


def test_support_access_is_granted_by_the_customer_and_read_only(client, operator, globex):
    h = pytest.globex_admin
    assert client.post(f"/api/operator/tenants/{globex}/support-session", headers=operator).status_code == 403
    client.post("/api/setup/support-access", headers=h, json={"hours": 2, "reason": "Help connecting CloudWatch"})
    s = client.post(f"/api/operator/tenants/{globex}/support-session", headers=operator).json()
    sh = {"Authorization": f"Bearer {s['token']}"}
    assert client.get("/api/setup", headers=sh).status_code == 200
    assert client.put("/api/setup/alerts", headers=sh, json={"kind": "slack", "target": "https://x.test"}).status_code == 403
    audit = client.get("/api/admin/audit", headers=h).json()
    assert any("support session" in a["action"] for a in audit)
    client.delete("/api/setup/support-access", headers=h)
    assert client.get("/api/setup", headers=sh).status_code == 403  # revoked immediately


def test_ai_policy_is_enforced(client, operator, globex):
    from app.db import SessionLocal
    from app.models import ModelDeployment
    from app.services.llm import candidates

    h = pytest.globex_admin
    m = client.post("/api/models", headers=h, json={"name": "Self-hosted", "provider": "openai_compatible",
                                                    "endpoint": "http://127.0.0.1:9", "deployment": "x"}).json()
    with SessionLocal() as db:
        ids = [d.id for d in candidates(db, globex, "assistant")]
        assert m["id"] not in ids  # provider not in the agreed policy
        dep = db.get(ModelDeployment, m["id"])
        assert dep.provider == "openai_compatible"


def test_alert_channel_must_deliver_a_test(client, globex, monkeypatch):
    import httpx

    h = pytest.globex_admin
    client.put("/api/setup/alerts", headers=h, json={"kind": "slack", "target": "https://hooks.slack.test/T/B/X"})

    class FakeRes:
        status_code, text = 200, "ok"

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeRes())
    r = client.post("/api/setup/alerts/test", headers=h)
    assert r.status_code == 200
    s = client.get("/api/setup", headers=h).json()
    assert {c["key"]: c for c in s["readiness"]["checks"]}["alerts"]["status"] == "passed"


@mock_aws
def test_cloudwatch_connector_syncs_into_the_analyzer_and_cloud_whitehat(client, globex):
    h = pytest.globex_admin
    logs = boto3.client("logs", region_name="ap-south-1")
    logs.create_log_group(logGroupName="cloudtrail")
    logs.create_log_stream(logGroupName="cloudtrail", logStreamName="s1")
    import time as t

    now = int(t.time() * 1000)
    events = [
        {"eventName": "StopLogging", "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::1:user/mallory"},
         "awsRegion": "ap-south-1", "recipientAccountId": "111122223333"},
        {"eventName": "AuthorizeSecurityGroupIngress", "userIdentity": {"arn": "arn:aws:iam::1:user/dev"},
         "requestParameters": {"cidrIp": "0.0.0.0/0"}, "awsRegion": "ap-south-1"},
        {"eventName": "ConsoleLogin", "userIdentity": {"type": "Root", "arn": "arn:aws:iam::1:root"},
         "responseElements": {"ConsoleLogin": "Success"}, "sourceIPAddress": "203.0.113.5", "awsRegion": "us-east-1"},
    ]
    logs.put_log_events(logGroupName="cloudtrail", logStreamName="s1",
                        logEvents=[{"timestamp": now - 1000 + i, "message": json.dumps(e)} for i, e in enumerate(events)])
    iam = boto3.client("iam", region_name="ap-south-1")
    iam.create_role(RoleName="whitehats-reader", AssumeRolePolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }))
    role_arn = iam.get_role(RoleName="whitehats-reader")["Role"]["Arn"]
    cons = client.get("/api/connectors", headers=h).json()
    cw = next(c for c in cons if c["key"] == "cloudwatch")
    r = client.put(f"/api/connectors/{cw['id']}", headers=h, json={"values": {
        "region": "ap-south-1", "log_groups": "cloudtrail", "role_arn": role_arn,
        "external_id": "tenancy-external-id"}}).json()
    assert r["status"] == "checked", r["last_check"]
    synced = client.post(f"/api/connectors/{cw['id']}/sync", headers=h).json()
    assert synced["read"] == 3
    assert synced["connector"]["status"] == "reading"
    assert synced["connector"]["last_sync_at"]
    assert synced["connector"]["sync"]["total"] == 3
    again = client.post(f"/api/connectors/{cw['id']}/sync", headers=h).json()
    assert again["read"] == 0  # the cursor means each event is read once
    titles = [s["title"] for s in client.get("/api/signals", headers=h).json()]
    assert "Cloud audit logging was switched off" in titles
    assert "A firewall rule now allows traffic from the whole internet" in titles
    assert "Root account signed in to the cloud console" in titles
    cols = client.get("/api/collectors", headers=h).json()
    assert any(c["mode"] == "cloud" and c["events_total"] == 3 for c in cols)


def test_whitehat_catalog_add_and_planned_refused(client, globex):
    from conftest import PASSWORD, add_person, login

    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password

    owner = pytest.globex_admin
    data = client.get("/api/agents", headers=owner).json()
    assert {a["key"] for a in data["agents"]} == {"identity", "network", "endpoint", "cloud"}
    refused = client.post("/api/agents", headers=owner, json={"key": "insider"})
    assert refused.status_code == 403 and "Engineer" in refused.json()["detail"]  # WhiteHat settings: spec 0A
    with SessionLocal() as db:
        add_person(db, User(org_id=globex, email="eng@globex.test", name="Globex Engineer", role="analyst",
                            password_hash=hash_password(PASSWORD)))
        db.commit()
    h = login(client, "eng@globex.test")
    assert client.post("/api/agents", headers=h, json={"key": "insider"}).status_code == 200
    r = client.post("/api/agents", headers=h, json={"key": "ot"})
    assert r.status_code == 409 and "cannot be switched on yet" in r.json()["detail"]


def test_process_monitor_feeds_endpoint_whitehat(client):
    h = pytest.globex_admin
    out = client.post("/api/collectors", headers=h, json={"name": "srv procs", "kind": "process", "mode": "docker"}).json()
    tok = {"Authorization": f"Bearer {out['token']}"}
    client.post("/api/ingest/processes", headers=tok, json={"host": "srv-9", "processes": [
        {"name": "xmrig", "pid": 4242, "user": "www-data", "cpu": 97.0, "memory_mb": 300}]})
    assert any("xmrig running on srv-9" == s["title"] for s in client.get("/api/signals", headers=h).json())


def test_suspend_blocks_sign_in_then_offboard_export_and_delete(client, operator, globex):
    h = pytest.globex_admin
    client.post(f"/api/operator/tenants/{globex}/suspend", headers=operator, json={"reason": "Unpaid invoice"})
    assert client.get("/api/overview", headers=h).status_code == 403
    client.post(f"/api/operator/tenants/{globex}/resume", headers=operator)
    assert client.get("/api/overview", headers=h).status_code == 200
    assert client.post(f"/api/operator/tenants/{globex}/offboard", headers=operator,
                       json={"confirm_name": "wrong"}).status_code == 400
    off = client.post(f"/api/operator/tenants/{globex}/offboard", headers=operator,
                      json={"confirm_name": "Globex Payments", "retention_days": 30}).json()
    export = json.loads(Path(off["export_path"]).read_text())
    assert export["company"] == "Globex Payments" and "signals" in export["tables"]
    assert "secret_enc" not in json.dumps(export) and "password_hash" not in json.dumps(export)
    assert client.post(f"/api/operator/tenants/{globex}/delete", headers=operator,
                       json={"confirm_name": "Globex Payments"}).status_code == 409  # retention window
    cert = client.post(f"/api/operator/tenants/{globex}/delete", headers=operator,
                       json={"confirm_name": "Globex Payments", "early": True}).json()
    assert cert["rows_deleted"]["signals"] >= 1 and cert["export_sha256"] == off["sha256"]
    t = client.get(f"/api/operator/tenants/{globex}", headers=operator).json()
    assert t["settings"]["status"] == "deleted" and t["users"] == []


def test_retention_removes_old_raw_lines(client, operator, ceo):
    from app.db import SessionLocal, utcnow
    from app.models import LogEvent

    with SessionLocal() as db:
        old = db.query(LogEvent).first()
        if old is None:
            pytest.skip("no log lines in this run")
        old.ts = utcnow() - timedelta(days=400)
        db.commit()
        old_id = old.id
    client.post("/api/operator/maintenance/retention", headers=operator)
    with SessionLocal() as db:
        assert db.get(LogEvent, old_id) is None


def test_help_pages_are_served(client, ceo):
    pages = client.get("/api/help", headers=ceo).json()
    slugs = {p["slug"] for p in pages}
    assert "operations/tenant-onboarding" in slugs and "connectors/aws-cloudwatch" in slugs
    assert "developers/adding-a-whitehat" in slugs
    page = client.get("/api/help/operations/tenant-onboarding", headers=ceo)
    assert page.json()["markdown"].startswith("# ")
    assert client.get("/api/help/..%2F..%2F..%2Fetc%2Fpasswd", headers=ceo).status_code == 404  # traversal refused


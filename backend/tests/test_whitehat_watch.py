"""The WhiteHats summary and the posture blind-spot count come from one function.

A collector or connector is not linked to a WhiteHat row. whitehat_watch attributes a
heartbeat or a sync by data source. A network collector feeds network, so only Network
watches. A process monitor feeds processes, so only Endpoint watches. CloudWatch feeds
logs and cloud_audit. A cloud collector does the same when its host is the connector key,
so Azure Monitor feeds logs and leaves Cloud Posture waiting. A sample signal, a live
signal and last_signal_at do not count. Waiting counts as a blind spot and adds no penalty.
"""

from datetime import timedelta

from conftest import PASSWORD, add_person, login
from sqlalchemy import delete, select

from app.db import SessionLocal, utcnow
from app.models import Agent, AuditLog, Collector, Connector, Organization, PostureSnapshot, Signal, User
from app.security import hash_password
from app.whitehats import BUILTIN_KEYS, enable

WAITING = {
    "identity": "waiting",
    "endpoint": "waiting",
    "network": "waiting",
    "cloud": "waiting",
    "insider": "waiting",
}


def _wipe(slug: str) -> None:
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == slug))
        if org is None:
            return
        oid = org.id
        for model in (Signal, PostureSnapshot, Collector, Connector, AuditLog, Agent, User):
            db.execute(delete(model).where(model.org_id == oid))
        db.delete(org)
        db.commit()


def _company(slug: str) -> int:
    _wipe(slug)
    with SessionLocal() as db:
        org = Organization(name=slug, slug=slug, mfa_required=False)
        db.add(org)
        db.flush()
        now = utcnow()
        add_person(db, User(org_id=org.id, email=f"ceo@{slug}.test", name="Watch CEO", role="ceo",
                    password_hash=hash_password(PASSWORD), terms_accepted_at=now))
        for key in (*BUILTIN_KEYS, "ot"):
            enable(db, org.id, key)
        agents = {a.key: a for a in db.scalars(select(Agent).where(Agent.org_id == org.id))}
        for key, agent in agents.items():
            if key == "ot":
                continue
            agent.last_signal_at = now
            db.add(Signal(org_id=org.id, agent_id=agent.id, severity="medium", title=f"Sample note from {key}",
                          origin="sample", source=f"whitehat:{key}"))
        network = agents["network"]
        network.last_signal_at = now
        db.add(Signal(org_id=org.id, agent_id=network.id, severity="high", title="Live probe against the edge",
                      origin="live", source="whitehat:network"))
        db.commit()
        return org.id


def _agree(client, headers, expected, penalty):
    body = client.get("/api/agents", headers=headers).json()
    posture = client.get("/api/overview", headers=headers).json()["posture"]
    shipped = {a["key"]: a["watch"] for a in body["agents"] if "watch" in a}
    blind = next(part for part in posture["breakdown"] if part["area"] == "Blind spots")
    assert shipped == expected
    assert body["summary"]["blind_spots"] == posture["blind_spots"] == blind["count"]
    assert body["summary"]["watching"] == sum(state == "watching" for state in expected.values())
    assert body["summary"]["blind_spots"] == sum(state != "watching" for state in expected.values())
    assert body["summary"]["roster"] == len(expected)
    assert blind["penalty"] == penalty
    planned = next(a for a in body["agents"] if a["key"] == "ot")
    assert "watch" not in planned and planned["coverage"] is None and planned["info"]["available"] is False
    return body, posture, blind


def test_no_real_delivery_is_waiting_and_the_blind_spot_counts_agree(client):
    slug = "waitco"
    try:
        _company(slug)
        headers = login(client, f"ceo@{slug}.test")
        body, posture, blind = _agree(client, headers, WAITING, 400)
        assert all(a["status"] == "active" and a["last_signal_at"] for a in body["agents"] if a["key"] != "ot")
        signals = client.get("/api/signals", headers=headers).json()
        assert any(s["origin"] == "sample" for s in signals)
        assert any(s["origin"] == "live" and s["severity"] == "high" for s in signals)
        assert posture["grade"] == "Unseen"
        assert "Nothing is reporting yet" in blind["detail"]
        assert body["summary"] == {"watching": 0, "blind_spots": 5, "roster": 5}
    finally:
        _wipe(slug)


def test_a_heartbeat_or_sync_watches_only_the_whitehats_on_that_source(client):
    slug = "feedco"
    try:
        org_id = _company(slug)
        headers = login(client, f"ceo@{slug}.test")
        _agree(client, headers, WAITING, 400)

        with SessionLocal() as db:
            db.add(Collector(org_id=org_id, name="edge", kind="network", mode="local", token_hash="feed-net",
                             status="running", interval_s=15, last_heartbeat=utcnow() - timedelta(days=2)))
            db.commit()
        stale = {**WAITING, "network": "blind"}
        _, posture, blind = _agree(client, headers, stale, 15)
        assert posture["grade"] != "Unseen"
        assert blind["detail"] == "0 dark WhiteHats, 1 silent collectors"

        with SessionLocal() as db:
            row = db.scalar(select(Collector).where(Collector.org_id == org_id, Collector.kind == "network"))
            row.last_heartbeat = utcnow()
            db.commit()
        fresh = {**WAITING, "network": "watching"}
        _agree(client, headers, fresh, 0)

        with SessionLocal() as db:
            db.add(Collector(org_id=org_id, name="procs", kind="process", mode="local", token_hash="feed-proc",
                             status="running", last_heartbeat=utcnow()))
            db.commit()
        _agree(client, headers, {**fresh, "endpoint": "watching"}, 0)
    finally:
        _wipe(slug)

    slug = "syncco"
    try:
        org_id = _company(slug)
        headers = login(client, f"ceo@{slug}.test")
        _agree(client, headers, WAITING, 400)

        with SessionLocal() as db:
            db.add(Connector(org_id=org_id, key="cloudwatch", name="AWS", category="cloud", last_sync_at=utcnow()))
            db.commit()
        cloudwatch = {**WAITING, "identity": "watching", "endpoint": "watching", "insider": "watching",
                      "cloud": "watching"}
        _agree(client, headers, cloudwatch, 0)

        with SessionLocal() as db:
            row = db.scalar(select(Connector).where(Connector.org_id == org_id, Connector.key == "cloudwatch"))
            row.last_sync_at = None
            db.add(Collector(org_id=org_id, name="Azure", kind="log", mode="cloud", host="azure_monitor",
                             token_hash="feed-azure", status="running", last_heartbeat=utcnow()))
            db.commit()
        azure = {**WAITING, "identity": "watching", "endpoint": "watching", "insider": "watching"}
        body, _, _ = _agree(client, headers, azure, 0)
        assert body["agents"]
        assert all(a["watch"] == "waiting" for a in body["agents"] if a["key"] == "network")
    finally:
        _wipe(slug)


def test_the_aws_connection_reading_the_activity_log_makes_cloud_posture_watch(client):
    # The AWS connection reads CloudTrail itself (no CloudWatch connection needed): Cloud Posture is then watching, and
    # Identity, which reads its console sign-ins; not the WhiteHats that read log lines, processes or networks.
    slug = "awsco"
    try:
        org_id = _company(slug)
        headers = login(client, f"ceo@{slug}.test")
        with SessionLocal() as db:
            db.add(Connector(org_id=org_id, key="aws", name="AWS", category="cloud", last_sync_at=utcnow()))
            db.commit()
        _agree(client, headers, {**WAITING, "cloud": "watching", "identity": "watching"}, 0)
    finally:
        _wipe(slug)

"""Coverage is unmeasured until it can be computed, and WhiteHats claim only what they read."""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Agent, Organization
from app.whitehats import CATALOG, enable
from app.whitehats.base import Context
from app.whitehats.builtin import BUILTIN, PLANNED, Insider


class _Clock(datetime):
    """UTC stand-in. Calling now() with no zone is the server local clock, which Insider must not use."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            raise AssertionError("Insider used the server local clock")
        return datetime(2026, 6, 15, 3, 0, tzinfo=UTC)


def _insider(lines, host="host", contract=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr("app.whitehats.builtin.datetime", _Clock)
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        if contract is not None:
            org.contract = {**(org.contract or {}), **contract}
        agent = db.scalar(select(Agent).where(Agent.org_id == org.id, Agent.key == "insider"))
        ctx = Context(db=db, org_id=org.id, agent=agent)
        Insider().on_log_batch(ctx, host, lines)
        raised = [(s.title, s.detail) for s in ctx.raised]
        db.rollback()
        return raised


def test_coverage_is_not_a_constant(client, ceo):
    available = [w for w in CATALOG.values() if w.available]
    coverages = [w.coverage for w in available]
    assert coverages
    numeric = [c for c in coverages if isinstance(c, (int, float))]
    # Every WhiteHat at one shared percentage is a stamp, not a measurement.
    if len(numeric) == len(coverages):
        assert len(set(numeric)) > 1
    else:
        assert numeric == []
    assert all(w.info()["coverage"] is None for w in CATALOG.values())
    data = client.get("/api/agents", headers=ceo).json()
    assert data["agents"]
    # Measured from the map on every read (COV.1): a share of what is there, or None when nothing is there to watch.
    for agent in data["agents"]:
        covers = agent["covers"]
        assert agent["coverage"] == covers["percent"]
        assert covers["percent"] is None if covers["total"] == 0 else covers["percent"] == round(
            100 * covers["seen"] / covers["total"])
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        stored = db.scalars(select(Agent.coverage).where(Agent.org_id == org.id)).all()
    assert stored and all(value is None for value in stored)


def test_planned_whitehats_are_unavailable_and_have_no_coverage(client, ceo):
    assert len(PLANNED) == 8
    for w in PLANNED:
        assert w.available is False
        assert w.coverage is None
        info = w.info()
        assert info["available"] is False
        assert info["coverage"] is None
        assert info["unavailable_reason"]
    data = client.get("/api/agents", headers=ceo).json()
    planned = {w.key for w in PLANNED}
    catalog = {w["key"]: w for w in data["catalog"]}
    assert planned <= set(catalog)
    for key in planned:
        assert catalog[key]["available"] is False
        assert catalog[key]["coverage"] is None
    for agent in data["agents"]:
        assert agent["key"] not in planned
        assert agent["info"]["available"] is True
        assert agent["info"]["coverage"] is None
        assert agent["coverage"] == agent["covers"]["percent"]  # measured, never the catalogue's stamp


def test_enable_does_not_stamp_a_fixed_coverage(client):
    assert CATALOG["supply"].coverage is None
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        agent = enable(db, org.id, "supply")
        assert agent.coverage is None
        db.rollback()
    assert client.get("/api/agents").status_code == 401


# What each shipped WhiteHat may say. A new WhiteHat has to be added here.
CLAIMS = {
    "identity": {"must": ["failed sign-in", "log", "multi-factor", "sign-in provider"],
                 "must_not": ["privilege", "credential", "okta", "entra"], "sources": ["logs", "cloud_audit", "identity"]},
    "endpoint": {"must": ["log shape", "process"], "must_not": ["tamper"], "sources": ["logs", "processes"]},
    "network": {"must": ["high", "critical", "traffic log", "outgoing"], "must_not": ["lateral"], "sources": ["network"]},
    "cloud": {"must": ["cloudtrail"], "must_not": ["azure", "google"], "sources": ["cloud_audit"]},
    "insider": {"must": ["working hours"], "must_not": ["server"], "sources": ["logs"]},
}


def test_descriptions_match_what_each_whitehat_reads(client, ceo):
    by_key = {w.key: w for w in BUILTIN}
    assert set(by_key) == set(CLAIMS)
    planned_names = [w.name for w in PLANNED]
    for key, claim in CLAIMS.items():
        info = by_key[key].info()
        blob = f"{info['domain']} {info['description']}".lower()
        assert [d["key"] for d in info["data_sources"]] == claim["sources"]
        for word in claim["must"]:
            assert word in blob, key
        for word in claim["must_not"]:
            assert word not in blob, key
        assert not any(name in blob for name in planned_names)
    data = client.get("/api/agents", headers=ceo).json()
    for agent in data["agents"]:
        claim = CLAIMS[agent["key"]]
        blob = f"{agent['domain']} {agent['info']['description']}".lower()
        for word in claim["must_not"]:
            assert word not in blob, agent["key"]


def test_insider_uses_the_event_time_not_the_server_clock(client, monkeypatch):
    assert client
    night = [{"source": "sshd", "message": "2026-06-15T02:05:00Z Accepted password for ada"}]
    day = [{"source": "sshd", "message": "2026-06-15T12:05:00Z Accepted password for ada"}]
    missing = [{"source": "sshd", "message": "Accepted password for ada from 203.0.113.4"}]
    assert _insider(night, host="night", monkeypatch=monkeypatch)
    assert _insider(day, host="day", monkeypatch=monkeypatch) == []
    assert _insider(missing, host="notime", monkeypatch=monkeypatch) == []
    mixed = _insider(night + day + missing, host="mixed", monkeypatch=monkeypatch)
    assert len(mixed) == 1 and mixed[0][0].startswith("1 sign-in")
    assert "02:05:00" in mixed[0][1]


def test_insider_keeps_the_event_zone_and_company_hours(client, monkeypatch):
    assert client
    # 08:00-05:00 is 13:00 UTC, inside the default 09:00-17:00 UTC window.
    inside = [{"message": "2026-06-15T08:00:00-05:00 Accepted password for ada"}]
    # 22:00-05:00 is 03:00 UTC the next day, outside that window.
    outside = [{"message": "2026-06-15T22:00:00-05:00 Accepted password for ada"}]
    assert _insider(inside, host="offset-in", monkeypatch=monkeypatch) == []
    assert _insider(outside, host="offset-out", monkeypatch=monkeypatch)
    # The same 02:00Z instant is inside a company window of 00:00-06:00 UTC.
    early = [{"message": "2026-06-15T02:00:00Z Accepted password for ada"}]
    assert _insider(early, host="company", contract={"timezone": "UTC", "work_start": "00:00", "work_end": "06:00"},
                    monkeypatch=monkeypatch) == []
    # 04:00Z is 09:30 at +05:30, inside that company's 09:00-17:00 hours.
    morning = [{"message": "2026-06-15T04:00:00Z Accepted password for ada"}]
    assert _insider(morning, host="ist", contract={"timezone": "+05:30", "work_start": "09:00", "work_end": "17:00"},
                    monkeypatch=monkeypatch) == []


def test_insider_reads_cloudtrail_event_time_and_syslog_as_utc(client, monkeypatch):
    assert client
    trail = [{"message": json.dumps({"eventTime": "2026-06-15T02:10:00Z", "eventName": "ConsoleLogin",
                                     "responseElements": {"ConsoleLogin": "Success"}})}]
    assert _insider(trail, host="trail", monkeypatch=monkeypatch)
    daytime = [{"message": json.dumps({"eventTime": "2026-06-15T15:10:00Z", "eventName": "ConsoleLogin"})}]
    assert _insider(daytime, host="trail-day", monkeypatch=monkeypatch) == []
    syslog = [{"message": "Jun 15 02:14:01 srv sshd[10]: Accepted password for ada from 203.0.113.9"}]
    raised = _insider(syslog, host="syslog", monkeypatch=monkeypatch)
    assert raised and "2026-06-15 02:14:01+0000" in raised[0][1]
    noon = [{"message": "Jun 15 15:14:01 srv sshd[10]: Accepted password for ada from 203.0.113.9"}]
    assert _insider(noon, host="syslog-day", monkeypatch=monkeypatch) == []


def test_sign_in_lines_with_no_readable_time_are_counted(client, ceo):
    out = client.post("/api/collectors", headers=ceo,
                      json={"name": "insider unreadable", "kind": "log", "mode": "docker"}).json()
    tok = {"Authorization": f"Bearer {out['token']}"}
    try:
        client.post("/api/ingest/logs", headers=tok, json={"host": "no-stamp", "lines": [
            {"source": "sshd", "message": "Accepted password for ada from 203.0.113.8"},
            {"source": "sshd", "message": "Accepted password for ben from 203.0.113.9"},
            {"source": "sshd", "message": "2026-06-15T12:05:00Z Accepted password for cara"},
        ]})
        insider = next(a for a in client.get("/api/agents", headers=ceo).json()["agents"] if a["key"] == "insider")
        assert insider["health"]["unreadable_times"] == 2
        titles = [s["title"] for s in client.get("/api/signals", headers=ceo).json()]
        assert not any("no-stamp" in t for t in titles)
    finally:
        client.delete(f"/api/collectors/{out['collector']['id']}", headers=ceo)
        with SessionLocal() as db:
            agent = db.scalar(select(Agent).where(Agent.org_id == db.scalar(
                select(Organization.id).where(Organization.slug == "acme")), Agent.key == "insider"))
            agent.health = {}
            db.commit()


def test_ingested_sign_in_is_judged_from_the_event_stamp(client, ceo):
    out = client.post("/api/collectors", headers=ceo,
                      json={"name": "insider honesty", "kind": "log", "mode": "docker"}).json()
    tok = {"Authorization": f"Bearer {out['token']}"}
    client.post("/api/ingest/logs", headers=tok, json={"host": "honesty-night", "lines": [
        {"source": "sshd", "message": "2026-06-15T02:05:00Z Accepted password for ada from 203.0.113.8"}]})
    client.post("/api/ingest/logs", headers=tok, json={"host": "honesty-day", "lines": [
        {"source": "sshd", "message": "2026-06-15T12:05:00Z Accepted password for ada from 203.0.113.8"}]})
    titles = [s["title"] for s in client.get("/api/signals", headers=ceo).json()]
    assert any("honesty-night" in t and "outside working hours" in t for t in titles)
    assert not any("honesty-day" in t for t in titles)


def test_working_hours_save_on_setup_and_reject_a_bad_window(client, ceo, engineer):
    """Working hours are the account's: an Account owner sets them; a Security lead or an Engineer may not (R-041)."""
    from conftest import login

    security_lead = login(client, "ciso@acme.example")
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        before = dict(org.contract or {})
        origin = org.contract_origin
    try:
        bad_zone = client.put("/api/setup/hours", headers=ceo, json={
            "timezone": "Not/AZone", "work_start": "09:00", "work_end": "17:00"})
        assert bad_zone.status_code == 400
        same = client.put("/api/setup/hours", headers=ceo, json={
            "timezone": "UTC", "work_start": "09:00", "work_end": "09:00"})
        assert same.status_code == 400
        for refused_role in (security_lead, engineer):
            refused = client.put("/api/setup/hours", headers=refused_role, json={
                "timezone": "UTC", "work_start": "09:00", "work_end": "17:00"})
            assert refused.status_code == 403
        saved = client.put("/api/setup/hours", headers=ceo, json={
            "timezone": "Asia/Kolkata", "work_start": "10:00", "work_end": "18:00"})
        assert saved.status_code == 200
        contract = saved.json()["settings"]["contract"]
        assert contract["timezone"] == "Asia/Kolkata"
        assert contract["work_start"] == "10:00" and contract["work_end"] == "18:00"
        with SessionLocal() as db:
            org = db.scalar(select(Organization).where(Organization.slug == "acme"))
            assert org.contract_origin == origin
            assert org.contract["order_ref"] == before.get("order_ref")
    finally:
        with SessionLocal() as db:
            org = db.scalar(select(Organization).where(Organization.slug == "acme"))
            org.contract = before
            db.commit()


def test_an_impossible_offset_is_refused_and_never_stops_log_intake(client, ceo, operator):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        tid, before = org.id, dict(org.contract or {})
    refused = client.put(f"/api/operator/tenants/{tid}", headers=operator, json={"contract": {"timezone": "+24:00"}})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Unknown time zone."
    assert client.put("/api/setup/hours", headers=ceo, json={
        "timezone": "+15:00", "work_start": "09:00", "work_end": "17:00"}).status_code == 400

    # A value stored before this check existed must not break intake: the WhiteHat reads it as UTC.
    with SessionLocal() as db:
        db.get(Organization, tid).contract = {**before, "timezone": "+24:00"}
        db.commit()
    out = client.post("/api/collectors", headers=ceo,
                      json={"name": "insider bad zone", "kind": "log", "mode": "docker"}).json()
    tok = {"Authorization": f"Bearer {out['token']}"}
    try:
        res = client.post("/api/ingest/logs", headers=tok, json={"host": "bad-zone-night", "lines": [
            {"source": "sshd", "message": "2026-06-15T02:05:00Z Accepted password for ada from 203.0.113.8"}]})
        assert res.status_code == 200
        titles = [s["title"] for s in client.get("/api/signals", headers=ceo).json()]
        assert any("bad-zone-night" in t and "outside working hours" in t for t in titles)
    finally:
        client.delete(f"/api/collectors/{out['collector']['id']}", headers=ceo)
        with SessionLocal() as db:
            db.get(Organization, tid).contract = before
            db.commit()

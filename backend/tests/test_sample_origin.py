"""Sample rows belong to the demo company. A company created without the seed has none."""

import re

from conftest import finish
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Incident, Organization, Remediation, Signal, TenantCheck
from app.whitehats import CATALOG
from app.whitehats.signals import PLANNED, SHIPPED, signal_whitehat

SEEDED = {
    "SSH on port 22 accepts connections from any network": ("network", "high"),
    "Remote Desktop on port 3389 accepts connections from any network": ("network", "high"),
    "Log shape needs review: error SELECT * FROM users for lookup": ("endpoint", "high"),
    "Root account used for AttachUserPolicy": ("cloud", "high"),
    "A new access key was created": ("cloud", "medium"),
    "12 failed sign-ins in one batch on finance-mail": ("identity", "medium"),
}
FORBIDDEN = (
    "attack tooling", "Probe timing", "Systematic probing", "Full-table", "Look-alike invoice",
    "change control", "live secret", "Live payments secret",
)


def _sample_count(db, model, org_id: int) -> int:
    return db.scalar(select(func.count()).select_from(model).where(
        model.org_id == org_id, model.origin == "sample")) or 0


def test_demo_rows_are_sample_and_signals_match_shipped_detectors(client, ceo):
    incidents = client.get("/api/incidents", headers=ceo).json()
    assert incidents and all(row["origin"] == "sample" for row in incidents)
    signals = client.get("/api/signals?days=30", headers=ceo).json()
    by_title = {row["title"]: row for row in signals}
    for title, (key, severity) in SEEDED.items():
        row = by_title[title]
        hat = CATALOG[key]
        assert hat.available
        assert row["origin"] == "sample" and row["agent"] == hat.name and row["severity"] == severity
        if key == "network":
            assert severity in ("high", "critical")
    for row in signals:
        blob = f"{row['title']} {row.get('detail', '')}"
        assert not any(phrase in blob for phrase in FORBIDDEN)
    fixes = client.get("/api/remediations", headers=ceo).json()
    seeded_fixes = [row for row in fixes if row.get("target") != "unit-test"]
    assert seeded_fixes and all(row["origin"] == "sample" for row in seeded_fixes)
    overview = client.get("/api/overview", headers=ceo).json()
    assert overview["incidents"] and all(row["origin"] == "sample" for row in overview["incidents"])
    assert any(row["origin"] == "sample" for row in overview["signals"])
    for row in overview["signals"]:
        if row["title"] in SEEDED:
            assert row["origin"] == "sample"
    assert any(row["kind"] == "fix" and row["origin"] == "sample" for row in overview["decisions"])
    checks = {row["key"]: row for row in client.get("/api/setup", headers=ceo).json()["readiness"]["checks"]}
    recorded = [row for row in checks.values() if row["detail"] == "Recorded during the demo setup."]
    assert len(recorded) >= 4 and all(row["origin"] == "sample" for row in recorded)
    assert checks["domain"]["origin"] == "sample"
    assert checks["region"]["origin"] == "live"
    me = client.get("/api/auth/me", headers=ceo).json()
    assert me["org"]["slug"] == "acme" and me["org"]["sample"] is True
    setup = client.get("/api/setup", headers=ceo).json()
    assert setup["settings"]["contract_origin"] == "sample"
    assert setup["first_value"] and all(item["origin"] == "sample" for item in setup["first_value"])

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        for model in (Incident, Signal, Remediation, TenantCheck):
            assert _sample_count(db, model, org.id) > 0


def test_banner_follows_sample_rows_not_the_company_name(client, ceo):
    models = (Incident, Signal, Remediation, TenantCheck)
    changed = []
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        for model in models:
            for row in db.scalars(select(model).where(model.org_id == org.id, model.origin == "sample")):
                changed.append((model, row.id))
                row.origin = "live"
        db.commit()
    try:
        me = client.get("/api/auth/me", headers=ceo).json()
        assert me["org"]["slug"] == "acme" and me["org"]["sample"] is False
    finally:
        with SessionLocal() as db:
            for model, rid in changed:
                row = db.get(model, rid)
                if row is not None:
                    row.origin = "sample"
            db.commit()


def test_real_company_has_zero_sample_rows(client, operator):
    created = client.post("/api/operator/tenants", headers=operator, json={
        "name": "Plain Co", "slug": "plainco", "plan": "starter", "region": settings.region,
        "domain": "plainco.test", "mfa_required": False,
        "ai_policy": {"providers": ["rules"], "zero_retention": True, "agreed": True},
        "contract": {"owner_name": "Pat Lee", "owner_email": "pat@plainco.test",
                     "security_contact": "Pat Lee +91 90000 22222"},
        "contracted_agents": ["identity", "network"],
    })
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    invited = client.post(f"/api/operator/tenants/{tid}/invites", headers=operator,
                          json={"email": "pat@plainco.test", "name": "Pat Lee", "role": "ceo"})
    assert invited.status_code == 200, invited.text
    token = invited.json()["link"].rsplit("/", 1)[1]
    accepted = client.post(f"/api/invites/{token}/accept",
                           json={"name": "Pat Lee", "password": "Plainco-Test-2026"})
    assert accepted.status_code == 200, accepted.text
    headers = {"Authorization": f"Bearer {finish(client, accepted.json())['token']}"}
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["org"]["slug"] == "plainco" and me["org"]["sample"] is False
    assert client.get("/api/incidents", headers=headers).json() == []
    assert client.get("/api/signals", headers=headers).json() == []
    assert client.get("/api/remediations", headers=headers).json() == []
    checks = client.get("/api/setup", headers=headers).json()["readiness"]["checks"]
    assert checks and all(row["origin"] == "live" for row in checks)

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "plainco"))
        assert org is not None
        for model in (Incident, Signal, Remediation, TenantCheck):
            assert _sample_count(db, model, org.id) == 0
        assert db.scalar(select(func.count()).select_from(TenantCheck).where(TenantCheck.org_id == org.id)) > 0


def test_catalogue_names_match_the_whitehat_registry():
    available = {hat.name for hat in CATALOG.values() if hat.available}
    planned = {hat.name for hat in CATALOG.values() if not hat.available}
    assert available == set(SHIPPED)
    assert planned == set(PLANNED)


# Template words that show up on many findings and do not name the subject.
_STOP = {
    "the", "and", "for", "from", "with", "that", "this", "was", "were", "are", "any",
    "not", "but", "its", "one", "new", "has", "had", "have", "been", "than", "then",
    "port", "accept", "accepts", "connection", "connections", "network", "open",
}


def _subject_words(text: str) -> set[str]:
    words = set()
    for raw in re.findall(r"[a-z0-9]+", text.lower()):
        if raw in _STOP or len(raw) < 3:
            continue
        word = raw[:-1] if len(raw) > 4 and raw.endswith("s") and not raw.endswith("ss") else raw
        if word in _STOP or len(word) < 3:
            continue
        words.add(word)
    return words


def test_sample_signals_share_their_incident_subject(client, ceo):
    incidents = client.get("/api/incidents", headers=ceo).json()
    by_ref = {row["ref"]: row for row in incidents}
    assert set(by_ref) == {"INC-2208", "INC-2211", "INC-2214", "INC-2190"}
    assert by_ref["INC-2208"]["severity"] == "critical"
    assert by_ref["INC-2211"]["severity"] == "high"
    assert by_ref["INC-2214"]["severity"] == "high"
    assert by_ref["INC-2190"]["severity"] == "medium"
    fixes = client.get("/api/remediations", headers=ceo).json()
    signals = client.get("/api/signals?days=30", headers=ceo).json()
    linked = [row for row in signals if row["origin"] == "sample" and row["incident_id"]]
    assert linked
    hats = {hat.name: hat for hat in CATALOG.values()}
    for inc in incidents:
        if inc["origin"] != "sample":
            continue
        title_words = _subject_words(inc["title"])
        assert title_words & _subject_words(inc["summary"]), inc["ref"]
        assert title_words & _subject_words(inc["impact"]), inc["ref"]
        own_signals = [row for row in linked if row["incident_id"] == inc["id"]]
        assert own_signals, inc["ref"]
        for fix in fixes:
            if fix["origin"] == "sample" and fix["incident_id"] == inc["id"]:
                assert title_words & _subject_words(fix["title"]), fix["title"]
        for signal in own_signals:
            hat = signal_whitehat(signal["title"])
            assert hat and signal["agent"] == hat and hats[hat].available, signal["title"]
            shared = title_words & _subject_words(signal["title"])
            assert shared, (
                f"{signal['title']!r} does not share a subject word with {inc['ref']} {inc['title']!r}"
            )
    decisions = client.get("/api/overview", headers=ceo).json()["decisions"]
    assert decisions[0]["title"] == "Fix SQL injection in user lookup"

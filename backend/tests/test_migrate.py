"""Upgrading a database made by an older version must leave every old company usable.

Every upgrade runs twice: on SQLite, which development uses, and on Postgres, which production uses.
"""

import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.migrate import migrate
from app.schema_sync import table_drift


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, tmp_path):
    if request.param == "postgresql":
        return request.getfixturevalue("postgres_engine")
    return create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}", connect_args={"check_same_thread": False})


def _old_table(conn, ddl: str) -> None:
    """Old-version DDL written once for both databases."""
    if conn.dialect.name == "postgresql":
        ddl = ddl.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY").replace("DATETIME", "TIMESTAMP")
    conn.execute(text(ddl))


def _advance_ids(engine) -> None:
    """Old rows were written with fixed ids. Postgres counters must move past them before the app inserts."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        insp = inspect(conn)
        for table in insp.get_table_names():
            if "id" not in {col["name"] for col in insp.get_columns(table)}:
                continue
            sequence = conn.execute(text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}).scalar()
            if sequence:
                conn.execute(text(f'SELECT setval(:s, COALESCE((SELECT MAX(id) FROM "{table}"), 0) + 1, false)'),
                             {"s": sequence})


def _company(engine, org_id: int = 1) -> None:
    from app.models import Organization

    with Session(engine) as db:
        db.add(Organization(id=org_id, name=f"Company {org_id}"))
        db.commit()
    _advance_ids(engine)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _assert_matches_models(engine) -> None:
    """An upgraded database has the same uniqueness rules, required columns and indexes as a fresh one."""
    with engine.connect() as conn:
        insp = inspect(conn)
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            drift = table_drift(conn, table)
            assert not drift.needs_change, drift
            have = {index["name"] for index in insp.get_indexes(table.name)}
            assert {index.name for index in table.indexes} <= have, table.name


def test_upgrade_from_1_0_fills_new_columns_and_is_repeatable(engine):
    with engine.begin() as conn:
        _old_table(conn, "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR(120), "
                         "plan VARCHAR(20), created_at DATETIME)")
        conn.execute(text("INSERT INTO organizations VALUES (1, 'Old Co', 'growth', '2026-09-01 10:00:00')"))
        _old_table(conn, "CREATE TABLE employees (id INTEGER PRIMARY KEY)")
    _advance_ids(engine)
    Base.metadata.create_all(engine)
    done = migrate(engine)
    assert "dropped retired table employees" in done
    with engine.connect() as conn:
        row = conn.execute(text("SELECT slug, status, live_at, contracted_agents, contract, is_platform "
                                "FROM organizations")).one()
    assert row.slug == "org-1" and row.status == "live" and str(row.live_at).startswith("2026-09-01")
    assert _json(row.contracted_agents) == [] and _json(row.contract) == {}
    assert not row.is_platform
    _assert_matches_models(engine)
    assert migrate(engine) == []  # nothing left to do the second time


def test_upgrade_removes_invented_posture_history(engine):
    from app.models import Organization
    from app.projects import ensure_main_project

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="History Co")
        db.add(org)
        db.flush()
        org_id, project_id = org.id, ensure_main_project(db, org).id
        db.commit()
    with engine.begin() as conn:
        for day, score, breakdown in (("2026-10-01", 800, "{}"), ("2026-10-02", 810, '{"score": 810}')):
            conn.execute(text("INSERT INTO posture_snapshots (org_id, project_id, day, score, breakdown) "
                              "VALUES (:org, :project, :day, :score, :breakdown)"),
                         {"org": org_id, "project": project_id, "day": day, "score": score, "breakdown": breakdown})
    done = migrate(engine)
    assert any(line.startswith("removed ") and "invented posture" in line for line in done)
    with engine.connect() as conn:
        days = [str(row.day) for row in conn.execute(text("SELECT day FROM posture_snapshots ORDER BY day"))]
    assert days == ["2026-10-02"]
    assert migrate(engine) == []


def test_upgrade_marks_seeded_models_and_leaves_a_persons_model(engine):
    from app.models import ModelDeployment

    Base.metadata.create_all(engine)
    _company(engine)
    with Session(engine) as db:
        db.add_all([
            ModelDeployment(org_id=1, name="whitehats · gpt-6-luna", provider="azure_openai",
                            deployment="gpt-6-luna", priority=1, seed_slot=""),
            ModelDeployment(org_id=1, name="whitehats · gpt-5.6-terra", provider="azure_openai",
                            deployment="gpt-5.6-terra", priority=2, seed_slot=""),
            ModelDeployment(org_id=1, name="whitehats · mine", provider="azure_openai",
                            deployment="gpt-6-sol", priority=3, seed_slot=""),
        ])
        db.commit()
    done = migrate(engine)
    assert any("seeded primary" in line for line in done)
    assert any("seeded optional" in line for line in done)
    with engine.connect() as conn:
        slots = dict(conn.execute(text("SELECT name, seed_slot FROM model_deployments")).all())
    assert slots["whitehats · gpt-6-luna"] == "primary"
    assert slots["whitehats · gpt-5.6-terra"] == "optional"
    assert slots["whitehats · mine"] == ""
    assert migrate(engine) == []


def test_upgrade_keeps_existing_signals_when_clone_columns_are_added(engine):
    with engine.begin() as conn:
        _old_table(conn, "CREATE TABLE signals (id INTEGER PRIMARY KEY, org_id INTEGER, severity VARCHAR(10), "
                         "title VARCHAR(240))")
        conn.execute(text("INSERT INTO signals VALUES (7, 1, 'high', 'old finding')"))
    _advance_ids(engine)
    Base.metadata.create_all(engine)
    done = migrate(engine)
    assert "added signals.clone_id" in done
    with engine.connect() as conn:
        row = conn.execute(text("SELECT title, clone_id FROM signals WHERE id = 7")).one()
    assert row.title == "old finding" and row.clone_id is None
    assert migrate(engine) == []


def _load_stamped_agents(engine) -> None:
    from sqlalchemy import event

    from app.models import Signal

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    with engine.begin() as conn:
        _old_table(conn, (
            "CREATE TABLE agents ("
            "id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, key VARCHAR(40) NOT NULL, "
            "name VARCHAR(80) NOT NULL, domain VARCHAR(200) NOT NULL, "
            "status VARCHAR(20) NOT NULL DEFAULT 'active', "
            "coverage INTEGER NOT NULL DEFAULT 80, last_signal_at DATETIME, "
            "UNIQUE (org_id, key))"
        ))
        conn.execute(text(
            "INSERT INTO agents (org_id, key, name, domain, coverage) VALUES "
            "(1, 'identity', 'Identity', 'old', 90), "
            "(1, 'network', 'Network', 'old', 40), "
            "(1, 'endpoint', 'Endpoint', 'old', 80), "
            "(1, 'cloud', 'Cloud', 'old', 0)"
        ))
    Base.metadata.create_all(engine)
    _company(engine)
    with Session(engine) as db:
        db.add(Signal(org_id=1, agent_id=1, severity="high", title="kept finding"))
        db.commit()


def test_upgrade_clears_stamped_coverage_and_keeps_a_measurement(engine):
    _load_stamped_agents(engine)
    done = migrate(engine)
    assert "cleared stamped coverage on 3 WhiteHats" in done
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT key, coverage FROM agents")).all())
        coverage = next(col for col in inspect(conn).get_columns("agents") if col["name"] == "coverage")
        linked = conn.execute(text("SELECT title FROM signals WHERE agent_id = 1")).scalar()
        if engine.dialect.name == "sqlite":
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
    assert rows == {"identity": None, "endpoint": None, "cloud": None, "network": 40}
    assert coverage["nullable"] and coverage["default"] is None
    assert linked == "kept finding"
    _assert_matches_models(engine)
    again = migrate(engine)
    assert not any("coverage" in line for line in again)


def test_a_coverage_measured_after_the_upgrade_is_never_cleared(engine):
    _load_stamped_agents(engine)
    migrate(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE agents SET coverage = 80 WHERE key = 'identity'"))
    assert migrate(engine) == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT coverage FROM agents WHERE key = 'identity'")).scalar() == 80


def test_upgraded_whitehats_are_unique_per_project_not_per_account(engine):
    from app.models import Agent, Organization, Project

    _load_stamped_agents(engine)
    migrate(engine)
    with Session(engine) as db:
        org = db.get(Organization, 1)
        second = Project(org_id=org.id, name="Second", slug="second")
        db.add(second)
        db.flush()
        db.add(Agent(org_id=org.id, project_id=second.id, key="identity", name="Identity", domain="people"))
        db.commit()
        keys = db.scalars(Agent.__table__.select().with_only_columns(Agent.project_id)
                          .where(Agent.key == "identity")).all()
    assert len(set(keys)) == 2


def test_upgrade_marks_connectors_that_are_not_really_reading(engine):
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.models import Connector

    Base.metadata.create_all(engine)
    _company(engine)
    with Session(engine) as db:
        db.add_all([
            Connector(org_id=1, key="jira", name="Jira", category="Ticketing", status="configured",
                      config={"site": "https://acme.atlassian.net"}, secret_enc="sealed-secret",
                      last_check="Saved.", connected_at=datetime(2026, 1, 1)),
            Connector(org_id=1, key="github", name="GitHub", category="Code", status="verified",
                      secret_enc="keep-me", last_check="Signed in."),
            Connector(org_id=1, key="cloudwatch", name="AWS CloudWatch Logs", category="Cloud logs",
                      status="verified", secret_enc="keep-me", last_sync_at=datetime(2026, 10, 1)),
            Connector(org_id=1, key="azure_monitor", name="Azure Monitor", category="Cloud logs",
                      status="verified", secret_enc="keep-me"),
        ])
        db.commit()
    done = migrate(engine)
    assert any("unbuilt" in line for line in done)
    assert any("not reading" in line for line in done)
    with engine.connect() as conn:
        rows = {row.key: row for row in conn.execute(text(
            "SELECT key, status, secret_enc, last_sync_at FROM connectors")).all()}
    assert rows["jira"].status == "unbuilt" and rows["jira"].secret_enc == ""
    assert rows["github"].status == "checked" and rows["github"].secret_enc == "keep-me"
    assert rows["cloudwatch"].status == "reading" and rows["cloudwatch"].last_sync_at is not None
    assert rows["azure_monitor"].status == "checked" and rows["azure_monitor"].secret_enc == "keep-me"
    again = migrate(engine)
    assert not any("connector" in line or "reading" in line for line in again)


def test_upgrade_aligns_the_sample_story(engine):
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.models import Incident, IncidentEvent, Organization, Remediation, Signal
    from app.seed import SAMPLE_INCIDENTS, SAMPLE_REMEDIATIONS

    Base.metadata.create_all(engine)
    old_summary = (
        "SSH on 10.2.7.44 accepts connections from any network, and Remote Desktop on port 3389 does too. "
        "An analyst confirmed the user lookup route builds SQL from raw input."
    )
    with Session(engine) as db:
        acme = Organization(name="Acme Fintech (demo)", slug="acme", status="live",
                            live_at=datetime(2026, 9, 1), created_at=datetime(2026, 8, 1))
        kept = Organization(name="Kept Co", slug="kept", status="live",
                            live_at=datetime(2026, 9, 1), created_at=datetime(2026, 8, 1))
        oldco = Organization(name="Old Co", slug="oldco", status="live",
                             live_at=datetime(2026, 9, 1), created_at=datetime(2026, 8, 1))
        db.add_all([acme, kept, oldco])
        db.flush()

        def add_inc(org, ref, title, sev, status, summary, impact, origin="sample"):
            row = Incident(org_id=org.id, ref=ref, title=title, severity=sev, status=status,
                           summary=summary, business_impact=impact, origin=origin)
            db.add(row)
            db.flush()
            return row

        recon = add_inc(acme, "INC-2208", "Automated reconnaissance against the customer API",
                        "critical", "investigating", old_summary,
                        "Customer records could be read in bulk if the weak route is exploited. About 82,000 customers.")
        admin = add_inc(acme, "INC-2211", "Admin role granted outside change control", "high", "open",
                        "The deployer service account was given organisation admin at 01:57 with no change ticket.",
                        "Anyone holding this service account's key could change any system.")
        payments = add_inc(acme, "INC-2214", "Live payments key found in source code", "high", "contained",
                           "A live payments provider key was committed to the payments service repository.",
                           "The key could be used to take payments as the company until it is rotated.")
        phish = add_inc(acme, "INC-2190", "Phishing campaign against the finance team", "medium", "resolved",
                        "Twelve finance staff received invoice lures from a look-alike domain.",
                        "None after containment.")
        for incident, events in (
            (recon, (
                "Network WhiteHat flagged SSH on port 22 open to any network",
                "Endpoint WhiteHat flagged a log shape: error SELECT * FROM users",
                "Confirmed the user lookup route builds SQL from raw input. Sent to Fix Factory.",
                "Edge rule rate-limits 10.2.7.44. Proposed network isolation.",
            )),
            (admin, ("Cloud Posture WhiteHat: root account used for AttachUserPolicy",)),
            (payments, (
                "Cloud Posture WhiteHat: a new access key was created",
                "Key rotated with the payments provider. Code fix sent to Fix Factory.",
            )),
            (phish, (
                "Identity WhiteHat: 12 failed sign-ins in one batch on finance-mail",
                "Domain blocked, messages pulled from inboxes",
            )),
        ):
            for event_text in events:
                db.add(IncidentEvent(incident_id=incident.id, kind="signal", text=event_text, actor="WhiteHats"))
        db.add_all([
            Signal(org_id=acme.id, incident_id=recon.id, severity="high", origin="sample",
                   title="Log shape needs review: error SELECT * FROM users", entity="db-prod-users"),
            Signal(org_id=acme.id, incident_id=recon.id, severity="high", origin="sample",
                   title="SSH on port 22 accepts connections from any network", entity="10.2.7.44"),
            Signal(org_id=acme.id, incident_id=recon.id, severity="high", origin="sample",
                   title="Remote Desktop on port 3389 accepts connections from any network", entity="10.2.7.44"),
            Signal(org_id=acme.id, incident_id=admin.id, severity="high", origin="sample",
                   title="Root account used for AttachUserPolicy", entity="ap-south-1"),
            Signal(org_id=acme.id, incident_id=payments.id, severity="medium", origin="sample",
                   title="A new access key was created", entity="ap-south-1"),
            Signal(org_id=acme.id, incident_id=phish.id, severity="medium", origin="sample",
                   title="12 failed sign-ins in one batch on finance-mail", entity="finance-mail"),
        ])
        db.add_all([
            Remediation(org_id=acme.id, incident_id=recon.id, origin="sample", status="pending",
                        title="Fix SQL injection in user lookup", target="customer-api",
                        action="Replace string-built SQL with a parameterised query.",
                        why="This is the weakness the probing found. Closing it ends the data risk.",
                        destination="Fix Factory", created_at=datetime(2026, 10, 23, 12, 0, 0)),
            Remediation(org_id=acme.id, incident_id=recon.id, origin="sample", status="pending",
                        title="Isolate the probing host", target="10.2.7.44",
                        action="Network isolation of one host for 4 hours.",
                        why="Stops further probing while the code fix is proven.",
                        destination="Firewall runbook", created_at=datetime(2026, 10, 23, 11, 0, 0)),
            Remediation(org_id=acme.id, incident_id=admin.id, origin="sample", status="pending",
                        title="Revoke the out-of-process admin grant", target="svc_deployer",
                        action="Remove org admin from svc_deployer.",
                        why="Nobody approved this grant and it gives full control.",
                        destination="Okta", created_at=datetime(2026, 10, 23, 11, 30, 0)),
        ])
        kept_inc = add_inc(kept, "INC-7001", "Automated reconnaissance against the customer API",
                           "critical", "investigating", old_summary, "Leave this live row alone.", origin="live")
        db.add(Signal(org_id=kept.id, incident_id=kept_inc.id, severity="high", origin="live",
                      title="Log shape needs review: error SELECT * FROM users", entity="db-prod-users"))
        db.add(Remediation(org_id=kept.id, incident_id=kept_inc.id, origin="live", status="pending",
                           title="Isolate the probing host", target="10.2.7.44", action="Leave it.",
                           why="A person wrote this.", destination="Slack"))
        add_inc(oldco, "INC-2208", "Automated reconnaissance against the customer API",
                "critical", "investigating", "Notes mention AI-driven attack tooling in the old demo.",
                "Old impact.", origin="sample")
        db.commit()
        acme_id, kept_id, oldco_id = acme.id, kept.id, oldco.id

    done = migrate(engine)
    assert any("aligned" in line for line in done)
    assert any("SQL fix first" in line for line in done)
    story = {item["ref"]: item for item in SAMPLE_INCIDENTS}
    fixes = {item["title"]: item for item in SAMPLE_REMEDIATIONS}
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT ref, title, summary, business_impact, severity, status, origin FROM incidents "
            "WHERE org_id = :org ORDER BY ref"
        ), {"org": acme_id}).all()
        for row in rows:
            want = story[row.ref]
            assert row.title == want["title"]
            assert row.summary == want["summary"]
            assert row.business_impact == want["impact"]
            assert row.severity == want["severity"] and row.status == want["status"]
            assert row.origin == "sample"
        placed = dict(conn.execute(text(
            "SELECT signals.title, incidents.ref FROM signals "
            "JOIN incidents ON incidents.id = signals.incident_id WHERE signals.org_id = :org"
        ), {"org": acme_id}).all())
        for item in SAMPLE_INCIDENTS:
            for signal in item["signals"]:
                assert placed[signal[3]] == item["ref"]
        log = conn.execute(text(
            "SELECT entity FROM signals WHERE org_id = :org AND title = :title"
        ), {"org": acme_id, "title": story["INC-2208"]["signals"][0][3]}).scalar()
        assert log == "customer-api"
        for ref, item in story.items():
            texts = set(conn.execute(text(
                "SELECT e.text FROM incident_events e JOIN incidents i ON i.id = e.incident_id "
                "WHERE i.org_id = :org AND i.ref = :ref"
            ), {"org": acme_id, "ref": ref}).scalars().all())
            assert texts == {event[2] for event in item["events"]}, ref
        pending = conn.execute(text(
            "SELECT title, why, target, destination FROM remediations "
            "WHERE org_id = :org AND status = 'pending' ORDER BY created_at, id"
        ), {"org": acme_id}).all()
        assert [row.title for row in pending] == [
            "Fix SQL injection in user lookup",
            "Disable the user lookup route",
            "Remove the policy the root account attached and disable the new access key",
        ]
        assert pending[0].why == fixes["Fix SQL injection in user lookup"]["why"]
        assert pending[1].target == "customer-api" and pending[1].destination == "Slack"
        assert pending[2].target == "ap-south-1"
        sql_at = conn.execute(text(
            "SELECT created_at FROM remediations WHERE org_id = :org AND title = :title"
        ), {"org": acme_id, "title": "Fix SQL injection in user lookup"}).scalar()
        kept_title = conn.execute(text(
            "SELECT title FROM incidents WHERE org_id = :org"
        ), {"org": kept_id}).scalar()
        kept_signal = conn.execute(text(
            "SELECT title FROM signals WHERE org_id = :org"
        ), {"org": kept_id}).scalar()
        kept_fix = conn.execute(text(
            "SELECT title FROM remediations WHERE org_id = :org"
        ), {"org": kept_id}).scalar()
        old_row = conn.execute(text(
            "SELECT title, summary FROM incidents WHERE org_id = :org"
        ), {"org": oldco_id}).one()
    assert kept_title == "Automated reconnaissance against the customer API"
    assert kept_signal == "Log shape needs review: error SELECT * FROM users"
    assert kept_fix == "Isolate the probing host"
    assert old_row.title == story["INC-2208"]["title"]
    assert old_row.summary == story["INC-2208"]["summary"]
    assert "AI-driven attack tooling" not in old_row.summary
    again = migrate(engine)
    assert not any("aligned" in line or "SQL fix" in line for line in again)
    with engine.connect() as conn:
        sql_again = conn.execute(text(
            "SELECT created_at FROM remediations WHERE org_id = :org AND title = :title"
        ), {"org": acme_id, "title": "Fix SQL injection in user lookup"}).scalar()
    assert sql_again == sql_at


def test_upgrade_puts_existing_rows_in_main_and_keeps_sign_in(engine):
    """A database from before projects keeps its rows, moves them into Main, and still signs in."""
    from datetime import timedelta

    from conftest import PASSWORD

    from app.db import utcnow
    from app.projects import PROJECT_TABLES
    from app.security import hash_password

    required = sorted(
        table.name for table in Base.metadata.tables.values()
        if "project_id" in table.c and table.c.project_id.nullable is False and table.name != "project_members"
    )
    assert required == sorted(PROJECT_TABLES)
    stamp = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as conn:
        _load_pre_project_rows(conn, stamp, hash_password(PASSWORD))
    _advance_ids(engine)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        before = _counts(conn)
    assert before == {"incidents": 1, "signals": 2, "users": 5, "remediations": 1}
    done = migrate(engine)
    with engine.connect() as conn:
        assert _counts(conn) == before
        _assert_every_row_has_a_project(conn)
        _assert_legacy_roles(conn)
        _assert_account_tables_have_no_project(engine)
    _assert_matches_models(engine)
    assert "gave 3 accounts a Main project" in done
    assert "mapped 5 existing users onto account and project roles" in done
    assert "placed 18 existing rows into Main" in done
    assert "attached 1 connections to Main" in done  # Okta is used by every project, so it is not attached
    assert migrate(engine) == []
    _assert_old_user_can_still_read(engine)
    with engine.connect() as conn:
        assert _counts(conn) == before


_LEGACY_INCIDENT = "Legacy outage on the payments path"
_LEGACY_SIGNAL = "Legacy signal on the payments host"
_OTHER_SIGNAL = "Other company signal"
_LEGACY_FIX = "Legacy fix for the payments host"
_TRACKED = ("incidents", "signals", "users", "remediations")
_ACCOUNT_TABLES = (
    "organizations", "users", "invites", "connectors", "model_deployments", "collectors",
    "log_templates", "log_events", "log_digests", "template_counts", "network_flows",
    "process_samples", "llm_calls", "tenant_checks",
)


def _counts(conn):
    return {name: conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() for name in _TRACKED}


def _load_pre_project_rows(conn, stamp, password_hash):
    """Tables as they were before project_id and account_role existed."""
    _old_table(conn, (
        "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR(120), slug VARCHAR(60), "
        "plan VARCHAR(20), status VARCHAR(20), mfa_required BOOLEAN, is_platform BOOLEAN, "
        "created_at DATETIME, live_at DATETIME)"
    ))
    for row in (
        {"id": 1, "name": "Legacy Co", "slug": "legacy", "created_at": "2026-09-01 10:00:00"},
        {"id": 2, "name": "Other Co", "slug": "other", "created_at": "2026-09-02 10:00:00"},
        {"id": 3, "name": "Quiet Co", "slug": "quiet", "created_at": "2026-09-03 10:00:00"},
    ):
        conn.execute(text(
            "INSERT INTO organizations (id, name, slug, plan, status, mfa_required, is_platform, created_at, live_at) "
            "VALUES (:id, :name, :slug, 'growth', 'live', FALSE, FALSE, :created_at, :created_at)"
        ), row)
    _old_table(conn, (
        "CREATE TABLE users (id INTEGER PRIMARY KEY, org_id INTEGER, email VARCHAR(200), name VARCHAR(120), "
        "role VARCHAR(20), password_hash VARCHAR(300), active BOOLEAN)"
    ))
    for person in (
        (1, 1, "ceo@legacy.example", "Anita Rao", "ceo"),
        (2, 1, "ciso@legacy.example", "Meera Pillai", "ciso"),
        (3, 1, "analyst@legacy.example", "Priyanka Das", "analyst"),
        (4, 1, "engineer@legacy.example", "Kavya Iyer", "engineer"),
        (5, 2, "ceo@other.example", "Omar Shah", "ceo"),
    ):
        conn.execute(text(
            "INSERT INTO users (id, org_id, email, name, role, password_hash, active) "
            "VALUES (:id, :org_id, :email, :name, :role, :password_hash, TRUE)"
        ), {"id": person[0], "org_id": person[1], "email": person[2], "name": person[3], "role": person[4],
            "password_hash": password_hash})
    _old_table(conn, (
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, org_id INTEGER, actor VARCHAR(120), action VARCHAR(80))"
    ))
    conn.execute(text(
        "INSERT INTO audit_log (id, org_id, actor, action) VALUES (1, 1, 'Anita Rao', 'kept from before')"
    ))
    _old_table(conn, (
        "CREATE TABLE incidents (id INTEGER PRIMARY KEY, org_id INTEGER, ref VARCHAR(20), title VARCHAR(200), "
        "severity VARCHAR(10), status VARCHAR(20), summary TEXT, business_impact TEXT, owner VARCHAR(120), "
        "created_at DATETIME, origin VARCHAR(20))"
    ))
    conn.execute(text(
        "INSERT INTO incidents (id, org_id, ref, title, severity, status, summary, business_impact, owner, "
        "created_at, origin) VALUES (1, 1, 'INC-1001', :title, 'high', 'open', 'Payments paused', "
        "'Customers could not pay', 'Anita Rao', :at, 'live')"
    ), {"title": _LEGACY_INCIDENT, "at": stamp})
    _old_table(conn, (
        "CREATE TABLE incident_events (id INTEGER PRIMARY KEY, incident_id INTEGER, at DATETIME, "
        "kind VARCHAR(20), text TEXT, actor VARCHAR(120))"
    ))
    conn.execute(text(
        "INSERT INTO incident_events (id, incident_id, at, kind, text, actor) "
        "VALUES (1, 1, :at, 'note', 'Opened before projects existed', 'Anita Rao')"
    ), {"at": stamp})
    _old_table(conn, (
        "CREATE TABLE signals (id INTEGER PRIMARY KEY, org_id INTEGER, severity VARCHAR(10), title VARCHAR(240), "
        "detail TEXT, entity VARCHAR(160), created_at DATETIME, origin VARCHAR(20))"
    ))
    for signal in (
        (1, 1, "high", _LEGACY_SIGNAL, "pay-1"),
        (2, 2, "medium", _OTHER_SIGNAL, "other-1"),
    ):
        conn.execute(text(
            "INSERT INTO signals (id, org_id, severity, title, detail, entity, created_at, origin) "
            "VALUES (:id, :org_id, :severity, :title, 'Seen before projects', :entity, :at, 'live')"
        ), {"id": signal[0], "org_id": signal[1], "severity": signal[2], "title": signal[3],
            "entity": signal[4], "at": stamp})
    _old_table(conn, (
        "CREATE TABLE remediations (id INTEGER PRIMARY KEY, org_id INTEGER, title VARCHAR(200), target VARCHAR(160), "
        "action TEXT, destination VARCHAR(80), status VARCHAR(20), created_at DATETIME)"
    ))
    conn.execute(text(
        "INSERT INTO remediations (id, org_id, title, target, action, destination, status, created_at) "
        "VALUES (1, 1, :title, 'pay-1', 'Restart the payments service', 'Slack', 'pending', :at)"
    ), {"title": _LEGACY_FIX, "at": stamp})
    _old_table(conn, (
        "CREATE TABLE posture_snapshots (id INTEGER PRIMARY KEY, org_id INTEGER, day DATE, score INTEGER, "
        "breakdown TEXT)"
    ))
    conn.execute(text(
        "INSERT INTO posture_snapshots (id, org_id, day, score, breakdown) "
        "VALUES (1, 1, '2026-10-22', 700, '{\"score\": 700}')"
    ))
    _old_table(conn, (
        "CREATE TABLE agents (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, key VARCHAR(40) NOT NULL, "
        "name VARCHAR(80) NOT NULL, domain VARCHAR(200) NOT NULL, status VARCHAR(20) NOT NULL)"
    ))
    conn.execute(text(
        "INSERT INTO agents (id, org_id, key, name, domain, status) "
        "VALUES (1, 1, 'identity', 'Identity', 'people and keys', 'active')"
    ))
    _old_table(conn, (
        "CREATE TABLE network_findings (id INTEGER PRIMARY KEY, org_id INTEGER, key VARCHAR(200), kind VARCHAR(40), "
        "severity VARCHAR(10), title VARCHAR(240), detail TEXT)"
    ))
    conn.execute(text(
        "INSERT INTO network_findings (id, org_id, key, kind, severity, title, detail) "
        "VALUES (1, 1, 'edge-22', 'exposure', 'high', 'Old network finding', 'Port 22 was open')"
    ))
    _old_table(conn, (
        "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, org_id INTEGER, user_id INTEGER, role VARCHAR(12), "
        "text TEXT)"
    ))
    conn.execute(text(
        "INSERT INTO chat_messages (id, org_id, user_id, role, text) "
        "VALUES (1, 1, 1, 'user', 'What happened on payments?')"
    ))
    _old_table(conn, (
        "CREATE TABLE clones (id INTEGER PRIMARY KEY, org_id INTEGER, slug VARCHAR(63), environment VARCHAR(20), "
        "library_version VARCHAR(20))"
    ))
    conn.execute(text(
        "INSERT INTO clones (id, org_id, slug, environment, library_version) VALUES (1, 1, 'estate', 'lab', '1')"
    ))
    _old_table(conn, (
        "CREATE TABLE clone_versions (id INTEGER PRIMARY KEY, org_id INTEGER, clone_id INTEGER, number INTEGER, "
        "author VARCHAR(120), reason TEXT, environment VARCHAR(20), library_version VARCHAR(20))"
    ))
    conn.execute(text(
        "INSERT INTO clone_versions (id, org_id, clone_id, number, author, reason, environment, library_version) "
        "VALUES (1, 1, 1, 1, 'Anita Rao', 'Drawn before projects', 'lab', '1')"
    ))
    _old_table(conn, (
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY, org_id INTEGER, clone_version_id INTEGER, node_key VARCHAR(80), "
        "type VARCHAR(80), name VARCHAR(200), environment VARCHAR(20))"
    ))
    conn.execute(text(
        "INSERT INTO nodes (id, org_id, clone_version_id, node_key, type, name, environment) "
        "VALUES (1, 1, 1, 'pay', 'service', 'Payments', 'lab')"
    ))
    _old_table(conn, (
        "CREATE TABLE edges (id INTEGER PRIMARY KEY, org_id INTEGER, clone_version_id INTEGER, from_key VARCHAR(80), "
        "to_key VARCHAR(80), type VARCHAR(40))"
    ))
    conn.execute(text(
        "INSERT INTO edges (id, org_id, clone_version_id, from_key, to_key, type) "
        "VALUES (1, 1, 1, 'pay', 'db', 'calls')"
    ))
    _old_table(conn, (
        'CREATE TABLE "groups" (id INTEGER PRIMARY KEY, org_id INTEGER, clone_version_id INTEGER, '
        "group_key VARCHAR(80), type VARCHAR(80), name VARCHAR(200), kind VARCHAR(40), environment VARCHAR(20))"
    ))
    conn.execute(text(
        'INSERT INTO "groups" (id, org_id, clone_version_id, group_key, type, name, kind, environment) '
        "VALUES (1, 1, 1, 'prod', 'boundary', 'Production', 'env', 'lab')"
    ))
    _old_table(conn, (
        "CREATE TABLE access_paths (id INTEGER PRIMARY KEY, org_id INTEGER, clone_version_id INTEGER, "
        "path_key VARCHAR(80), name VARCHAR(200), kind VARCHAR(40), environment VARCHAR(20))"
    ))
    conn.execute(text(
        "INSERT INTO access_paths (id, org_id, clone_version_id, path_key, name, kind, environment) "
        "VALUES (1, 1, 1, 'vpn', 'Office VPN', 'vpn', 'lab')"
    ))
    _old_table(conn, (
        "CREATE TABLE credential_refs (id INTEGER PRIMARY KEY, org_id INTEGER, clone_version_id INTEGER, "
        "ref_key VARCHAR(80), vault_uri VARCHAR(240))"
    ))
    conn.execute(text(
        "INSERT INTO credential_refs (id, org_id, clone_version_id, ref_key, vault_uri) "
        "VALUES (1, 1, 1, 'pay-key', 'vault://payments')"
    ))
    _old_table(conn, (
        "CREATE TABLE sense_points (id INTEGER PRIMARY KEY, org_id INTEGER, clone_version_id INTEGER, "
        "node_key VARCHAR(80), mode VARCHAR(40))"
    ))
    conn.execute(text(
        "INSERT INTO sense_points (id, org_id, clone_version_id, node_key, mode) "
        "VALUES (1, 1, 1, 'pay', 'logs')"
    ))
    _old_table(conn, (
        "CREATE TABLE change_sets (id INTEGER PRIMARY KEY, org_id INTEGER, clone_id INTEGER, source VARCHAR(30), "
        "status VARCHAR(20), base_version INTEGER)"
    ))
    conn.execute(text(
        "INSERT INTO change_sets (id, org_id, clone_id, source, status, base_version) "
        "VALUES (1, 1, 1, 'terraform_state', 'open', 1)"
    ))
    _old_table(conn, (
        "CREATE TABLE connectors (id INTEGER PRIMARY KEY, org_id INTEGER, key VARCHAR(40), name VARCHAR(80), "
        "category VARCHAR(30), status VARCHAR(20))"
    ))
    conn.execute(text(
        "INSERT INTO connectors (id, org_id, key, name, category, status) VALUES "
        "(1, 1, 'cloudwatch', 'AWS CloudWatch Logs', 'Cloud logs', 'reading'), "
        "(2, 1, 'okta', 'Okta', 'Identity', 'checked')"
    ))


# Tables made after projects existed: they never had a shape without a project, so no old row is there to upgrade.
BORN_WITH_A_PROJECT = frozenset({"vex_statements", "audits", "sphere_decisions", "sphere_versions",
                                 "sphere_graph_state", "finding_decisions"})


def _assert_every_row_has_a_project(conn):
    from app.projects import PROJECT_TABLES

    for table in PROJECT_TABLES:
        nulls = conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE project_id IS NULL')).scalar()
        total = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
        assert nulls == 0, table
        assert total >= 1 or table in BORN_WITH_A_PROJECT, table
    homes = {
        row.title: row.org_id for row in conn.execute(text(
            "SELECT s.title, p.org_id FROM signals s JOIN projects p ON p.id = s.project_id"
        ))
    }
    assert homes[_LEGACY_SIGNAL] == 1 and homes[_OTHER_SIGNAL] == 2
    projects = conn.execute(text(
        "SELECT o.slug AS org_slug, p.name AS name, p.slug AS project_slug, p.criticality AS criticality, "
        "p.archived AS archived FROM projects p JOIN organizations o ON o.id = p.org_id ORDER BY o.slug"
    )).all()
    assert [row.org_slug for row in projects] == ["legacy", "other", "quiet"]
    assert all(
        row.name == "Main" and row.project_slug == "main" and row.criticality == "medium" and not row.archived
        for row in projects
    )
    quiet = conn.execute(text(
        "SELECT p.id FROM projects p JOIN organizations o ON o.id = p.org_id WHERE o.slug = 'quiet'"
    )).scalar()
    assert conn.execute(text("SELECT COUNT(*) FROM incidents WHERE project_id = :id"), {"id": quiet}).scalar() == 0
    kept = conn.execute(text("SELECT project_id FROM audit_log WHERE action = 'kept from before'")).scalar()
    assert kept is None


def _assert_legacy_roles(conn):
    people = {row.email: row for row in conn.execute(text("SELECT email, role, account_role FROM users"))}
    assert people["ceo@legacy.example"].role == "ceo"
    assert people["ceo@legacy.example"].account_role == "account_owner"
    assert people["ceo@other.example"].account_role == "account_owner"
    assert people["ciso@legacy.example"].role == "ciso"
    assert people["ciso@legacy.example"].account_role == "security_lead"
    assert people["analyst@legacy.example"].role == "analyst"
    assert people["analyst@legacy.example"].account_role is None
    assert people["engineer@legacy.example"].role == "engineer"
    assert people["engineer@legacy.example"].account_role is None
    members = set(conn.execute(text(
        "SELECT u.email, m.role, o.slug FROM project_members m "
        "JOIN users u ON u.id = m.user_id JOIN projects p ON p.id = m.project_id "
        "JOIN organizations o ON o.id = p.org_id"
    )).all())
    assert members == {
        ("ceo@legacy.example", "approver", "legacy"),
        ("analyst@legacy.example", "engineer", "legacy"),
        ("engineer@legacy.example", "engineer", "legacy"),
        ("ceo@other.example", "approver", "other"),
    }


def _assert_account_tables_have_no_project(engine):
    from sqlalchemy import inspect

    columns = inspect(engine)
    for name in _ACCOUNT_TABLES:
        assert "project_id" not in {col["name"] for col in columns.get_columns(name)}, name
    assert "project_id" in {col["name"] for col in columns.get_columns("audit_log")}


def _assert_old_user_can_still_read(engine):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.db import get_db
    from app.main import app

    def _db():
        db = Session(engine, autoflush=False, expire_on_commit=False)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    # Not a context manager: entering one would run startup and seed the suite database.
    client = TestClient(app)
    try:
        _read_legacy_company(client)
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)


def _read_legacy_company(client):
    from conftest import PASSWORD

    def _headers(email, role):
        res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["step"] == "done"
        assert body["user"]["role"] == role
        return {"Authorization": f"Bearer {body['token']}"}

    def _titles(headers, path):
        res = client.get(path, headers=headers)
        assert res.status_code == 200, res.text
        return [row["title"] for row in res.json()]

    ceo = _headers("ceo@legacy.example", "ceo")
    engineer = _headers("engineer@legacy.example", "engineer")
    for headers in (ceo, engineer):
        assert _titles(headers, "/api/incidents") == [_LEGACY_INCIDENT]
        assert _titles(headers, "/api/signals") == [_LEGACY_SIGNAL]
        assert _titles(headers, "/api/remediations") == [_LEGACY_FIX]
    probe = {"name": "Rules check", "provider": "rules"}
    assert client.post("/api/models", headers=engineer, json=probe).status_code == 403
    allowed = client.post("/api/models", headers=ceo, json=probe)
    assert allowed.status_code == 200, allowed.text


# Old seed texts the upgrade rewords. A real company may hold the same words, and keeps them.
_OLD_SIGNALS = ("Probe timing matches known AI attack tooling", "ssh talks to the internet on unusual port 3389")
_OLD_SUMMARIES = ("Bulk probing that looks like AI-driven attack tooling.",
                  "web-1 talks to the internet on an unusual port, which nobody expected.")
_OLD_EVENT = "Network WhiteHat flagged systematic probing of /api/v2"


def test_upgrade_rewords_only_the_demo_companys_rows(engine):
    from datetime import datetime

    from app.models import Incident, IncidentEvent, Organization, Signal

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for slug in ("acme", "realco"):
            org = Organization(name=slug, slug=slug, status="live", live_at=datetime(2026, 9, 1))
            db.add(org)
            db.flush()
            for n, summary in enumerate(_OLD_SUMMARIES):
                inc = Incident(org_id=org.id, ref=f"INC-{n + 1}", title="Old incident", severity="high",
                               status="open", summary=summary, origin="live")
                db.add(inc)
                db.flush()
                db.add(IncidentEvent(incident_id=inc.id, kind="signal", text=_OLD_EVENT))
            for title in _OLD_SIGNALS:
                db.add(Signal(org_id=org.id, severity="medium", title=title, origin="live"))
        db.commit()
    _advance_ids(engine)
    migrate(engine)

    def rows(slug):
        with engine.connect() as conn:
            org = conn.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug}).scalar()
            signals = conn.execute(text("SELECT title FROM signals WHERE org_id = :o ORDER BY id"), {"o": org})
            summaries = conn.execute(text("SELECT summary FROM incidents WHERE org_id = :o ORDER BY id"), {"o": org})
            events = conn.execute(text("SELECT e.text FROM incident_events e JOIN incidents i ON i.id = e.incident_id "
                                       "WHERE i.org_id = :o ORDER BY e.id"), {"o": org})
            return list(signals.scalars()), list(summaries.scalars()), list(events.scalars())

    assert rows("realco") == (list(_OLD_SIGNALS), list(_OLD_SUMMARIES), [_OLD_EVENT, _OLD_EVENT])
    signals, summaries, events = rows("acme")
    assert not set(signals) & set(_OLD_SIGNALS)
    assert not set(summaries) & set(_OLD_SUMMARIES)
    assert _OLD_EVENT not in events


def test_upgrade_renumbers_shared_references_and_makes_them_unique(engine):
    with engine.begin() as conn:
        _old_table(conn, "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR(120), "
                         "plan VARCHAR(20), created_at DATETIME)")
        conn.execute(text("INSERT INTO organizations VALUES (1, 'One Co', 'growth', '2026-09-01 10:00:00'), "
                          "(2, 'Two Co', 'growth', '2026-09-01 10:00:00')"))
        _old_table(conn, "CREATE TABLE incidents (id INTEGER PRIMARY KEY, org_id INTEGER, ref VARCHAR(20), "
                         "title VARCHAR(200), severity VARCHAR(10), status VARCHAR(20), created_at DATETIME)")
        for row_id, org_id, ref in ((1, 1, "INC-2201"), (2, 1, "INC-2202"), (3, 1, "INC-2201"), (4, 2, "INC-2201")):
            conn.execute(text("INSERT INTO incidents VALUES (:id, :org, :ref, 'Old incident', 'high', 'open', "
                              "'2026-09-01 10:00:00')"), {"id": row_id, "org": org_id, "ref": ref})
    _advance_ids(engine)
    Base.metadata.create_all(engine)
    done = migrate(engine)
    assert "renumbered 1 incidents that shared a reference" in done
    with engine.connect() as conn:
        refs = dict(conn.execute(text("SELECT id, ref FROM incidents")).all())
    assert refs == {1: "INC-2201", 2: "INC-2202", 3: "INC-2203", 4: "INC-2201"}
    _assert_matches_models(engine)
    with pytest.raises(IntegrityError) as refused, engine.begin() as conn:
        conn.execute(text("UPDATE incidents SET ref = 'INC-2202' WHERE id = 1"))
    assert "unique" in str(refused.value).lower()
    assert migrate(engine) == []

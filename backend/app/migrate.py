"""Additive schema migration, run at every start.

`create_all` makes missing tables but never changes existing ones. This adds any
column the models have and the database lacks, fills list and settings columns
that are empty on old rows, and drops tables of retired features. It never drops
a column, so upgrades are safe to re-run. For data changes, write an explicit
step in `_upgrade_rows` and note it in docs/operations/backups-and-upgrades.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import inspect, text, update
from sqlalchemy.engine import Engine

from .connectors.routing import upgrade_attachments
from .db import Base, utcnow
from .incident_refs import next_ref
from .models import MigrationStep
from .projects import upgrade_legacy_accounts
from .schema_sync import match_models
from .seed import SAMPLE_INCIDENTS, SAMPLE_REMEDIATIONS

log = logging.getLogger("whitehats.migrate")

# Feature removed in 1.1: employee onboarding.
RETIRED_TABLES = ("onboarding_tasks", "access_grants", "employees", "role_templates")


def _default_sql(col) -> str:
    default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
    if default is None:
        return ""
    if isinstance(default, bool):
        return f" DEFAULT {'TRUE' if default else 'FALSE'}"
    if isinstance(default, (int, float)):
        return f" DEFAULT {default}"
    if isinstance(default, str):
        return " DEFAULT '" + default.replace("'", "''") + "'"
    return ""


def migrate(engine: Engine) -> list[str]:
    done = []
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in RETIRED_TABLES:
            if table in existing:
                conn.execute(text(f"DROP TABLE {table}"))
                done.append(f"dropped retired table {table}")
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}{_default_sql(col)}"))
                done.append(f"added {table.name}.{col.name}")
        done += _fill_empty_defaults(conn)
        done += _upgrade_rows(conn)
    done += match_models(engine)
    with engine.begin() as conn:
        done += _run_once(conn)
    for line in done:
        log.warning("migration: %s", line)
    return done


def _fill_empty_defaults(conn) -> list[str]:
    """JSON columns default to a fresh list or dict, which ALTER TABLE cannot express, so old rows get NULL."""
    done = []
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if col.default is None or not callable(col.default.arg) or col.type.__class__.__name__ != "JSON":
                continue
            empty = col.default.arg(None)
            n = conn.execute(update(table).where(col.is_(None)).values({col.name: empty})).rowcount
            if n:
                done.append(f"filled {n} empty {table.name}.{col.name}")
    return done


def _run_once(conn) -> list[str]:
    """Data steps that must never repeat, because a later row could look like the old one.

    Each runs after match_models, so a column it sets to null already allows null.
    """
    done = []
    finished = set(conn.execute(text("SELECT name FROM migration_steps")).scalars())
    for name, step in _ONCE_STEPS:
        if name in finished:
            continue
        done += step(conn)
        conn.execute(MigrationStep.__table__.insert().values(name=name, done_at=utcnow()))
    return done


def _clear_coverage_stamps(conn) -> list[str]:
    """Coverage used to be stamped 0, 80 or 90. After this runs once, any stored number is a measurement."""
    n = conn.execute(text("UPDATE agents SET coverage = NULL WHERE coverage IN (0, 80, 90)")).rowcount
    return [f"cleared stamped coverage on {n} WhiteHats"] if n else []


def _contained_is_investigating(conn) -> list[str]:
    """FD.2 dropped "contained": such an incident is still being worked."""
    n = conn.execute(text("UPDATE incidents SET status = 'investigating' WHERE status = 'contained'")).rowcount
    return [f"moved {n} contained incidents to investigating"] if n else []


_ONCE_STEPS = (
    ("clear-coverage-stamps", _clear_coverage_stamps),
    ("contained-is-investigating", _contained_is_investigating),
)


def _upgrade_rows(conn) -> list[str]:
    """Data steps for rows created by older versions. Each one only touches rows that still need it."""
    done = []
    # 1.1: every organisation needs a short name; companies from 1.0 were already live.
    n = conn.execute(text("UPDATE organizations SET slug = 'org-' || id WHERE slug IS NULL OR slug = ''")).rowcount
    if n:
        done.append(f"gave {n} organisations a short name")
    n = conn.execute(text("UPDATE organizations SET live_at = created_at "
                          "WHERE status = 'live' AND live_at IS NULL AND is_platform = FALSE")).rowcount
    if n:
        done.append(f"recorded go-live date for {n} organisations")
    # The demo used to insert 29 made-up scores. Those rows have an empty breakdown.
    # Compared as text: Postgres cannot compare a JSON column with a string.
    n = conn.execute(text(
        "DELETE FROM posture_snapshots WHERE CAST(breakdown AS TEXT) IN ('{}', 'null', '')"
    )).rowcount
    if n:
        done.append(f"removed {n} invented posture history rows")
    done += _label_demo_samples(conn)
    done += _mark_seeded_models(conn)
    done += _renumber_shared_refs(conn)
    done += upgrade_legacy_accounts(conn)
    done += upgrade_attachments(conn)
    return done


def _mark_seeded_models(conn) -> list[str]:
    """Older seeds were recognised by deployment name. Stamp the slot once.

    After this, startup matches seed_slot only, so a model a person named
    the same way is left alone.
    """
    done = []
    n = conn.execute(text(
        "UPDATE model_deployments SET seed_slot = 'primary' "
        "WHERE seed_slot = '' AND provider = 'azure_openai' AND priority = 1 "
        "AND deployment IN ('gpt-6-luna', 'gpt-5.4-mini')"
    )).rowcount
    if n:
        done.append(f"marked {n} seeded primary models")
    n = conn.execute(text(
        "UPDATE model_deployments SET seed_slot = 'optional' "
        "WHERE seed_slot = '' AND provider = 'azure_openai' AND deployment = 'gpt-5.6-terra'"
    )).rowcount
    if n:
        done.append(f"marked {n} seeded optional models")
    return done


# The seed's demo company. Old databases have no other marker, and the short name is unique.
_DEMO_ROWS = "org_id IN (SELECT id FROM organizations WHERE slug = 'acme')"


def _renumber_shared_refs(conn) -> list[str]:
    """References become unique per company (match_models adds the rule). Later copies take the next number."""
    taken: dict[int, set[str]] = {}
    copies = []
    for row_id, org_id, ref in conn.execute(text("SELECT id, org_id, ref FROM incidents ORDER BY org_id, id")):
        refs = taken.setdefault(org_id, set())
        if ref in refs:
            copies.append((row_id, org_id))
        refs.add(ref)
    for row_id, org_id in copies:
        new = next_ref(taken[org_id])
        taken[org_id].add(new)
        conn.execute(text("UPDATE incidents SET ref = :ref WHERE id = :id"), {"ref": new, "id": row_id})
    return [f"renumbered {len(copies)} incidents that shared a reference"] if copies else []


def _label_demo_samples(conn) -> list[str]:
    """Mark the seeded demo rows, and reword signals no shipped detector can raise.

    Each statement matches only the old text in the demo company, so a real company's rows are left alone.
    """
    done = []
    rewords = (
        ("Systematic probing of 1,140 API routes in 9 minutes",
         "SSH on port 22 accepts connections from any network", "high", "T1046", "network", "Network"),
        ("Probe timing matches known AI attack tooling",
         "Remote Desktop on port 3389 accepts connections from any network", "high", "T1046", "network", "Network"),
        ("Full-table reads on users from a single lookup request",
         "Log shape needs review: error SELECT * FROM users", "high", "", "endpoint", "Endpoint"),
        ("svc_deployer granted org admin outside change control",
         "Root account used for AttachUserPolicy", "high", "T1078.004", "cloud", "Cloud Posture"),
        ("Live payments secret committed to payments-service",
         "A new access key was created", "medium", "T1098.001", "cloud", "Cloud Posture"),
        ("Look-alike invoice domain acme-flntech.com",
         "12 failed sign-ins in one batch on finance-mail", "medium", "T1110", "identity", "Identity"),
    )
    n = 0
    for old, new, severity, mitre, key, name in rewords:
        n += conn.execute(text(
            "UPDATE signals SET title = :new, severity = :severity, mitre = :mitre, origin = 'sample', "
            "detail = :detail, source = 'whitehat:' || :key, "
            "agent_id = (SELECT id FROM agents WHERE agents.org_id = signals.org_id AND agents.key = :key) "
            f"WHERE title = :old AND {_DEMO_ROWS}"
        ), {"old": old, "new": new, "severity": severity, "mitre": mitre, "key": key,
            "detail": f"Seen by the {name} WhiteHat."}).rowcount
    if n:
        done.append(f"reworded {n} sample signals to match shipped detectors")
    n = conn.execute(text(
        "UPDATE incidents SET origin = 'sample' WHERE origin != 'sample' "
        "AND ref IN ('INC-2208', 'INC-2211', 'INC-2214', 'INC-2190') "
        f"AND {_DEMO_ROWS}"
    )).rowcount
    if n:
        done.append(f"marked {n} demo incidents as sample")
    n = conn.execute(text(
        f"UPDATE incidents SET summary = :summary WHERE summary LIKE '%AI-driven attack tooling%' AND {_DEMO_ROWS}"
    ), {"summary": "SSH on 10.2.7.44 accepts connections from any network, and a process there talks to the "
                   "internet on an unusual port. An analyst confirmed the user lookup route builds SQL from raw "
                   "input."}).rowcount
    if n:
        done.append(f"reworded {n} incident summaries that claimed AI attack tooling")
    events = (
        ("Network WhiteHat flagged systematic probing of /api/v2",
         "Network WhiteHat flagged SSH on port 22 open to any network"),
        ("Endpoint WhiteHat saw full-table reads in the database logs",
         "Endpoint WhiteHat flagged a log shape: error SELECT * FROM users"),
        ("Identity WhiteHat: admin grant with no matching change ticket",
         "Cloud Posture WhiteHat: root account used for AttachUserPolicy"),
        ("Fix Factory matched a live secret pattern",
         "Cloud Posture WhiteHat: a new access key was created"),
        ("Identity WhiteHat flagged sign-in attempts from a look-alike domain",
         "Identity WhiteHat: 12 failed sign-ins in one batch on finance-mail"),
        ("Edge rule rate-limits the probing host. Proposed network isolation.",
         "Edge rule rate-limits 10.2.7.44. Proposed network isolation."),
    )
    n = 0
    for old, new in events:
        n += conn.execute(text(
            "UPDATE incident_events SET text = :new WHERE text = :old "
            f"AND incident_id IN (SELECT id FROM incidents WHERE {_DEMO_ROWS})"
        ), {"old": old, "new": new}).rowcount
    if n:
        done.append(f"reworded {n} demo timeline lines")
    n = conn.execute(text(
        "UPDATE remediations SET origin = 'sample' WHERE origin != 'sample' "
        f"AND {_DEMO_ROWS} "
        "AND title IN ('Fix SQL injection in user lookup', 'Isolate the probing host', "
        "'Revoke the out-of-process admin grant', 'Fix uploads that can read any server file', "
        "'Restore laptop protection telemetry')"
    )).rowcount
    if n:
        done.append(f"marked {n} demo fixes as sample")
    n = conn.execute(text(
        "UPDATE tenant_checks SET origin = 'sample' WHERE origin != 'sample' "
        f"AND {_DEMO_ROWS} "
        "AND (detail = 'Recorded during the demo setup.' "
        "OR detail = 'Demo domain acme.example cannot carry DNS records.')"
    )).rowcount
    if n:
        done.append(f"marked {n} demo setup checks as sample")
    n = conn.execute(text(
        f"UPDATE signals SET title = :new, severity = 'high' WHERE title = :old AND {_DEMO_ROWS}"
    ), {"old": "ssh talks to the internet on unusual port 3389",
        "new": "Remote Desktop on port 3389 accepts connections from any network"}).rowcount
    if n:
        done.append(f"raised {n} sample network signals to a finding the WhiteHat can emit")
    n = conn.execute(text(
        "UPDATE incidents SET summary = :summary "
        f"WHERE summary LIKE '%talks to the internet%unusual port%' AND {_DEMO_ROWS}"
    ), {"summary": "SSH on 10.2.7.44 accepts connections from any network, and Remote Desktop on port 3389 does too. "
                   "An analyst confirmed the user lookup route builds SQL from raw input."}).rowcount
    if n:
        done.append(f"reworded {n} incident summaries that described an impossible network signal")
    n = conn.execute(text(
        "UPDATE organizations SET milestones_origin = 'sample', contract_origin = 'sample' "
        "WHERE slug = 'acme' AND milestones_origin = 'live' AND contract_origin = 'live' "
        "AND CAST(contract AS TEXT) LIKE '%ACME-2026-001%'"
    )).rowcount
    if n:
        done.append(f"marked milestones and the contract as sample on {n} demo companies")
    # Seeded incidents used to mix a headline with signals about another event.
    done += _align_sample_story(conn)
    done += _honest_connectors(conn)
    return done


# Previous sample headlines. The replacement text is the current seed.
_OLD_INCIDENT_TITLES = {
    "INC-2208": "Automated reconnaissance against the customer API",
    "INC-2211": "Admin role granted outside change control",
    "INC-2214": "Live payments key found in source code",
    "INC-2190": "Phishing campaign against the finance team",
}
_OLD_LOG_SIGNAL = "Log shape needs review: error SELECT * FROM users"
_OLD_SQL_WHY = "This is the weakness the probing found. Closing it ends the data risk."
_OLD_FIXES = {
    "Isolate the probing host": "Disable the user lookup route",
    "Revoke the out-of-process admin grant": "Remove the policy the root account attached and disable the new access key",
}
# (signal title, incident ref it used to sit on, incident ref it belongs to now)
_SIGNAL_MOVES = (
    ("SSH on port 22 accepts connections from any network", "INC-2208", "INC-2214"),
    ("Remote Desktop on port 3389 accepts connections from any network", "INC-2208", "INC-2214"),
    ("A new access key was created", "INC-2214", "INC-2211"),
)
_SAMPLE = "(origin = 'sample' OR org_id IN (SELECT id FROM organizations WHERE slug = 'acme'))"


def _story(ref: str) -> dict:
    return next(item for item in SAMPLE_INCIDENTS if item["ref"] == ref)


def _fix(title: str) -> dict:
    return next(item for item in SAMPLE_REMEDIATIONS if item["title"] == title)


def _as_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    text_value = str(value).replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text_value)


def _sample_incident_clause(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"({prefix}origin = 'sample' OR {prefix}org_id IN "
        "(SELECT id FROM organizations WHERE slug = 'acme'))"
    )


def _align_sample_story(conn) -> list[str]:
    """Rewrite seeded demo incidents so the title, summary, signals and fixes are one event.

    Matches the previous sample sentences only. A live row is left alone. The new
    sentences are the current seed, so a fresh database and an upgraded one agree.
    """
    n = 0
    n += _rewrite_incident_text(conn)
    n += _rewrite_log_signal(conn)
    n += _move_sample_signals(conn)
    n += _rewrite_sample_events(conn)
    n += _rewrite_sample_fixes(conn)
    ordered = _put_sql_fix_first(conn)
    done = []
    if n:
        done.append(f"aligned {n} seeded sample sentences")
    if ordered:
        done.append(f"listed the sample SQL fix first for {ordered} companies")
    return done


def _rewrite_incident_text(conn) -> int:
    n = 0
    for item in SAMPLE_INCIDENTS:
        changed = conn.execute(text(
            "UPDATE incidents SET title = :title, summary = :summary, business_impact = :impact "
            f"WHERE ref = :ref AND title = :old AND {_SAMPLE}"
        ), {
            "title": item["title"], "summary": item["summary"], "impact": item["impact"],
            "ref": item["ref"], "old": _OLD_INCIDENT_TITLES[item["ref"]],
        }).rowcount
        n += changed
        if item["ref"] == "INC-2208" and changed:
            # Older seeds have no line for taking the route offline. Add it once.
            kind, event_text, actor = item["events"][2][1:]
            n += conn.execute(text(
                "INSERT INTO incident_events (incident_id, project_id, at, kind, text, actor) "
                "SELECT id, project_id, created_at, :kind, :text, :actor FROM incidents "
                "WHERE ref = 'INC-2208' AND origin = 'sample' AND NOT EXISTS ("
                "SELECT 1 FROM incident_events e WHERE e.incident_id = incidents.id AND e.text = :text)"
            ), {"kind": kind, "text": event_text, "actor": actor}).rowcount
    return n


def _rewrite_log_signal(conn) -> int:
    signal = _story("INC-2208")["signals"][0]
    return conn.execute(text(
        "UPDATE signals SET title = :title, entity = :entity WHERE origin = 'sample' AND title = :old"
    ), {"title": signal[3], "entity": signal[4], "old": _OLD_LOG_SIGNAL}).rowcount


def _move_sample_signals(conn) -> int:
    n = 0
    for title, old_ref, new_ref in _SIGNAL_MOVES:
        rows = conn.execute(text(
            "SELECT signals.id, src.org_id FROM signals "
            "JOIN incidents AS src ON src.id = signals.incident_id "
            "WHERE signals.origin = 'sample' AND signals.title = :title AND src.ref = :old "
            f"AND {_sample_incident_clause('src')}"
        ), {"title": title, "old": old_ref}).all()
        for sig_id, org_id in rows:
            dest = conn.execute(text(
                "SELECT id FROM incidents WHERE org_id = :org AND ref = :ref"
            ), {"org": org_id, "ref": new_ref}).scalar()
            if dest is None:
                continue
            n += conn.execute(text(
                "UPDATE signals SET incident_id = :dest WHERE id = :id"
            ), {"dest": dest, "id": sig_id}).rowcount
    return n


def _rewrite_sample_events(conn) -> int:
    """Event indexes follow SAMPLE_INCIDENTS. Texts that already match the seed stay put."""
    n = 0
    endpoint = _story("INC-2208")["events"][0][2]
    firewall = _story("INC-2214")["events"][2][2]
    desktop = _story("INC-2214")["events"][1][2]
    stopped = _story("INC-2190")["events"][1][2]
    ssh = _story("INC-2214")["events"][0][2]
    access_key = _story("INC-2211")["events"][1][2]
    n += _retitle_events(conn, "Endpoint WhiteHat flagged a log shape: error SELECT * FROM users", endpoint)
    n += _retitle_events(conn, "Domain blocked, messages pulled from inboxes", stopped)
    n += _retitle_events(conn, "Key rotated with the payments provider. Code fix sent to Fix Factory.", desktop)
    n += _move_events(conn, ssh, ssh, "INC-2208", "INC-2214")
    n += _move_events(
        conn, "Edge rule rate-limits 10.2.7.44. Proposed network isolation.", firewall, "INC-2208", "INC-2214")
    n += _move_events(conn, access_key, access_key, "INC-2214", "INC-2211")
    return n


def _retitle_events(conn, old: str, new: str) -> int:
    if old == new:
        return 0
    return conn.execute(text(
        "UPDATE incident_events SET text = :new WHERE text = :old AND incident_id IN ("
        f"SELECT id FROM incidents WHERE {_SAMPLE})"
    ), {"old": old, "new": new}).rowcount


def _move_events(conn, old: str, new: str, old_ref: str, new_ref: str) -> int:
    rows = conn.execute(text(
        "SELECT e.id, i.org_id FROM incident_events AS e "
        "JOIN incidents AS i ON i.id = e.incident_id "
        "WHERE e.text = :text AND i.ref = :ref "
        f"AND {_sample_incident_clause('i')}"
    ), {"text": old, "ref": old_ref}).all()
    n = 0
    for event_id, org_id in rows:
        dest = conn.execute(text(
            "SELECT id FROM incidents WHERE org_id = :org AND ref = :ref"
        ), {"org": org_id, "ref": new_ref}).scalar()
        if dest is None:
            continue
        n += conn.execute(text(
            "UPDATE incident_events SET text = :text, incident_id = :dest WHERE id = :id"
        ), {"text": new, "dest": dest, "id": event_id}).rowcount
    return n


def _rewrite_sample_fixes(conn) -> int:
    n = 0
    for old_title, new_title in _OLD_FIXES.items():
        fix = _fix(new_title)
        n += conn.execute(text(
            "UPDATE remediations SET title = :title, target = :target, action = :action, why = :why, "
            "destination = :destination, risk = :risk, simulated_impact = :simulated_impact "
            f"WHERE title = :old AND {_SAMPLE}"
        ), {
            "title": fix["title"], "target": fix["target"], "action": fix["action"], "why": fix["why"],
            "destination": fix["destination"], "risk": fix["risk"],
            "simulated_impact": fix["simulated_impact"], "old": old_title,
        }).rowcount
    sql = _fix("Fix SQL injection in user lookup")
    n += conn.execute(text(
        "UPDATE remediations SET why = :why "
        f"WHERE title = :title AND why = :old AND {_SAMPLE}"
    ), {"why": sql["why"], "title": sql["title"], "old": _OLD_SQL_WHY}).rowcount
    return n


def _put_sql_fix_first(conn) -> int:
    """waiting() orders by created_at. Keep the sample SQL fix as the oldest pending row."""
    title = "Fix SQL injection in user lookup"
    n = 0
    orgs = conn.execute(text(
        "SELECT DISTINCT org_id FROM remediations "
        "WHERE origin = 'sample' AND status = 'pending' AND title = :title"
    ), {"title": title}).all()
    for (org_id,) in orgs:
        rows = conn.execute(text(
            "SELECT id, title, created_at, origin FROM remediations "
            "WHERE org_id = :org AND status = 'pending' ORDER BY created_at, id"
        ), {"org": org_id}).all()
        if not rows or any(row.origin != "sample" for row in rows):
            continue
        sql = next((row for row in rows if row.title == title), None)
        if sql is None or rows[0].id == sql.id:
            continue
        earliest = _as_dt(rows[0].created_at) - timedelta(seconds=1)
        conn.execute(text(
            "UPDATE remediations SET created_at = :at WHERE id = :id"
        ), {"at": earliest.strftime("%Y-%m-%d %H:%M:%S.%f"), "id": sql.id})
        n += 1
    return n


def _honest_connectors(conn) -> list[str]:
    """A saved password is not a connection, and CrowdStrike and Jira are not built."""
    done = []
    n = conn.execute(text(
        "UPDATE connectors SET status = 'unbuilt', config = '{}', secret_enc = '', "
        "last_check = 'Not built yet.', connected_at = NULL, state = '{}', last_sync_at = NULL "
        "WHERE key IN ('crowdstrike', 'jira') AND status != 'unbuilt'"
    )).rowcount
    if n:
        done.append(f"marked {n} unbuilt connectors")
    n = conn.execute(text(
        "UPDATE connectors SET status = 'checked' "
        "WHERE key IN ('okta', 'entra', 'google', 'github') AND status = 'verified'"
    )).rowcount
    if n:
        done.append(f"marked {n} credential checks as not reading")
    n = conn.execute(text(
        "UPDATE connectors SET status = 'reading' "
        "WHERE category = 'Cloud logs' AND status = 'verified' AND last_sync_at IS NOT NULL"
    )).rowcount
    if n:
        done.append(f"marked {n} cloud log sources as reading")
    n = conn.execute(text(
        "UPDATE connectors SET status = 'checked' "
        "WHERE category = 'Cloud logs' AND status = 'verified' AND last_sync_at IS NULL"
    )).rowcount
    if n:
        done.append(f"marked {n} cloud log sources as checked but not reading")
    return done

"""First-run data.

Always created: the WhiteHats platform organisation with one operator account.
With WH_SEED_DEMO=1 (the default) also one live demo tenant, clearly labelled,
so every screen has something to show. Set WH_SEED_DEMO=0 for a clean install:
operators then create real tenants from the operator console.
"""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .connectors.routing import attach_to_main
from .db import utcnow
from .demo import DEMO_NAME, DEMO_SLUG, add_story, is_reshaped, shape_demo
from .models import Connector, ModelDeployment, Organization, Signal, User
from .projects import apply_legacy_access, ensure_main_project
from .security import hash_password, seal
from .services import tenancy
from .whitehats import BUILTIN_KEYS, enable

CONNECTORS = [
    ("okta", "Okta", "Identity", "Sign-ins and multi-factor status for the Identity WhiteHat."),
    ("entra", "Microsoft Entra ID", "Identity", "Sign-ins and group membership."),
    ("google", "Google Workspace", "Identity", "Accounts, 2-step verification and admin roles."),
    ("aws", "AWS", "Cloud logs", "Inventory, CloudTrail and CloudWatch logs, read through a read-only role you create."),
    ("cloudwatch", "AWS CloudWatch Logs", "Cloud logs", "CloudTrail and application logs from AWS, read-only."),
    ("azure_monitor", "Azure Monitor", "Cloud logs", "Activity and sign-in logs from a Log Analytics workspace."),
    ("gcp", "Google Cloud", "Cloud logs", "Resources, permissions and admin activity of a Google Cloud project, read "
     "through a read-only reader you create. No key is stored."),
    ("gcp_logging", "Google Cloud Logging", "Cloud logs", "Cloud Audit Logs and application logs from Google Cloud."),
    ("github", "GitHub", "Code", "Repositories and code scanning alerts for Fix Factory."),
    ("bitbucket", "Bitbucket", "Code", "Read-only repository access in a Bitbucket Cloud workspace."),
    ("crowdstrike", "CrowdStrike", "Endpoint", "Device health and detections."),
    ("jira", "Jira", "Ticketing", "Tracked tickets for fixes that need a person."),
]

MODEL_ROLES = ["assistant", "summarizer", "executive", "triage", "review"]


def ensure_connectors(db: Session, org: Organization) -> None:
    """Give a company every connector type, including ones added after it was created."""
    have = set(db.scalars(select(Connector.key).where(Connector.org_id == org.id)))
    for key, name, cat, desc in CONNECTORS:
        if key not in have:
            status = "unbuilt" if key == "jira" else "available"
            row = Connector(org_id=org.id, key=key, name=name, category=cat, description=desc, status=status)
            db.add(row)
            db.flush()
            attach_to_main(db, row)


def tenant_defaults(db: Session, org: Organization) -> None:
    """Everything a new tenant starts with: connectors to fill in, the model gateway, its WhiteHats."""
    ensure_connectors(db, org)
    db.add_all([
        ModelDeployment(org_id=org.id, name="whitehats · gpt-6-luna", provider="azure_openai",
                        endpoint="https://whitehats.openai.azure.com", deployment="gpt-6-luna",
                        api_version="v1", roles=MODEL_ROLES, context_window=400000, max_output=1600, priority=1,
                        zero_retention=True, status="needs_key", seed_slot="primary",
                        notes="Primary model. Locally, the key is taken from AZURE_OPENAI_API_KEY at startup."),
        ModelDeployment(org_id=org.id, name="whitehats · gpt-5.6-terra", provider="azure_openai",
                        endpoint="https://whitehats.openai.azure.com", deployment="gpt-5.6-terra", api_version="v1",
                        roles=["assistant", "executive", "review"], context_window=400000, max_output=2000,
                        priority=2, enabled=False, zero_retention=True, status="untested", seed_slot="optional",
                        notes="Optional. Enable it when you want this deployment."),
        ModelDeployment(org_id=org.id, name="Claude (local CLI)", provider="claude_cli", deployment="sonnet",
                        roles=["summarizer", "review"], context_window=200000, max_output=1500, priority=5,
                        enabled=False, status="untested", notes="Uses the claude CLI on this server."),
        ModelDeployment(org_id=org.id, name="Rules engine", provider="rules", roles=[], context_window=8000,
                        max_output=800, priority=99, status="ready",
                        notes="Always on. Answers from the data when no model is reachable."),
    ])
    for key in org.contracted_agents or BUILTIN_KEYS:
        enable(db, org.id, key)
    db.flush()
    tenancy.ensure_checks(db, org)


def resource_root(endpoint: str) -> str:
    """Azure resource origin. Drops a trailing /openai or /openai/v1 path."""
    parsed = urlparse(endpoint.strip())
    path = parsed.path.rstrip("/")
    for suffix in ("/openai/v1", "/openai"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")


def apply_local_azure(db: Session) -> None:
    """Seal the local Azure key into each company's seeded primary model.

    Runs only when WH_ENV is exactly development. Unset does nothing, so a
    production install that forgot the setting cannot load a key from a stray
    .env. A key already stored on the row was typed on the screen and is kept.
    """
    if os.environ.get("WH_ENV") != "development":
        return
    key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    if not key or not endpoint:
        return
    root = resource_root(endpoint)
    rows = db.scalars(select(ModelDeployment).where(
        ModelDeployment.provider == "azure_openai",
        ModelDeployment.seed_slot.in_(("primary", "optional")),
    ).order_by(ModelDeployment.org_id, ModelDeployment.priority, ModelDeployment.id)).all()
    for dep in rows:
        if dep.secret_enc:
            continue
        _fill_seeded_azure(db, dep, root, key)
    db.flush()


def _fill_seeded_azure(db: Session, dep: ModelDeployment, root: str, key: str) -> None:
    if dep.seed_slot == "primary":
        dep.name = "whitehats · gpt-6-luna"
        dep.deployment = "gpt-6-luna"
        dep.endpoint = root
        dep.secret_enc = seal(key, db, dep.org_id)
        dep.status = "untested"
        dep.last_error = ""
        return
    dep.endpoint = root
    dep.name = "whitehats · gpt-5.6-terra"
    dep.deployment = "gpt-5.6-terra"


def seed(db: Session) -> None:
    if db.scalar(select(Organization.id).where(Organization.is_platform.is_(True))) is None:
        platform = Organization(name="WhiteHats", slug="whitehats", is_platform=True, status="live", plan="enterprise")
        db.add(platform)
        db.flush()
        ensure_main_project(db, platform)
        db.add(User(org_id=platform.id, email=settings.operator_email.lower(), name="WhiteHats Operations",
                    role="operator", title="Platform operator", password_hash=hash_password(settings.operator_password),
                    terms_accepted_at=utcnow()))
        db.commit()
    if db.scalar(select(Organization.id).where(Organization.is_platform.is_(False))) is not None:
        reshape_older_demo(db)
        return
    if os.environ.get("WH_SEED_DEMO", "1") == "0":
        return
    _demo(db)
    db.commit()


def reshape_older_demo(db: Session) -> bool:
    """Give a demo account seeded before AP.7 its four projects, so a kept database matches a fresh one.

    Only the seeded demo (slug acme, sample milestones) is touched, and only once.
    """
    org = db.scalar(select(Organization).where(Organization.slug == DEMO_SLUG, Organization.is_platform.is_(False)))
    if org is None or org.milestones_origin != "sample" or is_reshaped(db, org):
        return False
    resources = {signal[3]: signal[6] for item in SAMPLE_INCIDENTS for signal in item["signals"]}
    for row in db.scalars(select(Signal).where(
            Signal.org_id == org.id, Signal.origin == "sample", Signal.resource == "")):
        row.resource = resources.get(row.title, "")
    shape_demo(db, org, utcnow())
    db.commit()
    return True


# The real resources behind the fintech story. Whole estate holds the same ones (demo.py), so the account
# counts each finding once. 111122223333 is the example account number AWS uses in its own documents.
CUSTOMER_API = "arn:aws:lambda:ap-south-1:111122223333:function:customer-api"
CUSTOMER_API_HOST = "arn:aws:ec2:ap-south-1:111122223333:instance/i-0a1b2c3d4e5f60718"
ROOT_ACCOUNT = "arn:aws:iam::111122223333:root"

# One subject per incident. The title, summary, impact, signals and fixes describe that
# same event. Signal titles are ones Identity, Endpoint, Network, Cloud Posture or
# Insider Threat actually raise. See whitehats/builtin.py and services/network.py.
SAMPLE_INCIDENTS = (
    {
        "ref": "INC-2208",
        "title": "SQL injection in user lookup",
        "severity": "critical",
        "status": "investigating",
        "summary": (
            "The customer API on 10.2.7.44 builds the user lookup SQL from the lookup value. "
            "The error log shape is SELECT * FROM users for lookup, which is SQL injection."
        ),
        "impact": "SQL injection in the user lookup could expose about 82,000 customer records.",
        "owner": "Meera Pillai",
        "hours_ago": 5,
        "events": (
            (5, "signal", "Endpoint WhiteHat flagged a log shape: error SELECT * FROM users for lookup", "WhiteHats"),
            (4.2, "note", "Confirmed the user lookup route builds SQL from raw input. Sent to Fix Factory.",
             "Priyanka Das"),
            (4.0, "action", "Proposed disabling the user lookup route until the query is parameterised.",
             "Priyanka Das"),
        ),
        "signals": (
            (5, "endpoint", "high", "Log shape needs review: error SELECT * FROM users for lookup",
             "customer-api", "", CUSTOMER_API),
        ),
    },
    {
        "ref": "INC-2211",
        "title": "Root account used for AttachUserPolicy and a new access key",
        "severity": "high",
        "status": "open",
        "summary": (
            "In the cloud account for the customer API (ap-south-1) the root account called "
            "AttachUserPolicy, and a new access key was created. Neither change had a ticket."
        ),
        "impact": "The policy the root account attached, and the new access key, could grant control of the cloud account.",
        "owner": "",
        "hours_ago": 14,
        "events": (
            (14, "signal", "Cloud Posture WhiteHat: root account used for AttachUserPolicy", "WhiteHats"),
            (13.5, "signal", "Cloud Posture WhiteHat: a new access key was created", "WhiteHats"),
        ),
        "signals": (
            (14, "cloud", "high", "Root account used for AttachUserPolicy", "ap-south-1", "T1078.004", ROOT_ACCOUNT),
            (13.5, "cloud", "medium", "A new access key was created", "ap-south-1", "T1098.001", ROOT_ACCOUNT),
        ),
    },
    {
        "ref": "INC-2214",
        "title": "SSH and Remote Desktop accept connections from any network",
        "severity": "high",
        "status": "investigating",
        "summary": (
            "On the customer API host 10.2.7.44, SSH on port 22 and Remote Desktop on port 3389 "
            "accept connections from any network. A firewall rule now blocks those public paths."
        ),
        "impact": (
            "Anyone on the internet could reach the customer API host while SSH and Remote Desktop "
            "accept connections from any network."
        ),
        "owner": "Kavya Iyer",
        "hours_ago": 30,
        "events": (
            (30, "signal", "Network WhiteHat flagged SSH on port 22 open to any network", "WhiteHats"),
            (29.5, "signal", "Network WhiteHat flagged Remote Desktop on port 3389 open to any network", "WhiteHats"),
            (28, "action", "Firewall rule applied. SSH and Remote Desktop no longer accept public connections.",
             "Kavya Iyer"),
        ),
        "signals": (
            (30, "network", "high", "SSH on port 22 accepts connections from any network", "10.2.7.44", "T1046",
             CUSTOMER_API_HOST),
            (29.5, "network", "high", "Remote Desktop on port 3389 accepts connections from any network",
             "10.2.7.44", "T1046", CUSTOMER_API_HOST),
        ),
    },
    {
        "ref": "INC-2190",
        "title": "Failed sign-ins in one batch on finance-mail",
        "severity": "medium",
        "status": "resolved",
        "summary": "Twelve failed sign-ins arrived in one batch on finance-mail. No sign-in succeeded.",
        "impact": "None. No sign-in on finance-mail succeeded, and the batch has stopped.",
        "owner": "Meera Pillai",
        "hours_ago": 120,
        "events": (
            (120, "signal", "Identity WhiteHat: 12 failed sign-ins in one batch on finance-mail", "WhiteHats"),
            (118, "action", "Confirmed no sign-in succeeded. The batch has stopped.", "Meera Pillai"),
        ),
        "signals": (
            (120, "identity", "medium", "12 failed sign-ins in one batch on finance-mail", "finance-mail", "T1110",
             ""),
        ),
    },
)

# age is how long ago the row was proposed. The SQL fix is the oldest pending row, so waiting()
# lists "Fix SQL injection in user lookup" first.
SAMPLE_REMEDIATIONS = (
    {
        "ref": "INC-2208",
        "title": "Fix SQL injection in user lookup",
        "target": "customer-api",
        "action": "Replace string-built SQL with a parameterised query, proven by the original attack check.",
        "why": "The user lookup route builds SQL from raw input. Closing it ends the data risk.",
        "destination": "Fix Factory",
        "factory_intent": "WH-INT-2208-1",
        "risk": "high",
        "simulated_impact": "1 code change · tests pass · 0 services affected",
        "age": timedelta(seconds=40),
    },
    {
        "ref": "INC-2208",
        "title": "Disable the user lookup route",
        "target": "customer-api",
        "action": "Stop serving the user lookup route until the parameterised query is in place.",
        "why": "The user lookup builds SQL from raw input. Taking the route offline stops that while the code fix is proven.",
        "destination": "Slack",
        "risk": "medium",
        "simulated_impact": "1 route disabled · 0 other services affected",
        "age": timedelta(seconds=30),
    },
    {
        "ref": "INC-2211",
        "title": "Remove the policy the root account attached and disable the new access key",
        "target": "ap-south-1",
        "action": "Detach the policy the root account attached with AttachUserPolicy, and disable the new access key.",
        "why": "The root account used AttachUserPolicy and a new access key was created, with no ticket.",
        "destination": "Slack",
        "risk": "high",
        "simulated_impact": "1 policy detached · 1 key disabled",
        "age": timedelta(seconds=20),
    },
    {
        "ref": None,
        "title": "Fix uploads that can read any server file",
        "target": "files-service",
        "action": "Restrict file reads to the uploads folder, proven by a path traversal check.",
        "why": "Found during a weekly code scan.",
        "destination": "Fix Factory",
        "factory_intent": "WH-INT-2211-3",
        "risk": "critical",
        "simulated_impact": "1 code change · tests pass",
        "age": timedelta(seconds=10),
    },
    {
        "ref": None,
        "title": "Restore laptop protection telemetry",
        "target": "Endpoint agent",
        "action": "Restart the log forwarder on 3 laptops.",
        "why": "The Endpoint WhiteHat lost sight of 3 devices.",
        "destination": "Slack",
        "risk": "low",
        "status": "dispatched",
        "decided_by": "Meera Pillai",
        "decided_hours": 20,
        "result": "On-call engineer restarted the forwarder.",
        "simulated_impact": "3 devices · read-only restart",
        "age": timedelta(hours=20),
    },
)


def _demo(db: Session) -> None:
    now = utcnow()
    org = Organization(
        name=DEMO_NAME, slug=DEMO_SLUG, plan="growth", status="live", region=settings.region,
        domain="acme.example", domain_token=secrets.token_hex(8), mfa_required=False, live_at=now - timedelta(days=40),
        created_at=now - timedelta(days=45), created_by="WhiteHats Operations",
        ai_policy={"agreed": True, "providers": ["azure_openai", "rules"], "zero_retention": True},
        contract={"order_ref": "ACME-2026-001", "signed_at": "2026-08-28", "start_date": "2026-09-01",
                  "owner_name": "Anita Rao", "owner_email": "ceo@acme.example",
                  "security_contact": "Meera Pillai, +91 90000 00000", "pilot": True,
                  "pass_marks": "3 proven fixes and a weekly board report within 30 days",
                  "billing_contact": "finance@acme.example", "po_number": "PO-7781"},
        contracted_agents=list(BUILTIN_KEYS), go_live_operator="WhiteHats Operations", go_live_customer="Anita Rao",
        milestones_origin="sample", contract_origin="sample",
        # A healthy pilot: every first-value milestone met inside its target.
        milestones={key: (now - timedelta(days=45 - day)).isoformat() for key, day in
                    [("first_data", 1), ("first_score", 1), ("first_digest", 2), ("first_network", 2),
                     ("first_decision", 4), ("first_report", 6), ("first_fix", 10)]},
    )
    db.add(org)
    db.flush()
    project = ensure_main_project(db, org)
    pw = hash_password(settings.admin_password)
    people = []
    for email, name, role, title in [("ceo@acme.example", "Anita Rao", "ceo", "Chief Executive"),
                                     ("ciso@acme.example", "Meera Pillai", "ciso", "Chief Information Security Officer"),
                                     ("analyst@acme.example", "Priyanka Das", "analyst", "Security Analyst"),
                                     ("engineer@acme.example", "Kavya Iyer", "engineer", "Head of Platform")]:
        user = User(org_id=org.id, email=email, name=name, role=role, title=title, password_hash=pw,
                    terms_accepted_at=now - timedelta(days=44))
        db.add(user)
        people.append(user)
    db.flush()
    for user in people:
        # The demo engineer is in the home project, the Fintech shop. shape_demo opens the other three.
        added = [project.id] if user.role == "engineer" else None
        apply_legacy_access(db, user, added)
    tenant_defaults(db, org)
    rows = tenancy.ensure_checks(db, org)
    for key in ("contract", "dpa", "pass_marks", "billing"):
        rows[key].status, rows[key].checked_by, rows[key].checked_at = "passed", "WhiteHats Operations", now
        rows[key].detail = "Recorded during the demo setup."
        rows[key].origin = "sample"
    rows["domain"].status, rows["domain"].detail = "waived", "Demo domain acme.example cannot carry DNS records."
    rows["domain"].checked_by = "WhiteHats Operations"
    rows["domain"].origin = "sample"

    add_story(db, org, project, now, SAMPLE_INCIDENTS, SAMPLE_REMEDIATIONS)
    shape_demo(db, org, now)

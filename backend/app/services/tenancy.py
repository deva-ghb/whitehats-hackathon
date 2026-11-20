"""Tenant onboarding: readiness checks, the go-live gate, time to first value, health, retention, offboarding.

Every check has a key, a stage and a kind. Manual checks are ticked by the
operator; automatic checks are evaluated from live data each time someone looks.
A tenant goes live only when every required check has passed (or was waived by
an operator with a reason) and both the operator and the customer admin sign off.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta

import dns.exception
import dns.resolver
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from ..db import utcnow
from ..models import (
    INCIDENT_CLOSED,
    AccessPath,
    Agent,
    Audit,
    AuditLog,
    ChangeEvent,
    ChangeSet,
    ChatMessage,
    Clone,
    CloneVersion,
    Collector,
    Connector,
    CredentialRef,
    Edge,
    FindingDecision,
    Group,
    Incident,
    IncidentEvent,
    Invite,
    LLMCall,
    LogDigest,
    LogEvent,
    LogTemplate,
    ModelDeployment,
    NetworkFinding,
    NetworkFlow,
    Node,
    Organization,
    PostureSnapshot,
    ProcessSample,
    Project,
    ProjectConnection,
    ProjectMember,
    RegistryPackage,
    RegistryToken,
    Remediation,
    ScanPlanRecord,
    SensePoint,
    SettingsCheck,
    Signal,
    SphereDecision,
    SphereGraphState,
    SphereVersion,
    TemplateCount,
    TenantCheck,
    User,
    VaultSecret,
    VexStatement,
)
from . import deploy

REGIONS = {"in-south": "India (Hyderabad)", "in-west": "India (Mumbai)", "eu-west": "Europe (Ireland)",
           "us-east": "United States (Virginia)"}
PROVIDER_CHOICES = {"azure_openai": "Azure OpenAI", "vertex": "Gemini on Vertex AI", "openai_compatible": "Self-hosted or compatible endpoint",
                    "claude_cli": "Claude", "rules": "Rules engine only (no AI provider)"}


def providers_label(policy: dict) -> str:
    return ", ".join(PROVIDER_CHOICES.get(p, p) for p in (policy or {}).get("providers", []))

STAGES = [
    ("commercial", "Contract and legal"),
    ("security", "Security and compliance"),
    ("admin", "First admin"),
    ("technical", "Technical readiness"),
    ("golive", "Go-live"),
]


@dataclass(frozen=True)
class CheckDef:
    key: str
    stage: str
    title: str
    why: str
    kind: str  # manual | auto
    owner: str  # operator | customer
    doc: str
    required: str = "always"  # always | pilot | growth+ | optional


CHECKS = [
    CheckDef("contract", "commercial", "Signed contract or order form, with plan and start date",
             "No access before there is a contract that says what we provide.", "manual", "operator",
             "operations/tenant-onboarding#contract"),
    CheckDef("dpa", "commercial", "Data processing agreement signed, outside services acknowledged",
             "The customer must agree, in writing, which services may see their data, including AI model providers.",
             "manual", "operator", "operations/tenant-onboarding#dpa"),
    CheckDef("pass_marks", "commercial", "Pilot pass marks written down",
             "A pilot without agreed pass marks never ends in a decision.", "manual", "operator",
             "operations/tenant-onboarding#pass-marks", "pilot"),
    CheckDef("billing", "commercial", "Billing contact and purchase order number",
             "Invoices reach the right person first time.", "manual", "operator",
             "operations/tenant-onboarding#billing", "optional"),
    CheckDef("contacts", "commercial", "Customer owner and security contact named",
             "Someone accountable on their side, and someone to call at 2am.", "auto", "operator",
             "operations/tenant-onboarding#contacts"),
    CheckDef("region", "security", "Data stored in the agreed region",
             "Data residency is a legal duty under India's DPDP Act and Europe's GDPR.", "auto", "operator",
             "operations/tenant-onboarding#region"),
    CheckDef("retention", "security", "Retention periods set for logs, runs and the audit trail",
             "Keep what is needed and nothing longer.", "auto", "operator", "operations/tenant-onboarding#retention"),
    CheckDef("ai_policy", "security", "AI model policy agreed",
             "Which AI providers may read summaries of their data, and whether zero data retention is required.",
             "auto", "operator", "operations/tenant-onboarding#ai-policy"),
    CheckDef("domain", "security", "Email domain verified by DNS",
             "Proves the customer owns the domain, so only their staff can be invited.", "auto", "customer",
             "operations/tenant-onboarding#domain"),
    CheckDef("mfa", "security", "Two-step sign-in enforced for every user",
             "A stolen password alone must never open a security console.", "auto", "operator",
             "operations/tenant-onboarding#mfa"),
    CheckDef("support_access", "security", "WhiteHats support access off by default",
             "Our staff see nothing unless the customer grants time-limited access.", "auto", "operator",
             "operations/tenant-onboarding#support-access"),
    CheckDef("tenant_key", "security", "Company encryption key created",
             "Their stored secrets are sealed with their own key.", "auto", "operator",
             "operations/tenant-onboarding#encryption"),
    CheckDef("invite_sent", "admin", "Invitation sent to the customer's first admin",
             "Single use and expires in 72 hours; WhiteHats never knows their password.", "auto", "operator",
             "operations/tenant-onboarding#invite"),
    CheckDef("admin_ready", "admin", "First admin signed in, two-step sign-in on, terms accepted",
             "The customer now controls their own company.", "auto", "customer",
             "operations/tenant-onboarding#first-admin"),
    # Optional while identity connectors are coming soon (checklist section P): nobody could pass it.
    CheckDef("identity_connector", "technical", "Identity connector working (Okta, Entra ID or Google)",
             "Sign-in data is where most breaches start.", "auto", "customer", "connectors/okta", "optional"),
    CheckDef("cloud_logs", "technical", "Cloud logs flowing (AWS CloudWatch, Azure Monitor or Google Cloud Logging)",
             "Cloud logs show changes and sign-ins the servers alone never see.", "auto", "customer",
             "connectors/aws-cloudwatch", "growth+"),
    CheckDef("collector_reporting", "technical", "At least one collector or log source reporting",
             "No data, no protection.", "auto", "customer", "deploy/collectors"),
    CheckDef("whitehats", "technical", "Contracted WhiteHats switched on",
             "Every area they pay for is watched, with no blind spots.", "auto", "customer",
             "developers/adding-a-whitehat"),
    CheckDef("ai_model", "technical", "AI model tested, or rules-only mode agreed",
             "Briefings and digests need a working model inside the agreed policy.", "auto", "customer",
             "operations/ai-models"),
    CheckDef("alerts", "technical", "Alerts channel received a test message",
             "Critical signals reach a person even when nobody has the console open.", "auto", "customer",
             "connectors/alerts"),
    CheckDef("repos", "technical", "Code repositories reachable for Fix Factory",
             "Only needed if they want proven code fixes.", "auto", "customer", "connectors/github", "optional"),
    CheckDef("plan_limits", "technical", "Usage within plan limits",
             "No surprise overages on the first invoice.", "auto", "operator", "operations/tenant-onboarding#plans"),
    CheckDef("signoff", "golive", "Operator and customer admin both signed off",
             "Going live is a joint decision, recorded in the audit trail.", "auto", "operator",
             "operations/tenant-onboarding#go-live"),
]
CHECK = {c.key: c for c in CHECKS}

MILESTONES = [
    ("first_data", "First data received", 1),
    ("first_score", "First posture score", 1),
    ("first_digest", "First log digest", 3),
    ("first_network", "First network snapshot", 3),
    ("first_decision", "First approval decision", 7),
    ("first_report", "First board report", 7),
    ("first_fix", "First proven fix approved", 14),
]

PLAN_LIMITS = {"starter": {"seats": 5, "collectors": 10}, "growth": {"seats": 20, "collectors": 100},
               "enterprise": {"seats": 500, "collectors": 5000}}


def is_required(c: CheckDef, org: Organization) -> bool:
    if c.required == "always":
        return True
    if c.required == "pilot":
        return bool((org.contract or {}).get("pilot"))
    if c.required == "growth+":
        return org.plan in ("growth", "enterprise")
    return False


def ensure_checks(db: Session, org: Organization) -> dict[str, TenantCheck]:
    have = {c.key: c for c in db.scalars(select(TenantCheck).where(TenantCheck.org_id == org.id))}
    for c in CHECKS:
        if c.key not in have:
            row = TenantCheck(org_id=org.id, key=c.key)
            db.add(row)
            have[c.key] = row
    db.flush()
    return have


# ------------------------------------------------------------------ automatic evaluators

def _domain(org: Organization) -> tuple[bool, str]:
    if not org.domain:
        return False, "No email domain set."
    try:
        answers = dns.resolver.resolve(org.domain, "TXT", lifetime=6)
    except dns.resolver.NXDOMAIN:
        return False, f"{org.domain} does not exist in DNS."
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return False, f"No TXT records on {org.domain} yet. Add: whitehats-verify={org.domain_token}"
    except dns.exception.DNSException as exc:
        return False, f"DNS lookup failed: {exc.__class__.__name__}. Try again shortly."
    want = f"whitehats-verify={org.domain_token}"
    for rr in answers:
        txt = b"".join(rr.strings).decode(errors="replace")
        if txt.strip() == want:
            if not org.domain_verified_at:
                org.domain_verified_at = utcnow()
            return True, f"Found {want} on {org.domain}."
    return False, f"TXT records found on {org.domain}, but not {want}."


def _repo_reachable(url: str) -> tuple[bool, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(["git", "ls-remote", "--heads", url], capture_output=True, text=True, timeout=20,
                              env=env, check=False)
    except subprocess.TimeoutExpired:
        return False, f"{url}: timed out."
    if proc.returncode != 0:
        return False, f"{url}: {(proc.stderr or 'not reachable').strip().splitlines()[-1][:160]}"
    return True, f"{url}: reachable."


def usage(db: Session, org: Organization) -> dict:
    return {
        "seats": db.scalar(select(func.count(User.id)).where(User.org_id == org.id, User.active.is_(True))) or 0,
        "collectors": db.scalar(select(func.count(Collector.id)).where(Collector.org_id == org.id)) or 0,
    }


def evaluate(db: Session, org: Organization, include_slow: bool = True) -> dict[str, TenantCheck]:
    """Re-test every automatic check. `include_slow` adds DNS and git lookups."""
    rows = ensure_checks(db, org)
    now = utcnow()

    def set_(key: str, ok: bool, detail: str, not_yet: str = "failed") -> None:
        row = rows[key]
        if row.status == "waived":
            return
        row.status, row.detail, row.checked_at, row.checked_by = ("passed" if ok else not_yet), detail, now, "automatic"

    c = org.contract or {}
    set_("contacts", bool(c.get("owner_email") and c.get("security_contact")),
         f"Owner {c.get('owner_name') or '?'} <{c.get('owner_email') or 'missing'}>; security contact "
         f"{c.get('security_contact') or 'missing'}.")
    set_("region", org.region == settings.region,
         f"Company region {REGIONS.get(org.region, org.region)}; this installation stores data in "
         f"{REGIONS.get(settings.region, settings.region)}."
         + ("" if org.region == settings.region else " Onboard them on an installation in their region."))
    ok_ret = 7 <= org.retention_log_days <= 365 and 30 <= org.retention_run_days <= 2555 \
        and org.retention_audit_days >= 365
    set_("retention", ok_ret, f"Logs {org.retention_log_days} days, runs {org.retention_run_days} days, audit "
         f"{org.retention_audit_days} days." + ("" if ok_ret else " Audit trail must be kept at least 365 days."))
    pol = org.ai_policy or {}
    set_("ai_policy", bool(pol.get("agreed") and pol.get("providers")),
         ("Allowed: " + providers_label(pol) + (" · zero data retention required"
                                                                  if pol.get("zero_retention") else ""))
         if pol.get("agreed") else "Not agreed yet.")
    if include_slow:
        set_("domain", *_domain(org))
    set_("mfa", org.mfa_required or org.sso_required,
         "Two-step sign-in is enforced." if org.mfa_required else
         "Single sign-on enforced." if org.sso_required else "Two-step sign-in is optional. Turn it on.")
    support_on = bool(org.support_access_until and org.support_access_until > now)
    # Granting access while getting help is expected; it only has to be off again before go-live.
    set_("support_access", not support_on, "Off." if not support_on else
         f"Granted by the customer until {org.support_access_until:%d %b %H:%M} UTC ({org.support_access_reason}). "
         "It must end or be revoked before go-live.", "pending")
    set_("tenant_key", bool(org.secret_key_enc), "Created." if org.secret_key_enc else "Missing.")

    invites = db.scalars(select(Invite).where(Invite.org_id == org.id, Invite.revoked_at.is_(None))).all()
    set_("invite_sent", bool(invites), f"{len(invites)} invitation(s) sent." if invites else "No invitation yet.")
    admins = db.scalars(select(User).where(User.org_id == org.id, User.role.in_(["ceo", "ciso"]),
                                           User.active.is_(True))).all()
    ready = [u for u in admins if u.terms_accepted_at and (u.mfa_enrolled_at or not org.mfa_required)]
    set_("admin_ready", bool(ready), f"{ready[0].name} is ready." if ready else
         ("Admin exists but has not finished two-step sign-in or the terms." if admins else "No admin has joined."))

    cons = db.scalars(select(Connector).where(Connector.org_id == org.id)).all()
    ident = [x for x in cons if x.category == "Identity" and x.status == "reading"]
    ident_checked = [x for x in cons if x.category == "Identity" and x.status in ("checked", "verified")]
    set_("identity_connector", bool(ident), f"{ident[0].name} is reading." if ident else (
        f"{ident_checked[0].name}: credentials checked, not reading yet." if ident_checked else
        "No identity connector is reading yet."))
    cloud = [x for x in cons if x.category == "Cloud logs" and x.status in ("reading", "verified")]
    fresh = [x for x in cloud if x.last_sync_at and (now - x.last_sync_at).total_seconds() < 3600]
    cloud_checked = [x for x in cons if x.category == "Cloud logs" and x.status == "checked"]
    if fresh:
        cloud_detail = (f"{fresh[0].name} synced "
                        f"{int((now - fresh[0].last_sync_at).total_seconds() // 60)} min ago.")
    elif cloud:
        cloud_detail = "No read in the last hour."
    elif cloud_checked:
        cloud_detail = f"{cloud_checked[0].name}: credentials checked, not reading yet."
    else:
        cloud_detail = "No cloud log source yet."
    set_("cloud_logs", bool(fresh), cloud_detail)
    cols = db.scalars(select(Collector).where(Collector.org_id == org.id)).all()
    online = [x for x in cols if deploy.health(x) == "online"]
    set_("collector_reporting", bool(online or fresh),
         f"{len(online)} collector(s) online" + (f", {len(fresh)} cloud source(s) syncing." if fresh else "."))
    agents = {a.key: a for a in db.scalars(select(Agent).where(Agent.org_id == org.id))}
    missing = [k for k in org.contracted_agents if k not in agents or agents[k].status == "dark"]
    set_("whitehats", not missing, "All contracted WhiteHats are on." if not missing else
         "Not on yet: " + ", ".join(missing))
    providers = set(pol.get("providers", []))
    deps = db.scalars(select(ModelDeployment).where(ModelDeployment.org_id == org.id,
                                                    ModelDeployment.enabled.is_(True))).all()
    usable = [d for d in deps if d.provider != "rules" and d.status == "ready" and d.provider in providers
              and (d.zero_retention or not pol.get("zero_retention"))]
    rules_only = providers == {"rules"}
    set_("ai_model", bool(usable) or rules_only,
         f"{usable[0].name} tested and allowed by policy." if usable else
         "Rules-only mode agreed." if rules_only else "No allowed model has passed its test.")
    al = org.alerts or {}
    set_("alerts", bool(al.get("verified_at")), f"Test delivered to {al.get('kind')} on {al['verified_at'][:16]}."
         if al.get("verified_at") else "No test message delivered yet.")
    if include_slow and org.repos:
        results = [_repo_reachable(r["url"]) for r in org.repos]
        set_("repos", all(ok for ok, _ in results), " ".join(d for _, d in results))
    elif not org.repos:
        set_("repos", False, "No repositories linked.")
    lim = PLAN_LIMITS.get(org.plan, PLAN_LIMITS["growth"])
    u = usage(db, org)
    set_("plan_limits", u["seats"] <= lim["seats"] and u["collectors"] <= lim["collectors"],
         f"{u['seats']}/{lim['seats']} seats, {u['collectors']}/{lim['collectors']} collectors.")
    set_("signoff", bool(org.go_live_operator and org.go_live_customer),
         f"Operator: {org.go_live_operator or 'pending'}. Customer: {org.go_live_customer or 'pending'}.", "pending")
    db.flush()
    return rows


def readiness(db: Session, org: Organization, include_slow: bool = False) -> dict:
    rows = evaluate(db, org, include_slow)
    out, blocking = [], []
    for c in CHECKS:
        r = rows[c.key]
        req = is_required(c, org)
        item = {"key": c.key, "stage": c.stage, "title": c.title, "why": c.why, "kind": c.kind, "owner": c.owner,
                "doc": c.doc, "required": req, "status": r.status, "detail": r.detail,
                "checked_at": r.checked_at.isoformat() if r.checked_at else None, "checked_by": r.checked_by}
        out.append(item)
        if req and r.status not in ("passed", "waived") and c.key != "signoff":
            blocking.append(c.title)
    done = sum(1 for i in out if i["status"] in ("passed", "waived"))
    return {"checks": out, "stages": [{"key": k, "title": t} for k, t in STAGES], "passed": done,
            "total": len(out), "blocking": blocking, "ready_for_signoff": not blocking}


def mark_milestone(db: Session, org_id: int, key: str) -> None:
    org = db.get(Organization, org_id)
    if org is None or (org.milestones or {}).get(key):
        return
    org.milestones = {**(org.milestones or {}), key: utcnow().isoformat()}


def first_value(org: Organization) -> list[dict]:
    start = org.created_at
    today = utcnow()
    out = []
    for key, title, day in MILESTONES:
        at = (org.milestones or {}).get(key)
        due = start + timedelta(days=day)
        if at:
            state = "done"
        elif today > due + timedelta(days=2):
            state = "red"
        elif today > due:
            state = "amber"
        else:
            state = "on_track"
        out.append({"key": key, "title": title, "target_day": day, "due": due.isoformat(), "done_at": at,
                    "state": state, "origin": org.milestones_origin or "live"})
    return out


def health(db: Session, org: Organization) -> dict:
    day = utcnow() - timedelta(days=1)
    cols = db.scalars(select(Collector).where(Collector.org_id == org.id)).all()
    calls = db.scalars(select(LLMCall).where(LLMCall.org_id == org.id, LLMCall.at >= day)).all()
    last_login = db.scalar(select(func.max(User.last_login)).where(User.org_id == org.id))
    crit = db.scalar(select(func.count(Incident.id)).where(Incident.org_id == org.id, Incident.status.notin_(INCIDENT_CLOSED),
                                                            Incident.severity == "critical")) or 0
    fv = first_value(org)
    state = "red" if any(m["state"] == "red" for m in fv) else "amber" if any(m["state"] == "amber" for m in fv) \
        else "good"
    return {
        "collectors_online": sum(1 for c in cols if deploy.health(c) == "online"), "collectors_total": len(cols),
        "collectors_silent": sum(1 for c in cols if deploy.health(c) in ("late", "offline", "crashed")),
        "ai_calls_24h": len(calls), "ai_failures_24h": sum(1 for c in calls if not c.ok),
        "ai_fallbacks_24h": sum(1 for c in calls if c.fallback),
        "last_login": last_login.isoformat() if last_login else None, "open_critical_incidents": crit,
        "first_value": state, "usage": usage(db, org), "limits": PLAN_LIMITS.get(org.plan, PLAN_LIMITS["growth"]),
    }


# ------------------------------------------------------------------ retention and offboarding

def enforce_retention(db: Session) -> dict[str, int]:
    removed = {}
    now = utcnow()
    for org in db.scalars(select(Organization)):
        logs_before = now - timedelta(days=org.retention_log_days)
        runs_before = now - timedelta(days=org.retention_run_days)
        audit_before = now - timedelta(days=org.retention_audit_days)
        n = 0
        n += db.execute(delete(LogEvent).where(LogEvent.org_id == org.id, LogEvent.ts < logs_before)).rowcount
        tpl_ids = select(LogTemplate.id).where(LogTemplate.org_id == org.id)
        n += db.execute(delete(TemplateCount).where(TemplateCount.template_id.in_(tpl_ids),
                                                    TemplateCount.minute < logs_before)).rowcount
        n += db.execute(delete(LogDigest).where(LogDigest.org_id == org.id, LogDigest.created_at < runs_before)).rowcount
        n += db.execute(delete(LLMCall).where(LLMCall.org_id == org.id, LLMCall.at < runs_before)).rowcount
        n += db.execute(delete(ChatMessage).where(ChatMessage.org_id == org.id, ChatMessage.at < runs_before)).rowcount
        n += db.execute(delete(AuditLog).where(AuditLog.org_id == org.id, AuditLog.at < audit_before)).rowcount
        removed[org.slug or str(org.id)] = n
    db.commit()
    return removed


def _rows(db: Session, model, query) -> list[dict]:
    out = []
    for r in db.scalars(query):
        d = {}
        for col in model.__table__.columns:
            if col.name.endswith("_enc") or col.name in ("token_hash", "password_hash"):
                continue
            v = getattr(r, col.name)
            d[col.name] = v.isoformat() if hasattr(v, "isoformat") else v
        out.append(d)
    return out


EXPORT_MODELS = (User, Project, Agent, Incident, Signal, Remediation, PostureSnapshot, Collector, LogDigest,
                 NetworkFinding, ChatMessage, ModelDeployment, Connector, AuditLog,
                 Clone, CloneVersion, Node, Edge, Group, AccessPath, CredentialRef, ChangeSet, SensePoint,
                 ProjectConnection, VaultSecret, ChangeEvent, ScanPlanRecord, VexStatement, RegistryPackage, RegistryToken,
                 Audit, SphereDecision, SphereVersion, SphereGraphState, FindingDecision, SettingsCheck)
# Raw telemetry, metering and setup records stay out: they are large, short-lived, or not the customer's own data.
EXPORT_LEFT_OUT = ("log_events", "log_templates", "template_counts", "network_flows", "process_samples",
                   "llm_calls", "invites", "tenant_checks")


def export(db: Session, org: Organization) -> tuple[str, str]:
    tables = {m.__tablename__: _rows(db, m, select(m).where(m.org_id == org.id)) for m in EXPORT_MODELS}
    tables["incident_events"] = _rows(db, IncidentEvent, select(IncidentEvent).join(Incident).where(
        Incident.org_id == org.id))
    tables["project_members"] = _rows(db, ProjectMember, select(ProjectMember).join(Project).where(
        Project.org_id == org.id))
    data = {"company": org.name, "slug": org.slug, "exported_at": utcnow().isoformat(), "tables": tables}
    raw = json.dumps(data, indent=2, default=str)
    path = settings.export_dir / f"{org.slug or org.id}-{date.today().isoformat()}.json"
    path.write_text(raw)
    return str(path), hashlib.sha256(raw.encode()).hexdigest()


def delete_tenant_data(db: Session, org: Organization) -> dict[str, int]:
    counts = {}
    for c in db.scalars(select(Collector).where(Collector.org_id == org.id)):
        if c.mode == "local":
            deploy.stop_local(c)
    inc_ids = select(Incident.id).where(Incident.org_id == org.id)
    counts["incident_events"] = db.execute(delete(IncidentEvent).where(IncidentEvent.incident_id.in_(inc_ids))).rowcount
    tpl_ids = select(LogTemplate.id).where(LogTemplate.org_id == org.id)
    counts["template_counts"] = db.execute(delete(TemplateCount).where(TemplateCount.template_id.in_(tpl_ids))).rowcount
    project_ids = select(Project.id).where(Project.org_id == org.id)
    counts["project_members"] = db.execute(
        delete(ProjectMember).where(ProjectMember.project_id.in_(project_ids))).rowcount
    # The platform keeps its own audit of this company's requests. It keeps the actor's name, not the person.
    people = select(User.id).where(User.org_id == org.id)
    db.execute(update(AuditLog).where(AuditLog.org_id != org.id, AuditLog.user_id.in_(people)).values(user_id=None))
    for model in (LogEvent, NetworkFlow, ProcessSample, Signal, Remediation, LogDigest, LogTemplate, NetworkFinding,
                  PostureSnapshot, ChatMessage, LLMCall, SensePoint, Node, Edge, Group, AccessPath, CredentialRef,
                  ChangeSet, CloneVersion, Clone, Incident, Collector, ProjectConnection, ModelDeployment, ChangeEvent,
                  ScanPlanRecord, VexStatement, RegistryPackage, RegistryToken, Audit,
                  SphereDecision, SphereVersion, SphereGraphState, FindingDecision, SettingsCheck, Connector, Agent, Invite, TenantCheck, AuditLog, VaultSecret, User):
        counts[model.__tablename__] = db.execute(delete(model).where(model.org_id == org.id)).rowcount
    counts["projects"] = db.execute(delete(Project).where(Project.org_id == org.id)).rowcount
    shutil.rmtree(DATA_DIR / "registry" / str(org.id), ignore_errors=True)  # the company's fixed library files (REG.1)
    return counts

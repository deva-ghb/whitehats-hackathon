"""AI model deployments, connectors, the board report, users, audit trail, plan and usage."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, object_session

from ..connectors import PROTOTYPE_CONNECTORS, routing
from ..db import get_db, utcnow
from ..models import (
    AuditLog,
    Collector,
    Connector,
    Incident,
    Invite,
    Audit,
    LLMCall,
    ModelDeployment,
    NetworkFinding,
    Organization,
    Project,
    Remediation,
    SettingsCheck,
    Signal,
    User,
)
from ..permissions import allow, can
from ..projects import MAIN_SLUG, change_legacy_role
from ..security import (
    ROLES,
    Principal,
    audit,
    mask,
    seal,
    unseal,
)
from ..services import (
    auto_map,
    briefing,
    cloudlogs,
    connection_nodes,
    connector_status,
    factory,
    invites,
    posture,
    project_desk,
)
from ..connectors.base import SourceError as _SourceError
from ..connectors.gcp import Gcp
from ..services import audit_jobs, connector_probes, gcp_checks, local_workspace


def gcp_test(c: Connector) -> tuple[str, str]:
    """Sign in as the customer's reader and try each read the connection needs."""
    try:
        result = Gcp(c.config or {}).test()
    except _SourceError as exc:
        return "error", str(exc)
    if not result.is_ok:
        return "error", result.first_problem()
    return "checked", f"Signed in as {result.identity}. Resources, permissions and the activity log are readable."
from ..services.llm import PROVIDERS, PURPOSES, complete, pack_context, test_deployment
from ..services.tenancy import mark_milestone

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ AI models


def dep_out(d: ModelDeployment, usage: dict | None = None, db: Session | None = None) -> dict:
    return {"id": d.id, "name": d.name, "provider": d.provider, "provider_label": PROVIDERS.get(d.provider, d.provider),
            "endpoint": d.endpoint, "deployment": d.deployment, "api_version": d.api_version,
            "has_key": bool(d.secret_enc), "key_hint": mask(unseal(d.secret_enc, db, d.org_id)), "roles": d.roles,
            "zero_retention": d.zero_retention,
            "context_window": d.context_window, "max_output": d.max_output, "priority": d.priority,
            "enabled": d.enabled, "status": d.status, "last_test_at": d.last_test_at.isoformat() if d.last_test_at else None,
            "last_latency_ms": d.last_latency_ms, "last_error": d.last_error, "notes": d.notes,
            "usage_30d": usage or {"calls": 0, "tokens": 0, "failures": 0}}


@router.get("/models")
def models(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=30)
    usage = {}
    for dep_id, calls, p, c, ok in db.execute(
        select(LLMCall.deployment_id, func.count(LLMCall.id), func.sum(LLMCall.prompt_tokens),
               func.sum(LLMCall.completion_tokens), func.sum(case((LLMCall.ok.is_(True), 1), else_=0)))
        .where(LLMCall.org_id == user.org_id, LLMCall.at >= since).group_by(LLMCall.deployment_id)
    ).all():
        usage[dep_id] = {"calls": calls, "tokens": int((p or 0) + (c or 0)), "failures": calls - int(ok or 0)}
    deps = db.scalars(select(ModelDeployment).where(ModelDeployment.org_id == user.org_id)
                      .order_by(ModelDeployment.priority, ModelDeployment.id)).all()
    fallbacks = db.scalar(select(func.count(LLMCall.id)).where(LLMCall.org_id == user.org_id,
                                                               LLMCall.fallback.is_(True), LLMCall.at >= since)) or 0
    recent = db.scalars(select(LLMCall).where(LLMCall.org_id == user.org_id).order_by(LLMCall.id.desc()).limit(25)).all()
    audit_cost = db.scalar(select(func.sum(Audit.cost_usd)).where(
        Audit.org_id == user.org_id, Audit.created_at >= since)) or 0
    return {"audit_cost_30d": audit_cost,
            "audit_limit_usd": audit_jobs.BUDGET_USD["quick"], "review_limit_usd": audit_jobs.BUDGET_USD[audit_jobs.CHANGE],
            "deployments": [dep_out(d, usage.get(d.id), db) for d in deps], "providers": PROVIDERS, "purposes": PURPOSES,
            "fallback_calls_30d": fallbacks,
            "recent_calls": [{"at": c.at.isoformat(), "model": c.model_label, "purpose": c.purpose, "ok": c.ok,
                              "fallback": c.fallback, "tokens": c.prompt_tokens + c.completion_tokens,
                              "latency_ms": c.latency_ms, "error": c.error[:200]} for c in recent]}


class DeploymentIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    provider: Literal["vertex", "azure_openai", "openai_compatible", "claude_cli", "rules"]
    endpoint: str = ""
    deployment: str = ""
    api_version: str = "v1"
    api_key: str | None = None  # write-only
    roles: list[str] = []
    context_window: int = Field(default=128000, ge=2000, le=2_000_000)
    max_output: int = Field(default=1200, ge=100, le=64000)
    priority: int = 10
    enabled: bool = True
    zero_retention: bool = False
    notes: str = ""


@router.post("/models")
def create_model(body: DeploymentIn, user: Principal = Depends(allow("manage_ai")), db: Session = Depends(get_db)):
    data = body.model_dump(exclude={"api_key"})
    d = ModelDeployment(org_id=user.org_id, **data, secret_enc=seal(body.api_key or "", db, user.org_id))
    db.add(d)
    audit(db, user, "added AI model", body.name, body.provider)
    db.commit()
    return dep_out(d, None, db)


@router.put("/models/{mid}")
def update_model(mid: int, body: DeploymentIn, user: Principal = Depends(allow("manage_ai")), db: Session = Depends(get_db)):
    d = db.get(ModelDeployment, mid)
    if d is None or d.org_id != user.org_id:
        raise HTTPException(404, "Model not found.")
    for k, v in body.model_dump(exclude={"api_key"}).items():
        setattr(d, k, v)
    if body.api_key:  # blank keeps the stored key
        d.secret_enc, d.status = seal(body.api_key, db, user.org_id), "untested"
    audit(db, user, "updated AI model", d.name, "key changed" if body.api_key else "")
    db.commit()
    return dep_out(d, None, db)


@router.post("/models/{mid}/test")
def test_model(mid: int, user: Principal = Depends(allow("manage_ai")), db: Session = Depends(get_db)):
    d = db.get(ModelDeployment, mid)
    if d is None or d.org_id != user.org_id:
        raise HTTPException(404, "Model not found.")
    return test_deployment(db, d)


@router.delete("/models/{mid}")
def delete_model(mid: int, user: Principal = Depends(allow("manage_ai")), db: Session = Depends(get_db)):
    d = db.get(ModelDeployment, mid)
    if d is None or d.org_id != user.org_id:
        raise HTTPException(404, "Model not found.")
    db.query(LLMCall).filter(LLMCall.deployment_id == d.id).update({LLMCall.deployment_id: None})
    db.delete(d)
    audit(db, user, "removed AI model", d.name)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ connectors

# (key, label, secret, required)
CONNECTOR_FIELDS = {
    "aws": [("role_arn", "Read-only role ARN (from the WhiteHats CloudFormation template)", False, True),
            ("external_id", "External ID", False, True), ("region", "Home region", False, True),
            ("regions", "Other regions to read, comma separated", False, False),
            ("tag_key", "Only resources with this tag key", False, False), ("tag_value", "and this value", False, False),
            ("log_groups", "CloudWatch log groups, comma separated", False, False)],
    "okta": [("domain", "Okta domain, e.g. acme.okta.com", False, True), ("token", "API token (read-only admin)", True, True)],
    "entra": [("tenant_id", "Directory (tenant) ID", False, True), ("client_id", "Application (client) ID", False, True),
              ("token", "Client secret", True, True)],
    "google": [("domain", "Workspace domain", False, True),
               ("admin_email", "An administrator's email (the key reads the directory as them, read-only)", False, True),
               ("token", "Service account key (JSON)", True, True)],
    "cloudwatch": [("region", "AWS region, e.g. ap-south-1", False, True),
                   ("log_groups", "Log groups, comma separated", False, True),
                   ("role_arn", "Read-only role ARN", False, True),
                   ("external_id", "External ID for the role", False, True)],
    "azure_monitor": [("tenant_id", "Directory (tenant) ID", False, True), ("client_id", "Application (client) ID", False, True),
                      ("workspace_id", "Log Analytics workspace ID", False, True),
                      ("query", "Tables to read (KQL), default: union AzureActivity, SigninLogs", False, False),
                      ("token", "Client secret", True, True)],
    "gcp": [("project_id", "Project ID", False, True),
            ("service_account", "Reader email (from the WhiteHats setup script)", False, True)],
    "gcp_logging": [("project_id", "Project ID", False, False),
                    ("filter", 'Log filter, default: logName:"cloudaudit.googleapis.com"', False, False),
                    ("token", "Service account key (JSON)", True, True)],
    "github": [],  # connected by installing the WhiteHats GitHub App (POST /api/github/install), never a token
    "jira": [("site", "Site URL", False, True), ("project", "Project key", False, True), ("token", "API token", True, True)],
    "bitbucket": [("workspace", "Workspace slug", False, True),
                  ("token", "Repository-read workspace access token", True, True)],
    "crowdstrike": [("region", "Cloud region", False, True), ("client_id", "Client ID", False, True),
                    ("token", "Client secret", True, True)],
}
DOCS = {"aws": "connectors/aws", "okta": "connectors/okta", "entra": "connectors/entra-id", "google": "connectors/google-workspace",
        "cloudwatch": "connectors/aws-cloudwatch", "azure_monitor": "connectors/azure-monitor",
        "gcp_logging": "connectors/gcp-logging", "gcp": "connectors/gcp", "github": "connectors/github", "jira": "connectors/jira",
        "crowdstrike": "connectors/crowdstrike"}
CONFIGURABLE = PROTOTYPE_CONNECTORS | connector_probes.KEYS | {"gcp_logging", "gcp"}
# Read for real even in the local sample workspace: the hackathon demo connects a real Google Cloud project.
ALWAYS_REAL = frozenset({"gcp"})
COMING_SOON = "is not available for connection yet."


def honest_status(c: Connector) -> str:
    """What the screen may say. A credential check is not the same as reading."""
    if c.key not in CONFIGURABLE:
        return "coming_soon"
    if c.status in ("unbuilt", "coming_soon"):
        return "available"
    if c.status == "reading" or (c.status == "verified" and c.last_sync_at):
        return "reading"
    if c.status in ("checked", "configured") or c.status == "verified":
        return "checked"
    return c.status


def _settings_check(c: Connector) -> dict:
    """The AWS settings check's last outcome (CS.1), kept apart from the connection's own state."""
    session = object_session(c)
    check = session.get(SettingsCheck, c.id) if session is not None and c.key == "aws" else None
    return {"settings": check.status} if check is not None else {}


def con_out(c: Connector) -> dict:
    return {"id": c.id, "key": c.key, "name": c.name, "category": c.category, "description": c.description,
            "status": honest_status(c), "config": {k: v for k, v in c.config.items()}, "has_secret": bool(c.secret_enc),
            "last_check": c.last_check, "doc": DOCS.get(c.key),
            "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            "sync": {**{k: v for k, v in (c.state or {}).items() if k != "cursors" and k not in connector_status.OWN_STATE
                        and k not in ("settings", "settings_pending")},
                     **_settings_check(c)},
            "contract": connector_status.contract(c, utcnow()) if c.key in PROTOTYPE_CONNECTORS else None,
            "connected_at": c.connected_at.isoformat() if c.connected_at else None,
            "local_access": local_workspace.ENABLED,
            "fields": [{"key": k, "label": label, "secret": sec, "required": req}
                       for k, label, sec, req in CONNECTOR_FIELDS.get(c.key, [])]}


@router.get("/connectors")
def connectors(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    return [con_out(c) for c in db.scalars(select(Connector).where(Connector.org_id == user.org_id)
                                           .order_by(Connector.category, Connector.name))]


class ConnectorIn(BaseModel):
    values: dict[str, str]


def _verify(db: Session, c: Connector) -> tuple[str, str]:
    """Real, read-only checks for every connector that offers a cheap call."""
    if c.key == "gcp":
        return gcp_test(c)
    if local_workspace.ENABLED:
        return "checked", "Provider read access verified."
    if c.key in connector_probes.KEYS:
        return connector_probes.check(db, c)
    if c.key == "gcp_logging":
        try:
            status, detail = cloudlogs.test_source(db, c)
        except (ValueError, KeyError, TypeError):
            return "error", "Provide a valid Google service account JSON key."
        return (status, detail) if status == "checked" else (status, "Google Cloud denied log access or could not be reached.")
    if c.category == "Cloud logs":
        return cloudlogs.test_source(db, c)
    if c.key in ("okta", "entra", "google"):
        return cloudlogs.verify_identity(db, c)
    if c.key == "github":
        return cloudlogs.test_source(db, c)
    return "checked", "Credentials checked. Nothing is read from this service yet."


@router.put("/connectors/{cid}")
def configure(cid: int, body: ConnectorIn, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if c is None or c.org_id != user.org_id:
        raise HTTPException(404, "Connector not found.")
    if c.key not in CONFIGURABLE:
        raise HTTPException(409, f"{c.name} {COMING_SOON}")
    if c.key in ("aws", "cloudwatch") and (body.values.get("access_key_id") or body.values.get("token")):
        raise HTTPException(400, "Long-lived access keys are not accepted. Give a read-only role ARN.")
    if c.key == "github" and not local_workspace.ENABLED:
        raise HTTPException(400, "GitHub connects by installing the WhiteHats GitHub App. Personal tokens are not accepted.")
    fields = CONNECTOR_FIELDS.get(c.key, [])
    missing = [label for k, label, sec, req in fields if req and not body.values.get(k) and not (sec and c.secret_enc)]
    if missing and (not local_workspace.ENABLED or c.key in ALWAYS_REAL):
        raise HTTPException(400, "Please fill in: " + ", ".join(missing))
    if c.key == "cloudwatch" and not body.values.get("role_arn") and not local_workspace.ENABLED:
        raise HTTPException(400, "Give a read-only role ARN.")
    keep = {k: v for k, v in (c.config or {}).items() if k == "cursors"}
    c.config = {**keep, **{k: body.values[k].strip() for k, _, sec, _ in fields if not sec and body.values.get(k)}}
    secret = next((body.values.get(k) for k, _, sec, _ in fields if sec and body.values.get(k)), None)
    if secret and not local_workspace.ENABLED:
        c.secret_enc = seal(secret, db, user.org_id)
    c.status, c.last_check = _verify(db, c)
    c.connected_at = None if c.status == "error" else utcnow()
    if c.key == "gcp" and c.status == "checked" and user.project is not None:
        routing.attach(db, c, user.project, {}, user.email)  # the project it was connected from sees its findings
    if c.key in PROTOTYPE_CONNECTORS and c.status in cloudlogs.READABLE:
        auto_map.mark_due(c, f"{c.name} was connected")  # its projects' maps fill within a minute (STUDIO.6)
    audit(db, user, "configured connector", c.name, c.status)
    db.commit()
    return con_out(c)


@router.post("/connectors/{cid}/sync")
def sync_now(cid: int, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if c is None or c.org_id != user.org_id:
        raise HTTPException(404, "Connector not found.")
    if c.key not in CONFIGURABLE:
        raise HTTPException(409, f"{c.name} {COMING_SOON}")
    if c.key in connector_probes.KEYS or not cloudlogs.can_read(c):
        raise HTTPException(409, "Only a cloud log source whose credentials checked can be read.")
    if c.key == "gcp":
        try:
            result = gcp_checks.sync(db, c)
        except cloudlogs.SourceError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"read": result["activity"], "result": result, "connector": con_out(c)}
    if local_workspace.ENABLED:
        c.status, c.last_sync_at = "reading", utcnow()
        db.commit()
        return {"read": 0, "connector": con_out(c)}
    try:
        n = cloudlogs.sync(db, c)
    except cloudlogs.SourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"read": n, "connector": con_out(c)}


@router.post("/connectors/{cid}/check")
def check_again(cid: int, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    """Run the connection check again with what is stored, so a changed permission shows without re-entering it."""
    c = _own_connector(db, user, cid)
    if c.key not in CONFIGURABLE:
        raise HTTPException(409, f"{c.name} {COMING_SOON}")
    if c.status in ("available", "coming_soon"):
        raise HTTPException(409, f"Connect {c.name} first.")
    c.status, c.last_check = _verify(db, c)
    audit(db, user, "checked connector", c.name, c.status)
    db.commit()
    return con_out(c)


@router.post("/connectors/{cid}/discover")
def discover_now(cid: int, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    """Check the connection and list what it can see. The projects it is attached to see their share on the canvas."""
    c = _own_connector(db, user, cid)
    if c.key not in PROTOTYPE_CONNECTORS:
        raise HTTPException(409, f"{c.name} {COMING_SOON}")
    if c.status in ("available", "coming_soon"):
        raise HTTPException(409, f"Connect {c.name} first.")
    if local_workspace.ENABLED:
        return {"found": 0, "connector": con_out(c)}
    try:
        found = connector_status.discover(db, c)
    except connector_status.SourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit(db, user, "discovered with connector", c.name, f"{found} nodes")
    db.commit()
    return {"found": found, "connector": con_out(c)}


@router.get("/discoveries")
def discoveries(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    """What the connections attached to this project found, as a read-only canvas with marks."""
    return connector_status.project_view(db, user.org_id, user.project.id)


@router.delete("/connectors/{cid}")
def disconnect(cid: int, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if c is None or c.org_id != user.org_id:
        raise HTTPException(404, "Connector not found.")
    # Allowed for a coming-soon connector too: it only clears credentials saved before.
    connection_nodes.forget(db, c)  # STUDIO.8: what it brought in leaves the maps with it
    c.status, c.config, c.secret_enc, c.last_check, c.connected_at = "available", {}, "", "", None
    c.state, c.last_sync_at = {}, None
    audit(db, user, "disconnected connector", c.name)
    db.commit()
    return con_out(c)


# ------------------------------------------------------------------ board report



class ScopeIn(BaseModel):
    scope: dict = Field(default_factory=dict)


def _own_connector(db: Session, user: Principal, cid: int) -> Connector:
    c = db.get(Connector, cid)
    if c is None or c.org_id != user.org_id:
        raise HTTPException(404, "Connector not found.")
    return c


@router.get("/connectors/{cid}/projects")
def connector_projects(cid: int, user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    """Which projects this connection feeds, and with what scope."""
    c = _own_connector(db, user, cid)
    names = dict(db.execute(select(Project.id, Project.slug).where(Project.org_id == user.org_id)).all())
    return {"account_wide": routing.is_account_wide(c),
            "projects": [{"project": names[pid], "scope": scope} for pid, scope in routing.attachments(db, c)]}


@router.put("/connectors/{cid}/scope")
def attach_connector(cid: int, body: ScopeIn, user: Principal = Depends(allow("attach_connections")),
                     db: Session = Depends(get_db)):
    """Attach this connection to the project named by ?project=, or change its scope there."""
    c = _own_connector(db, user, cid)
    try:
        row = routing.attach(db, c, user.project, body.scope, user.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    auto_map.mark_due(c, f"{c.name} was attached to {user.project.name}")
    audit(db, user, "attached connection", c.name, f"to {user.project.name}: {json.dumps(row.scope)}")
    db.commit()
    return {"project": user.project.slug, "scope": row.scope}


@router.delete("/connectors/{cid}/scope")
def detach_connector(cid: int, user: Principal = Depends(allow("attach_connections")), db: Session = Depends(get_db)):
    """New data from this connection stops reaching the project, and what it drew leaves the project's map. Its
    findings and incidents stay, as history."""
    c = _own_connector(db, user, cid)
    if not routing.detach(db, c, user.project):
        raise HTTPException(404, f"{c.name} is not attached to {user.project.name}.")
    connection_nodes.forget(db, c, [user.project.id])
    audit(db, user, "detached connection", c.name, f"from {user.project.name}")
    db.commit()
    return {"ok": True}


@router.get("/reports/board")
def board_report(days: int = 30, account: bool = False, user: Principal = Depends(allow("see_project")),
                 db: Session = Depends(get_db)):
    """One project's report, or the account's over the projects this person can open, and nothing else."""
    org = db.get(Organization, user.org_id)
    since = utcnow() - timedelta(days=days)
    visible = project_desk.visible_projects(db, user) if account else [user.project]
    ids = [project.id for project in visible]
    rolled = posture.roll_up(db, user.org_id, visible) if account else None
    main = next((project for project in visible if project.slug == MAIN_SLUG), None)
    if rolled is not None:
        p = {
            "score": rolled["account"]["score"], "grade": rolled["account"]["grade"],
            "blind_spots": posture.whitehat_watch(db, user.org_id, project_ids=ids)["blind_spots"],
            "breakdown": [
                {"area": row["name"], "penalty": 1000 - row["score"],
                 "detail": f"{row['criticality'].title()} criticality, score {row['score']}"}
                for row in rolled["projects"]
            ],
        }
        # There is no account history yet. Main's is shown, and only to someone who can open Main.
        hist = posture.history(db, user.org_id, days, project_id=main.id) if main else []
        started = posture.history_started(db, user.org_id, project_id=main.id) if main else None
    else:
        pid = user.project.id
        p = posture.compute(db, user.org_id, project_id=pid)
        hist = posture.history(db, user.org_id, days, project_id=pid)
        started = posture.history_started(db, user.org_id, project_id=pid)

    def scoped(model):
        return [model.org_id == user.org_id, model.project_id.in_(ids)]

    incs = db.scalars(select(Incident).where(*scoped(Incident), Incident.created_at >= since)).all()
    rems = db.scalars(select(Remediation).where(*scoped(Remediation), Remediation.created_at >= since)).all()
    cols = db.scalars(select(Collector).where(Collector.org_id == user.org_id)).all()
    signals = db.scalar(select(func.count(Signal.id)).where(*scoped(Signal), Signal.created_at >= since,
                                                            Signal.learning.is_(False))) or 0
    nf = db.scalars(select(NetworkFinding).where(*scoped(NetworkFinding))).all()
    # Fix Factory works on Main's code, so its numbers belong only to a report that covers Main.
    fstat = factory.status(org) if main is not None else {}
    facts = {
        "posture": p, "history": hist, "history_started": started,
        "incidents": {"total": len(incs), "resolved": sum(1 for i in incs if i.status == "resolved"),
                      "critical": sum(1 for i in incs if i.severity == "critical")},
        "fixes": {"proposed": len(rems), "approved": sum(1 for r in rems if r.status in ("dispatched", "done")),
                  "rejected": sum(1 for r in rems if r.status == "rejected"),
                  "pending": sum(1 for r in rems if r.status == "pending")},
        "signals": signals,
        "coverage": {"collectors": len(cols), "cloud_sources": sum(1 for c in cols if c.mode == "cloud")},
        "network": {"open": sum(1 for f in nf if f.status == "open"), "resolved": sum(1 for f in nf if f.status == "resolved")},
        "factory": fstat.get("summary") if fstat.get("connected") else None,
        "lowest": rolled["lowest"] if rolled else None,
        "findings_once": rolled["findings"]["account"] if rolled else posture.findings_once(db, ids)["account"],
        "projects": rolled["projects"] if rolled else None,
    }
    scope = briefing.Scope(project_ids=tuple(ids), account_data=can(db, user, "read_account_data"))
    packed = pack_context(briefing.sections(db, user.org_id, scope), 3000)
    fallback = (f"Our security posture stands at {p['score']} out of 1000 ({p['grade'].lower()}). Over the last {days} "
                f"days the team handled {len(incs)} incidents and approved {facts['fixes']['approved']} fixes. "
                f"The priority for next month is closing the {facts['network']['open']} open network findings and "
                f"the {facts['fixes']['pending']} fixes still waiting for a decision.")
    mark_milestone(db, user.org_id, "first_report")
    res = complete(db, user.org_id, "executive",
                   "Write the narrative section of a board cyber-risk report. Two short paragraphs, plain English, "
                   "no acronyms, for non-technical directors. First: where we stand and what changed. Second: the top "
                   "risk, what is being done, and any decision the board must make. Use the numbers given.",
                   packed.text, fallback, max_tokens=700)
    return {"org": org.name, "period_days": days, "generated_at": utcnow().isoformat(), "facts": facts,
            "narrative": res.text, "model": res.model, "fallback": res.fallback}


# ------------------------------------------------------------------ users, audit, plan

PLANS = {
    "starter": {"label": "Starter", "price": "$1,500 / month", "seats": 5, "collectors": 10, "ai_tokens": 2_000_000,
                "fixes": 25, "features": ["Posture, incidents and approvals", "Log and network analysis",
                                          "10 collectors", "Rules engine plus 1 AI model"]},
    "growth": {"label": "Growth", "price": "$4,900 / month", "seats": 20, "collectors": 100, "ai_tokens": 20_000_000,
               "fixes": 200, "features": ["Everything in Starter", "Cloud log connectors", "100 collectors",
                                          "Fix Factory with 200 proven fixes", "Any AI model, bring your own key"]},
    "enterprise": {"label": "Enterprise", "price": "Talk to us", "seats": 500, "collectors": 5000,
                   "ai_tokens": 500_000_000, "fixes": 5000,
                   "features": ["Everything in Growth", "Air-gapped deployment with your own model",
                                "Kubernetes collector fleet", "SSO and audit export", "Dedicated success team"]},
}


@router.get("/admin/users")
def users(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.org_id == user.org_id).order_by(User.id)).all()
    roles = project_desk.project_roles(db, user)
    shown = len(project_desk.visible_projects(db, user))
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "account_role": u.account_role,
             "projects": roles.get(u.id, []), "projects_shown": shown, "title": u.title, "active": u.active,
             "last_login": u.last_login.isoformat() if u.last_login else None} for u in rows]


class InviteIn(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    name: str = Field(default="", max_length=120)
    role: Literal["ceo", "ciso", "analyst", "engineer"]


@router.get("/admin/invites")
def list_invites(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Invite).where(Invite.org_id == user.org_id).order_by(Invite.created_at.desc())).all()
    return [invites.out(i) for i in rows]


@router.post("/admin/invites")
def create_invite(body: InviteIn, user: Principal = Depends(allow("manage_users")), db: Session = Depends(get_db)):
    _may_grant(db, user, body.role)
    org = db.get(Organization, user.org_id)
    result = invites.create(db, org, body.email, body.name, body.role, user.name)
    audit(db, user, "invited user", body.email.strip().lower(), body.role)
    db.commit()
    return result


@router.post("/admin/invites/{iid}/revoke")
def revoke_invite(iid: int, user: Principal = Depends(allow("manage_users")), db: Session = Depends(get_db)):
    inv = db.get(Invite, iid)
    if inv is None or inv.org_id != user.org_id or inv.accepted_at:
        raise HTTPException(404, "Open invitation not found.")
    inv.revoked_at = utcnow()
    audit(db, user, "revoked invitation", inv.email)
    db.commit()
    return {"ok": True}


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None


@router.patch("/admin/users/{uid}")
def update_user(uid: int, body: UserPatch, user: Principal = Depends(allow("manage_users")), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if u is None or u.org_id != user.org_id:
        raise HTTPException(404, "User not found.")
    if u.id == user.id and body.active is False:
        raise HTTPException(400, "You cannot deactivate your own account.")
    role_changed = False
    if body.role and body.role != u.role:
        if body.role not in ROLES:
            raise HTTPException(400, "Unknown role.")
        if u.id == user.id:
            raise HTTPException(409, "You cannot change your own role.")
        _may_grant(db, user, body.role)
        was_owner = u.account_role == "account_owner"
        previous = u.role
        change_legacy_role(db, u, body.role)
        if was_owner and u.account_role != "account_owner" and _last_owner(db, u):
            raise HTTPException(409, _LAST_OWNER)
        role_changed = True
        audit(db, user, "changed role", u.name, f"{previous} to {body.role}")
    if body.active is not None and body.active != u.active:
        if body.active is False and u.account_role == "account_owner" and _last_owner(db, u):
            raise HTTPException(409, _LAST_OWNER)
        u.active = body.active
    if not role_changed:
        audit(db, user, "updated user", u.email, body.model_dump_json(exclude_none=True))
    db.commit()
    return {"ok": True}


_LAST_OWNER = "This is the last account owner. Make someone else an account owner first."
# The job title that makes someone an Account owner (projects.apply_legacy_access).
_OWNER_TITLE = "ceo"
_ONLY_OWNERS_MAKE_OWNERS = ("Only an Account owner can make someone an Account owner. An Account admin has no "
                            "billing, so cannot hand it out.")


def _may_grant(db: Session, user: Principal, role: str) -> None:
    """An Account admin may not give out the Account owner role, which carries billing the admin lacks."""
    if role != _OWNER_TITLE:
        return
    granter = db.get(User, user.id)
    if granter is None or granter.account_role != "account_owner":
        raise HTTPException(403, _ONLY_OWNERS_MAKE_OWNERS)


def _last_owner(db: Session, person: User) -> bool:
    """True when nobody else active owns this account. The last owner cannot be removed (spec 0A)."""
    others = db.scalar(select(func.count(User.id)).where(
        User.org_id == person.org_id, User.active.is_(True), User.id != person.id,
        User.account_role == "account_owner")) or 0
    return others < 1


@router.get("/admin/audit")
def audit_log(user: Principal = Depends(allow("read_audit")), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.org_id == user.org_id).order_by(AuditLog.id.desc()).limit(300)).all()
    return _audit_out(rows)



def _audit_out(rows) -> list[dict]:
    return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "target": a.target, "detail": a.detail}
            for a in rows]


@router.get("/projects/audit")
def project_audit(user: Principal = Depends(allow("read_project_audit")), db: Session = Depends(get_db)):
    """One project's audit trail: what was done inside the project named by ?project= (Main by default)."""
    rows = db.scalars(select(AuditLog).where(AuditLog.org_id == user.org_id, AuditLog.project_id == user.project.id)
                      .order_by(AuditLog.id.desc()).limit(300)).all()
    return _audit_out(rows)

@router.get("/admin/plan")
def plan(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    org = db.get(Organization, user.org_id)
    month = utcnow() - timedelta(days=30)
    tokens = db.scalar(select(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens))
                       .where(LLMCall.org_id == org.id, LLMCall.at >= month)) or 0
    fstat = factory.status(org)
    return {
        "current": org.plan, "plans": PLANS,
        "usage": {
            "seats": db.scalar(select(func.count(User.id)).where(User.org_id == org.id, User.active.is_(True))),
            "collectors": db.scalar(select(func.count(Collector.id)).where(Collector.org_id == org.id)),
            "ai_tokens": int(tokens),
            "fixes": (fstat.get("summary") or {}).get("verified", 0) if fstat.get("connected") else 0,
        },
    }


class PlanIn(BaseModel):
    plan: Literal["starter", "growth", "enterprise"]


@router.put("/admin/plan")
def change_plan(body: PlanIn, user: Principal = Depends(allow("manage_billing")), db: Session = Depends(get_db)):
    """The plan is part of the contract, so customers request a change and an operator applies it."""
    org = db.get(Organization, user.org_id)
    if body.plan == org.plan:
        raise HTTPException(400, f"You are already on {PLANS[body.plan]['label']}.")
    platform = db.scalar(select(Organization).where(Organization.is_platform.is_(True)))
    detail = f"from {org.plan} to {body.plan}"
    audit(db, user, "requested plan change", "WhiteHats", detail)
    if platform:
        audit(db, user, "requested plan change", org.name, detail, org_id=platform.id)
    db.commit()
    return {"ok": True, "message": "Request sent. Your WhiteHats contact will confirm the new price and switch it."}

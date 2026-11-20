"""Tenant onboarding.

/api/operator/*   the WhiteHats operator console (role: operator)
/api/setup/*      the customer's own side of onboarding (their admins)
/api/invites/*    public: accepting an invitation
"""

from __future__ import annotations

import re
import secrets
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..models import AuditLog, Invite, Organization, Project, TenantCheck, User
from ..permissions import allow
from ..projects import apply_invited_access, ensure_main_project
from ..security import (
    Principal,
    audit,
    hash_password,
    issue_token,
    mask,
    seal,
    tenant_box,
    token_digest,
    unseal,
)
from ..seed import tenant_defaults
from ..services import invites, notify, tenancy
from ..whitehats import CATALOG
from ..whitehats.builtin import read_zone
from .core import session_response

router = APIRouter(prefix="/api")


def _slug_ok(slug: str) -> bool:
    return 2 <= len(slug) <= 40 and all(ch.islower() or ch.isdigit() or ch == "-" for ch in slug) \
        and not slug.startswith("-") and not slug.endswith("-")


def _operator(user: Principal = Depends(allow("operate_platform"))) -> Principal:
    return user


def _tenant(db: Session, tid: int) -> Organization:
    org = db.get(Organization, tid)
    if org is None or org.is_platform:
        raise HTTPException(404, "Company not found.")
    return org


def _record(db: Session, op: Principal, org: Organization, action: str, detail: str = "") -> None:
    """Operator actions go into both the platform's and the tenant's audit trail."""
    audit(db, op, action, org.name, detail)
    audit(db, op, action, "WhiteHats operator", detail, org_id=org.id)


def org_settings(org: Organization, db: Session) -> dict:
    return {
        "id": org.id, "name": org.name, "slug": org.slug, "plan": org.plan, "status": org.status, "region": org.region,
        "region_label": tenancy.REGIONS.get(org.region, org.region), "domain": org.domain,
        "domain_record": f"whitehats-verify={org.domain_token}" if org.domain_token else "",
        "domain_verified_at": org.domain_verified_at.isoformat() if org.domain_verified_at else None,
        "mfa_required": org.mfa_required, "sso_required": org.sso_required,
        "retention": {"logs": org.retention_log_days, "runs": org.retention_run_days, "audit": org.retention_audit_days},
        "ai_policy": org.ai_policy or {}, "ai_providers_label": tenancy.providers_label(org.ai_policy),
        "contract": {k: v for k, v in (org.contract or {}).items()},
        "contract_origin": org.contract_origin or "live",
        "contracted_agents": org.contracted_agents or [],
        "contracted_names": [CATALOG[k].name if k in CATALOG else k for k in org.contracted_agents or []], "repos": org.repos or [],
        "alerts": {"kind": (org.alerts or {}).get("kind"),
                   "target": mask(unseal((org.alerts or {}).get("target_enc", ""), db, org.id)),
                   "verified_at": (org.alerts or {}).get("verified_at")},
        "support_access_until": org.support_access_until.isoformat() if org.support_access_until else None,
        "support_access_reason": org.support_access_reason,
        "support_project": _project_name(db, org.support_project_id),
        "go_live_operator": org.go_live_operator, "go_live_customer": org.go_live_customer,
        "live_at": org.live_at.isoformat() if org.live_at else None, "suspended_reason": org.suspended_reason,
        "offboarded_at": org.offboarded_at.isoformat() if org.offboarded_at else None,
        "deletion_due": org.deletion_due.isoformat() if org.deletion_due else None,
        "created_at": org.created_at.isoformat(), "created_by": org.created_by,
    }


def _project_name(db: Session, project_id: int | None) -> str | None:
    project = db.get(Project, project_id) if project_id else None
    return project.name if project else None


def _try_go_live(db: Session, org: Organization) -> None:
    if org.go_live_operator and org.go_live_customer and org.status in ("draft", "onboarding"):
        org.status, org.live_at = "live", utcnow()


# ================================================================== operator console


@router.get("/operator/catalog")
def catalog(op: Principal = Depends(_operator)):
    return {"regions": tenancy.REGIONS, "installation_region": settings.region,
            "providers": tenancy.PROVIDER_CHOICES,
            "whitehats": [w.info() for w in CATALOG.values()], "plans": list(tenancy.PLAN_LIMITS),
            "stages": [{"key": k, "title": t} for k, t in tenancy.STAGES],
            "checks": [{"key": c.key, "stage": c.stage, "title": c.title, "kind": c.kind, "owner": c.owner}
                       for c in tenancy.CHECKS]}


@router.get("/operator/tenants")
def tenants(op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    out = []
    for org in db.scalars(select(Organization).where(Organization.is_platform.is_(False))
                          .order_by(Organization.created_at.desc())):
        r = tenancy.readiness(db, org)
        h = tenancy.health(db, org)
        out.append({"id": org.id, "name": org.name, "slug": org.slug, "plan": org.plan, "status": org.status,
                    "region": org.region, "passed": r["passed"], "total": r["total"], "blocking": len(r["blocking"]),
                    "first_value": h["first_value"], "collectors_online": h["collectors_online"],
                    "collectors_total": h["collectors_total"], "ai_failures_24h": h["ai_failures_24h"],
                    "last_login": h["last_login"], "created_at": org.created_at.isoformat(),
                    "live_at": org.live_at.isoformat() if org.live_at else None})
    db.commit()
    counts = {s: sum(1 for t in out if t["status"] == s) for s in ("draft", "onboarding", "live", "suspended", "offboarded")}
    return {"tenants": out, "counts": counts}


class Contract(BaseModel):
    order_ref: str = ""
    signed_at: str = ""
    start_date: str = ""
    owner_name: str = ""
    owner_email: str = ""
    security_contact: str = ""
    pilot: bool = False
    pass_marks: str = ""
    billing_contact: str = ""
    po_number: str = ""
    timezone: str = ""
    work_start: str = ""
    work_end: str = ""


class AiPolicy(BaseModel):
    providers: list[str] = Field(default_factory=lambda: ["azure_openai", "rules"])
    zero_retention: bool = True
    agreed: bool = True


class TenantIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=40)
    plan: Literal["starter", "growth", "enterprise"]
    region: str = "in-south"
    domain: str = ""
    mfa_required: bool = True
    sso_required: bool = False
    retention_log_days: int = Field(default=30, ge=1, le=3650)
    retention_run_days: int = Field(default=365, ge=1, le=3650)
    retention_audit_days: int = Field(default=730, ge=1, le=3650)
    ai_policy: AiPolicy = Field(default_factory=AiPolicy)
    contract: Contract = Field(default_factory=Contract)
    contracted_agents: list[str] = Field(default_factory=list)


def _validate(body: TenantIn) -> None:
    if not _slug_ok(body.slug):
        raise HTTPException(400, "The short name may use lower-case letters, digits and hyphens only.")
    if body.region not in tenancy.REGIONS:
        raise HTTPException(400, "Unknown region.")
    bad = [p for p in body.ai_policy.providers if p not in tenancy.PROVIDER_CHOICES]
    if bad:
        raise HTTPException(400, f"Unknown AI provider: {', '.join(bad)}.")
    unknown = [k for k in body.contracted_agents if k not in CATALOG]
    if unknown:
        raise HTTPException(400, f"Unknown WhiteHat: {', '.join(unknown)}.")
    unavailable = [CATALOG[k].name for k in body.contracted_agents if not CATALOG[k].available]
    if unavailable:
        raise HTTPException(400, f"Not available yet, do not sell: {', '.join(unavailable)}.")
    if body.domain and ("." not in body.domain or " " in body.domain or "/" in body.domain):
        raise HTTPException(400, "Enter the email domain only, for example acme.com.")
    _check_contract_hours(body.contract)


@router.post("/operator/tenants")
def create_tenant(body: TenantIn, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    _validate(body)
    if db.scalar(select(Organization.id).where(Organization.slug == body.slug)):
        raise HTTPException(409, "That short name is taken.")
    org = Organization(name=body.name, slug=body.slug, plan=body.plan, region=body.region, status="draft",
                       domain=body.domain.lower().strip(), domain_token=secrets.token_hex(12),
                       mfa_required=body.mfa_required, sso_required=body.sso_required,
                       retention_log_days=body.retention_log_days, retention_run_days=body.retention_run_days,
                       retention_audit_days=body.retention_audit_days, ai_policy=body.ai_policy.model_dump(),
                       contract=body.contract.model_dump(), contracted_agents=body.contracted_agents, created_by=op.name)
    db.add(org)
    db.flush()
    ensure_main_project(db, org, created_by=op.id)
    tenant_box(db, org.id)  # creates the tenant's own key
    tenant_defaults(db, org)
    _record(db, op, org, "created company", f"{body.plan}, region {body.region}")
    db.commit()
    return {"id": org.id}


@router.get("/operator/tenants/{tid}")
def tenant(tid: int, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    r = tenancy.readiness(db, org)
    rows = db.scalars(select(Invite).where(Invite.org_id == org.id).order_by(Invite.created_at.desc())).all()
    users = db.scalars(select(User).where(User.org_id == org.id).order_by(User.id)).all()
    db.commit()
    return {
        "settings": org_settings(org, db), "readiness": _with_check_origin(db, org, r),
        "first_value": tenancy.first_value(org),
        "health": tenancy.health(db, org),
        "invites": [invites.out(i) for i in rows],
        "users": [{"name": u.name, "email": u.email, "role": u.role, "active": u.active, "mfa": bool(u.mfa_enrolled_at),
                   "last_login": u.last_login.isoformat() if u.last_login else None} for u in users],
    }


class TenantPatch(BaseModel):
    plan: Literal["starter", "growth", "enterprise"] | None = None
    domain: str | None = None
    mfa_required: bool | None = None
    sso_required: bool | None = None
    retention_log_days: int | None = Field(default=None, ge=1, le=3650)
    retention_run_days: int | None = Field(default=None, ge=1, le=3650)
    retention_audit_days: int | None = Field(default=None, ge=1, le=3650)
    ai_policy: AiPolicy | None = None
    contract: Contract | None = None
    contracted_agents: list[str] | None = None


@router.put("/operator/tenants/{tid}")
def update_tenant(tid: int, body: TenantPatch, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    data = body.model_dump(exclude_none=True)
    if "contracted_agents" in data:
        unknown = [k for k in data["contracted_agents"] if k not in CATALOG or not CATALOG[k].available]
        if unknown:
            raise HTTPException(400, f"Not available: {', '.join(unknown)}.")
    if body.contract is not None:
        _check_contract_hours(body.contract)
    if "domain" in data and data["domain"] != org.domain:
        org.domain_verified_at, org.domain_token = None, secrets.token_hex(12)
        data["domain"] = data["domain"].lower().strip()
    for k, v in data.items():
        setattr(org, k, v)
    if "contract" in data:
        org.contract_origin = "live"
    _record(db, op, org, "updated company settings", ", ".join(data))
    db.commit()
    return tenant(tid, op, db)


class CheckAction(BaseModel):
    action: Literal["pass", "fail", "waive", "reset"]
    note: str = Field(default="", max_length=500)


@router.post("/operator/tenants/{tid}/checks/{key}")
def set_check(tid: int, key: str, body: CheckAction, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    c = tenancy.CHECK.get(key)
    if c is None:
        raise HTTPException(404, "Unknown check.")
    if c.kind == "auto" and body.action in ("pass", "fail"):
        raise HTTPException(409, "This check is automatic. Fix the cause and press Re-check, or waive it with a reason.")
    if body.action == "waive" and len(body.note.strip()) < 10:
        raise HTTPException(400, "Waiving a check needs a reason of at least 10 characters.")
    if key == "signoff":
        raise HTTPException(409, "Sign-off happens through the go-live button.")
    rows = tenancy.ensure_checks(db, org)
    row = rows[key]
    row.status = {"pass": "passed", "fail": "failed", "waive": "waived", "reset": "pending"}[body.action]
    row.detail = body.note or row.detail
    row.checked_at, row.checked_by = utcnow(), op.name
    _record(db, op, org, f"{body.action} check", f"{c.title}: {body.note}")
    db.commit()
    return _with_check_origin(db, org, tenancy.readiness(db, org))


@router.post("/operator/tenants/{tid}/recheck")
def recheck(tid: int, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    r = tenancy.readiness(db, org, include_slow=True)
    db.commit()
    return _with_check_origin(db, org, r)


class InviteIn(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    name: str = Field(default="", max_length=120)
    role: Literal["ceo", "ciso"] = "ciso"


@router.post("/operator/tenants/{tid}/invites")
def invite(tid: int, body: InviteIn, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    # The first admin owns the account: spec 0A requires an account owner, and nobody else can invite.
    result = invites.create(db, org, body.email, body.name, body.role, op.name, account_role="account_owner")
    if org.status == "draft":
        org.status = "onboarding"
    _record(db, op, org, "invited first admin", f"{body.email.strip().lower()} as {body.role}")
    db.commit()
    return result


@router.post("/operator/tenants/{tid}/invites/{iid}/revoke")
def revoke_invite(tid: int, iid: int, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    inv = db.get(Invite, iid)
    if inv is None or inv.org_id != org.id or inv.accepted_at:
        raise HTTPException(404, "Open invitation not found.")
    inv.revoked_at = utcnow()
    _record(db, op, org, "revoked invitation", inv.email)
    db.commit()
    return {"ok": True}


@router.post("/operator/tenants/{tid}/go-live")
def operator_go_live(tid: int, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    r = tenancy.readiness(db, org, include_slow=True)
    if not r["ready_for_signoff"]:
        raise HTTPException(409, "Not ready: " + "; ".join(r["blocking"]))
    org.go_live_operator = op.name
    _try_go_live(db, org)
    _record(db, op, org, "signed off go-live", "live" if org.status == "live" else "waiting for the customer")
    db.commit()
    return tenant(tid, op, db)


class Reason(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/operator/tenants/{tid}/suspend")
def suspend(tid: int, body: Reason, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    if org.status == "offboarded":
        raise HTTPException(409, "Already offboarded.")
    org.status, org.suspended_reason = "suspended", body.reason
    _record(db, op, org, "suspended company", body.reason)
    db.commit()
    return {"ok": True}


@router.post("/operator/tenants/{tid}/resume")
def resume(tid: int, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    if org.status != "suspended":
        raise HTTPException(409, "Only a suspended company can be resumed.")
    org.status = "live" if org.live_at else "onboarding"
    org.suspended_reason = ""
    _record(db, op, org, "resumed company")
    db.commit()
    return {"ok": True}


class Confirm(BaseModel):
    confirm_name: str
    retention_days: int = Field(default=30, ge=0, le=365)


@router.post("/operator/tenants/{tid}/offboard")
def offboard(tid: int, body: Confirm, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    if body.confirm_name != org.name:
        raise HTTPException(400, "Type the company name exactly to confirm.")
    path, digest = tenancy.export(db, org)
    org.status, org.offboarded_at = "offboarded", utcnow()
    org.deletion_due = date.today() + timedelta(days=body.retention_days)
    org.contract = {**(org.contract or {}), "export_path": path, "export_sha256": digest}
    for u in db.scalars(select(User).where(User.org_id == org.id)):
        u.active = False
    from ..models import Collector
    from ..services import deploy

    for c in db.scalars(select(Collector).where(Collector.org_id == org.id)):
        if c.mode == "local":
            deploy.stop_local(c)
        c.status = "stopped"
    _record(db, op, org, "offboarded company", f"export {path} sha256 {digest}; deletion due {org.deletion_due}")
    db.commit()
    return {"export_path": path, "sha256": digest, "deletion_due": org.deletion_due.isoformat()}


class DeleteIn(BaseModel):
    confirm_name: str
    early: bool = False


@router.post("/operator/tenants/{tid}/delete")
def delete_tenant(tid: int, body: DeleteIn, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    if org.status != "offboarded":
        raise HTTPException(409, "Offboard the company first; that exports its data.")
    if body.confirm_name != org.name:
        raise HTTPException(400, "Type the company name exactly to confirm.")
    if org.deletion_due and date.today() < org.deletion_due and not body.early:
        raise HTTPException(409, f"Data is kept until {org.deletion_due}. Tick 'delete early' only if the customer "
                                 "asked in writing.")
    counts = tenancy.delete_tenant_data(db, org)
    cert = {"company": org.name, "slug": org.slug, "deleted_at": utcnow().isoformat(), "deleted_by": op.name,
            "rows_deleted": counts, "export_sha256": (org.contract or {}).get("export_sha256")}
    org.contract = {"deletion_certificate": cert}
    org.ai_policy, org.alerts, org.repos, org.secret_key_enc, org.domain = {}, {}, [], "", ""
    org.status = "deleted"
    audit(db, op, "deleted company data", org.name, str(cert))
    db.commit()
    return cert


@router.post("/operator/tenants/{tid}/support-session")
def support_session(tid: int, op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    org = _tenant(db, tid)
    if not org.support_access_until or org.support_access_until < utcnow():
        raise HTTPException(403, "The customer has not granted support access. Ask them to grant it from Setup.")
    minutes = max(1, min(60, int((org.support_access_until - utcnow()).total_seconds() // 60)))
    user = db.get(User, op.id)
    audit(db, op, "started a read-only support session", "WhiteHats support", org.support_access_reason, org_id=org.id)
    audit(db, op, "started a support session", org.name)
    db.commit()
    return {"token": issue_token(user, support_org=org.id, minutes=minutes), "minutes": minutes,
            "org": {"name": org.name, "plan": org.plan, "status": org.status, "slug": org.slug, "is_platform": False}}


@router.post("/operator/maintenance/retention")
def run_retention(op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    removed = tenancy.enforce_retention(db)
    audit(db, op, "ran retention", "all companies", str(removed))
    db.commit()
    return {"removed": removed}


@router.get("/operator/audit")
def operator_audit(op: Principal = Depends(_operator), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.org_id == op.org_id).order_by(AuditLog.id.desc()).limit(300)).all()
    return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "target": a.target, "detail": a.detail}
            for a in rows]


# ================================================================== the customer's side


def _org(db: Session, user: Principal) -> Organization:
    return db.get(Organization, user.org_id)


def _with_check_origin(db: Session, org: Organization, readiness: dict) -> dict:
    origins = dict(db.execute(select(TenantCheck.key, TenantCheck.origin).where(TenantCheck.org_id == org.id)).all())
    for item in readiness["checks"]:
        item["origin"] = origins.get(item["key"], "live")
    return readiness


@router.get("/setup")
def setup(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    org = _org(db, user)
    r = tenancy.readiness(db, org)
    db.commit()
    return {"settings": org_settings(org, db), "readiness": _with_check_origin(db, org, r),
            "first_value": tenancy.first_value(org)}


@router.post("/setup/recheck")
def setup_recheck(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    org = _org(db, user)
    r = tenancy.readiness(db, org, include_slow=True)
    db.commit()
    return {"settings": org_settings(org, db), "readiness": _with_check_origin(db, org, r),
            "first_value": tenancy.first_value(org)}


class AlertsIn(BaseModel):
    kind: Literal["slack", "teams", "email"]
    target: str = Field(min_length=5, max_length=500)


@router.put("/setup/alerts")
def set_alerts(body: AlertsIn, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    org = _org(db, user)
    target = body.target.strip()
    if body.kind in ("slack", "teams") and not target.startswith("https://"):
        raise HTTPException(400, "Paste the full https:// webhook address.")
    org.alerts = {"kind": body.kind, "target_enc": seal(target, db, org.id)}
    audit(db, user, "set alerts channel", body.kind)
    db.commit()
    return {"ok": True}


@router.post("/setup/alerts/test")
def test_alerts(user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    org = _org(db, user)
    try:
        msg = notify.send(db, org, "Test alert from WhiteHats",
                          f"{user.name} sent this test from the Setup screen. Critical and high signals will arrive "
                          "here.")
    except notify.NotifyError as exc:
        raise HTTPException(502, str(exc)) from exc
    org.alerts = {**(org.alerts or {}), "verified_at": utcnow().isoformat()}
    audit(db, user, "sent test alert", org.alerts.get("kind", ""))
    db.commit()
    return {"message": msg}


class Repo(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=300)
    test: str = Field(default="", max_length=200)


class ReposIn(BaseModel):
    repos: list[Repo] = Field(max_length=50)


@router.put("/setup/repos")
def set_repos(body: ReposIn, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    org = _org(db, user)
    for r in body.repos:
        if not (r.url.startswith("https://") or r.url.startswith("git@")):
            raise HTTPException(400, f"{r.name}: use an https:// or git@ address.")
    org.repos = [r.model_dump() for r in body.repos]
    audit(db, user, "set code repositories", f"{len(body.repos)} repositories")
    db.commit()
    return {"ok": True}


_CLOCK = re.compile(r"^(\d{2}):(\d{2})$")


class WorkingHours(BaseModel):
    timezone: str = Field(min_length=2, max_length=64)
    work_start: str = Field(min_length=4, max_length=5)
    work_end: str = Field(min_length=4, max_length=5)


def _clock_ok(value: str) -> bool:
    match = _CLOCK.fullmatch(value.strip())
    if match is None:
        return False
    hour, minute = int(match.group(1)), int(match.group(2))
    return hour <= 23 and minute <= 59


def _check_hours(zone: str, start: str, end: str) -> None:
    """The same rule for Setup and the operator console, so the Insider WhiteHat never reads a value it cannot use."""
    if read_zone(zone) is None:
        raise HTTPException(400, "Unknown time zone.")
    if not _clock_ok(start) or not _clock_ok(end):
        raise HTTPException(400, "Working hours use 24-hour times, like 09:00.")
    if start.strip() == end.strip():
        raise HTTPException(400, "The working day needs a start and an end that differ.")


def _check_contract_hours(contract: Contract) -> None:
    """A contract may leave working hours blank (09:00-17:00 UTC applies); whatever it does set must be usable."""
    if not (contract.timezone or contract.work_start or contract.work_end):
        return
    _check_hours(contract.timezone or "UTC", contract.work_start or "09:00", contract.work_end or "17:00")


@router.put("/setup/hours")
def set_hours(body: WorkingHours, user: Principal = Depends(allow("manage_account_setup")),
              db: Session = Depends(get_db)):
    """Working hours are the company's, read by every project's Insider WhiteHat, so they are an account setting."""
    _check_hours(body.timezone, body.work_start, body.work_end)
    org = _org(db, user)
    org.contract = {**(org.contract or {}), "timezone": body.timezone.strip(),
                    "work_start": body.work_start.strip(), "work_end": body.work_end.strip()}
    audit(db, user, "set working hours", f"{body.work_start}-{body.work_end} {body.timezone.strip()}")
    db.commit()
    return setup(user, db)


class SupportIn(BaseModel):
    hours: int = Field(ge=1, le=72)
    reason: str = Field(min_length=5, max_length=300)
    project: str | None = Field(default=None, max_length=63)  # a project slug, to limit the session to it


@router.post("/setup/support-access")
def grant_support(body: SupportIn, user: Principal = Depends(allow("sign_off")), db: Session = Depends(get_db)):
    org = _org(db, user)
    limit = None
    if body.project:
        limit = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == body.project))
        if limit is None:
            raise HTTPException(404, "Project not found.")
    org.support_access_until = utcnow() + timedelta(hours=body.hours)
    org.support_access_reason = body.reason
    org.support_project_id = limit.id if limit else None
    where = f" in {limit.name} only" if limit else ""
    audit(db, user, "granted WhiteHats support access", f"{body.hours} hours{where}", body.reason)
    db.commit()
    return {"until": org.support_access_until.isoformat()}


@router.delete("/setup/support-access")
def revoke_support(user: Principal = Depends(allow("sign_off")), db: Session = Depends(get_db)):
    org = _org(db, user)
    org.support_access_until, org.support_access_reason, org.support_project_id = None, "", None
    audit(db, user, "revoked WhiteHats support access")
    db.commit()
    return {"ok": True}


@router.post("/setup/go-live")
def customer_go_live(user: Principal = Depends(allow("sign_off")), db: Session = Depends(get_db)):
    org = _org(db, user)
    r = tenancy.readiness(db, org, include_slow=True)
    if not r["ready_for_signoff"]:
        raise HTTPException(409, "Not ready yet: " + "; ".join(r["blocking"]))
    org.go_live_customer = user.name
    _try_go_live(db, org)
    audit(db, user, "signed off go-live", "", "live" if org.status == "live" else "waiting for WhiteHats")
    db.commit()
    return {"status": org.status}


# ================================================================== invitations (public)


def _invite(db: Session, token: str) -> Invite:
    inv = db.scalar(select(Invite).where(Invite.token_hash == token_digest(token)))
    if inv is None or inv.revoked_at:
        raise HTTPException(404, "This invitation is not valid. Ask whoever invited you for a new one.")
    if inv.accepted_at:
        raise HTTPException(409, "This invitation was already used. Sign in instead.")
    if inv.expires_at < utcnow():
        raise HTTPException(410, "This invitation has expired. Ask whoever invited you for a new one.")
    return inv


@router.get("/invites/{token}")
def invite_info(token: str, db: Session = Depends(get_db)):
    inv = _invite(db, token)
    org = db.get(Organization, inv.org_id)
    return {"company": org.name, "email": inv.email, "name": inv.name, "role": inv.role,
            "mfa_required": org.mfa_required, "expires_at": inv.expires_at.isoformat()}


class Accept(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=200)
    title: str = Field(default="", max_length=120)


@router.post("/invites/{token}/accept")
def accept_invite(token: str, body: Accept, db: Session = Depends(get_db)):
    inv = _invite(db, token)
    if db.scalar(select(User.id).where(func.lower(User.email) == inv.email)):
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    user = User(org_id=inv.org_id, email=inv.email, name=body.name, role=inv.role, title=body.title,
                password_hash=hash_password(body.password))
    db.add(user)
    inv.accepted_at = utcnow()
    db.flush()
    apply_invited_access(db, user, inv.account_role)
    audit(db, user, "accepted invitation", inv.email, inv.role)
    db.commit()
    return session_response(db, user)


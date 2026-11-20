"""Sign-in (with two-step codes), the overview and the Big Hat assistant."""

from __future__ import annotations

import time
from datetime import timedelta

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..knowledge import lookup
from ..models import (
    INCIDENT_CLOSED,
    ChangeEvent,
    ChatMessage,
    Collector,
    Incident,
    LLMCall,
    ModelDeployment,
    NetworkFinding,
    Organization,
    Project,
    Remediation,
    Signal,
    TenantCheck,
    User,
    VexStatement,
)
from ..permissions import allow, can, held
from ..projects import MAIN_SLUG
from ..scanning import vex
from ..security import (
    Principal,
    audit,
    issue_step_token,
    issue_token,
    new_totp_secret,
    read_token,
    seal,
    totp_uri,
    totp_verify,
    unseal,
    verify_password,
)
from ..services import briefing, deploy, local_workspace, posture, tenancy
from ..services.decisions import waiting
from ..services.llm import Section, complete, pack_context

router = APIRouter(prefix="/api")
TERMS_VERSION = "2026-10"


def user_out(u) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "account_role": u.account_role,
            "title": u.title}


def _workspace_sample(db: Session | None, org: Organization) -> bool:
    """True only when this company still holds sample rows. The name is not a clue."""
    if org.is_platform or db is None:
        return False
    for model in (Incident, Signal, Remediation, TenantCheck):
        if db.scalar(select(model.id).where(model.org_id == org.id, model.origin == "sample").limit(1)):
            return True
    return False


def org_out(org: Organization, db: Session | None = None) -> dict:
    return {"name": org.name, "plan": org.plan, "status": org.status, "slug": org.slug,
            "is_platform": org.is_platform, "local_workspace": local_workspace.ENABLED, "sample": _workspace_sample(db, org),
            # STUDIO.7: whether people may draw the map by hand, or only the connections draw it.
            "studio_hand_editing": settings.studio_hand_editing}


def session_response(db: Session, user: User) -> dict:
    """Finish sign-in, or say which step is still needed."""
    org = db.get(Organization, user.org_id)
    if user.mfa_enrolled_at:
        return {"step": "mfa", "step_token": issue_step_token(user, "mfa")}
    if org.mfa_required or user.role == "operator":
        return {"step": "mfa_setup", "step_token": issue_step_token(user, "mfa_setup")}
    return complete_login(db, user)


def complete_login(db: Session, user: User) -> dict:
    user.last_login = utcnow()
    audit(db, user, "signed in")
    db.commit()
    org = db.get(Organization, user.org_id)
    return {"step": "done", "token": issue_token(user), "user": user_out(user), "org": org_out(org, db),
            "needs_terms": user.terms_accepted_at is None}


class Login(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: Login, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        time.sleep(0.4)
        raise HTTPException(401, "That email and password do not match.")
    org = db.get(Organization, user.org_id)
    if org.status in ("suspended", "offboarded") and not org.is_platform:
        raise HTTPException(403, f"This company's access is {org.status}. Contact WhiteHats.")
    return session_response(db, user)


class Code(BaseModel):
    step_token: str
    code: str


@router.post("/auth/mfa")
def mfa(body: Code, db: Session = Depends(get_db)):
    payload = read_token(body.step_token, "mfa")
    user = db.get(User, int(payload["sub"]))
    if user is None or not totp_verify(unseal(user.totp_secret_enc, db, user.org_id), body.code):
        time.sleep(0.4)
        raise HTTPException(401, "That code is not right. Use the 6-digit code from your authenticator app.")
    return complete_login(db, user)


class SetupStart(BaseModel):
    step_token: str


@router.post("/auth/mfa/setup")
def mfa_setup(body: SetupStart, db: Session = Depends(get_db)):
    payload = read_token(body.step_token, "mfa_setup")
    user = db.get(User, int(payload["sub"]))
    secret = new_totp_secret()
    user.totp_secret_enc = seal(secret, db, user.org_id)
    db.commit()
    uri = totp_uri(secret, user.email)
    return {"secret": secret, "uri": uri, "qr_svg": segno.make(uri, error="m").svg_inline(scale=5, dark="#0f1a2a")}


@router.post("/auth/mfa/confirm")
def mfa_confirm(body: Code, db: Session = Depends(get_db)):
    payload = read_token(body.step_token, "mfa_setup")
    user = db.get(User, int(payload["sub"]))
    if not user.totp_secret_enc or not totp_verify(unseal(user.totp_secret_enc, db, user.org_id), body.code):
        raise HTTPException(401, "That code is not right. Check the time on your phone and try the next code.")
    user.mfa_enrolled_at = utcnow()
    audit(db, user, "turned on two-step sign-in")
    return complete_login(db, user)


@router.post("/auth/terms")
def accept_terms(user: Principal = Depends(allow("be_signed_in")), db: Session = Depends(get_db)):
    u = db.get(User, user.id)
    u.terms_accepted_at = utcnow()
    audit(db, user, "accepted the terms", TERMS_VERSION)
    db.commit()
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request, user: Principal = Depends(allow("be_signed_in")), db: Session = Depends(get_db)):
    org = db.get(Organization, user.org_id)
    u = db.get(User, user.id)
    slug = request.query_params.get("project")
    project = None
    if slug:
        project = db.scalar(select(Project).where(Project.org_id == user.org_id, Project.slug == slug))
    person = {**user_out(u), "role": user.role, "permissions": held(db, user, project)}
    out = {"user": person, "org": org_out(org, db), "support": user.support,
           "project_archived": bool(project is not None and project.archived),
           "needs_terms": u.terms_accepted_at is None and not user.support, "mfa": bool(u.mfa_enrolled_at)}
    if not org.is_platform and org.status in ("draft", "onboarding"):
        r = tenancy.readiness(db, org)
        mine = [c for c in r["checks"] if c["owner"] == "customer" and c["required"] and c["status"] != "passed"]
        out["setup"] = {"status": org.status, "open": len(mine), "passed": r["passed"], "total": r["total"]}
        db.commit()
    return out


# ------------------------------------------------------------------ overview

# Keyed by account, project and whether the reader sees raw account data, so no briefing crosses a project. A briefing is
# reused only while the facts it was written from are unchanged, so it never quotes a score the gauge beside it has left.
_summary_cache: dict[tuple[int, int, bool], tuple[float, str, dict]] = {}


def briefing_scope(db: Session, user: Principal) -> briefing.Scope:
    """The project the request resolved, plus raw account data only for the roles that may read it."""
    return briefing.one_project(user.project.id, account_data=can(db, user, "read_account_data"))


def code_findings(db: Session, org: int, pid: int, week) -> list[dict]:
    """What pushed changes show (VS.4): the open findings, and the ones a later commit closed this week."""
    rows = db.scalars(select(Signal).where(
        Signal.org_id == org, Signal.project_id == pid, Signal.fingerprint != "",
        or_(Signal.closed_at.is_(None), Signal.closed_at >= week))
        .order_by(Signal.closed_at.is_(None).desc(), Signal.created_at.desc()).limit(30)).all()
    changes = {c.id: c for c in db.scalars(select(ChangeEvent).where(
        ChangeEvent.id.in_([r.change_id for r in rows if r.change_id]))).all()}
    statements = {v.id: v for v in db.scalars(select(VexStatement).where(
        VexStatement.id.in_([r.vex_statement_id for r in rows if r.vex_statement_id]))).all()}
    out = []
    for r in rows:
        change = changes.get(r.change_id)
        out.append({
            "id": r.id, "title": r.title, "severity": r.severity, "where": r.entity, "branch": r.branch,
            "repo": r.resource.removeprefix("https://github.com/"),
            "commit": change.head_sha[:7] if change else "",
            "commit_url": f"{r.resource}/commit/{change.head_sha}" if change else "",
            "check_url": change.check_url if change else "",
            "explanation": r.detail.split("\n", 1)[0],
            "status": "open" if r.closed_at is None else "closed", "closed_reason": r.closed_reason,
            "at": r.created_at.isoformat(),
            # SCAN.5: a VEX statement in force marks it; it stays listed
            "vex": vex.words(statements[r.vex_statement_id]) if r.vex_status and r.vex_statement_id in statements else "",
            "vex_statement_id": r.vex_statement_id if r.vex_status else None,
            "image_finding": r.fingerprint.startswith("image|") and r.fingerprint.count("|") == 1,
        })
    return out


@router.get("/overview")
def overview(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    org = user.org_id
    pid = user.project.id
    slug = user.project.slug
    # Reading the page must not store a score. The daily worker owns snapshots.
    p = posture.compute(db, org, project_id=pid)
    tenancy.mark_milestone(db, org, "first_score")
    db.commit()
    hist = posture.history(db, org, project_id=pid)
    started = posture.history_started(db, org, project_id=pid)
    incidents = db.scalars(select(Incident).where(
        Incident.org_id == org, Incident.project_id == pid, Incident.status.notin_(INCIDENT_CLOSED))
        .order_by(Incident.created_at.desc())).all()
    week = utcnow() - timedelta(days=7)
    signals_7d = db.scalar(select(func.count(Signal.id)).where(
        Signal.org_id == org, Signal.project_id == pid, Signal.created_at >= week, Signal.learning.is_(False))) or 0
    fixed_7d = db.scalar(select(func.count(Remediation.id)).where(
        Remediation.org_id == org, Remediation.project_id == pid,
        Remediation.status.in_(["dispatched", "done"]), Remediation.decided_at >= week)) or 0
    # Collectors belong to the account. A project other than Main does not repeat them.
    cols = db.scalars(select(Collector).where(Collector.org_id == org)).all() if slug == MAIN_SLUG else []
    recent = db.scalars(select(Signal).where(
        Signal.org_id == org, Signal.project_id == pid, Signal.fingerprint == "", Signal.learning.is_(False))
        .order_by(Signal.created_at.desc()).limit(20)).all()

    def signal_out(s):
        return {"id": s.id, "title": s.title, "severity": s.severity, "origin": s.origin,
                "agent": s.agent.name if s.agent else s.source, "at": s.created_at.isoformat()}

    decisions = waiting(db, org, project_id=pid, link_prefix=f"/p/{slug}")
    fix_origin = dict(db.execute(select(Remediation.id, Remediation.origin).where(
        Remediation.org_id == org, Remediation.project_id == pid)).all())
    for item in decisions:
        if item["kind"] == "fix":
            item["origin"] = fix_origin.get(item["id"], "live")

    return {
        "posture": p, "history": hist, "history_started": started,
        "kpis": {
            "open_incidents": len(incidents),
            "critical_incidents": sum(1 for i in incidents if i.severity == "critical"),
            "signals_7d": signals_7d, "fixes_7d": fixed_7d,
            "collectors_online": sum(1 for c in cols if deploy.health(c) == "online"), "collectors_total": len(cols),
            "open_network_findings": db.scalar(select(func.count(NetworkFinding.id)).where(
                NetworkFinding.org_id == org, NetworkFinding.project_id == pid, NetworkFinding.status == "open")) or 0,
        },
        "decisions": decisions,
        "incidents": [{"id": i.id, "ref": i.ref, "title": i.title, "severity": i.severity, "status": i.status,
                       "origin": i.origin, "impact": i.business_impact,
                       "created_at": i.created_at.isoformat()} for i in incidents[:5]],
        "code_findings": code_findings(db, org, pid, week),
        "signals": [signal_out(s) for s in recent if s.created_at >= week][:6],
        "earlier_signals": [signal_out(s) for s in recent if s.created_at < week][:6],
    }


@router.get("/overview/summary")
def overview_summary(refresh: bool = False, user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    scope = briefing_scope(db, user)
    key = (user.org_id, user.project.id, scope.account_data)
    secs = briefing.sections(db, user.org_id, scope)
    packed = pack_context(secs, 2500)
    cached = _summary_cache.get(key)
    if cached and not refresh and cached[1] == packed.text and time.time() - cached[0] < 600:
        return cached[2]
    fallback = briefing.rules_answer(db, user.org_id, "summary", scope)
    res = complete(db, user.org_id, "executive",
                   "You brief a CEO on their company's cyber security in plain English. Three short sentences: "
                   "how we are doing, the one thing that needs their attention, and what the team is doing about it. "
                   "No jargon, no acronyms, no bullet points.",
                   packed.text, fallback, max_tokens=500)
    out = {"text": res.text, "model": res.model, "fallback": res.fallback, "context_tokens": packed.tokens,
           "at": utcnow().isoformat()}
    _summary_cache[key] = (time.time(), packed.text, out)
    return out


# ------------------------------------------------------------------ Big Hat assistant


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


SYSTEM = (
    "You are Big Hat, the commander of the WhiteHats security swarm. You answer leaders and analysts about their "
    "own company's security using ONLY the context provided. Be direct and plain-spoken. Lead with the answer. "
    "Cite the evidence with the bracketed references from the context, like [INC-123] or [FIX-3]. If the "
    "context does not contain the answer, say so and say which screen would show it. Keep it under 180 words "
    "unless asked for detail. Name an advisory id, an affected version range or a fixed version only when the "
    "Advisories section gives it, and cite it as [ADV-<id>]; never from memory. When a question touches what the "
    "business depends on, weigh it against the knowledge sphere's rules: a known rule is a fact the approvers "
    "confirmed, a proposed rule is only a hint, and an accepted exception was decided safe on purpose. Combine "
    "incidents, audit findings and the sphere in one answer rather than listing them apart."
)


def advisory_section(db: Session, question: str) -> Section | None:
    """KB.1: the knowledge base's facts for each advisory the question names, so Big Hat cites them, not its memory."""
    lines = []
    for advisory_id in lookup.cited_ids(question)[:5]:
        found = lookup.by_id(db, advisory_id)
        if found is None:
            lines.append(f"- {advisory_id}: not in the knowledge base.")
            continue
        packages = sorted({(item.get("package") or {}).get("name", "") for item in found["affected"]} - {""})
        lines.append(f"- [ADV-{found['id']}] {', '.join(found['aliases'])}. {found['summary'][:240]} Packages: "
                     f"{', '.join(packages[:6]) or 'none listed'}. Fixed in: {', '.join(found['fixed_versions'][:8]) or 'no fix listed'}. "
                     f"Record modified {found['modified']}, {found['source']}.")
    if not lines:
        return None
    return Section("Advisories (WhiteHats knowledge base, OSV.dev, CC BY 4.0)", "\n".join(lines), 1)


def unknown_note(db: Session, answer: str) -> str:
    """A sentence naming any advisory id in the answer that the knowledge base does not hold, else nothing."""
    unknown = lookup.unknown_ids(db, answer)
    if not unknown:
        return ""
    return (f"\n\nNote: {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not in WhiteHats' knowledge "
            "base, so treat that part of the answer as unchecked.")


def _budget(db: Session, org_id: int) -> int:
    dep = db.scalar(select(ModelDeployment).where(ModelDeployment.org_id == org_id, ModelDeployment.enabled.is_(True),
                                                  ModelDeployment.provider != "rules")
                    .order_by(ModelDeployment.priority))
    window = dep.context_window if dep else 8000
    return max(1500, min(6000, window // 4))


@router.get("/assistant/suggestions")
def assistant_suggestions(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    return briefing.suggestions(db, user.org_id, briefing_scope(db, user))


@router.get("/assistant/history")
def history(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    msgs = db.scalars(select(ChatMessage).where(
        ChatMessage.user_id == user.id, ChatMessage.project_id == user.project.id).order_by(ChatMessage.id)).all()
    return [{"id": m.id, "role": m.role, "text": m.text, "meta": m.meta, "at": m.at.isoformat()} for m in msgs[-60:]]


@router.delete("/assistant/history")
def clear_history(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    db.query(ChatMessage).filter(ChatMessage.user_id == user.id, ChatMessage.project_id == user.project.id).delete()
    db.commit()
    return {"ok": True}


@router.post("/assistant/ask")
def ask(body: Ask, user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    budget = _budget(db, user.org_id)
    scope = briefing_scope(db, user)
    extra = advisory_section(db, body.question)
    packed = pack_context([*([extra] if extra else []), *briefing.sections(db, user.org_id, scope),
                           *briefing.knowledge_sections(db, user.org_id, scope)], budget)
    project_id = user.project.id
    prior = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.project_id == project_id)
                       .order_by(ChatMessage.id.desc()).limit(4)).all()
    convo = "\n".join(f"{m.role}: {m.text[:400]}" for m in reversed(prior))
    prompt = f"{packed.text}\n## Recent conversation\n{convo or 'None.'}\n\n## Question\n{body.question}"
    res = complete(db, user.org_id, "assistant", SYSTEM, prompt,
                   briefing.rules_answer(db, user.org_id, body.question, scope))
    meta = {"model": res.model, "fallback": res.fallback, "context_tokens": packed.tokens, "budget": budget,
            "included": packed.included, "trimmed": packed.trimmed, "dropped": packed.dropped,
            "latency_ms": res.latency_ms}
    db.add(ChatMessage(org_id=user.org_id, project_id=project_id, user_id=user.id, role="user", text=body.question))
    msg = ChatMessage(org_id=user.org_id, project_id=project_id, user_id=user.id, role="assistant",
                      text=res.text + unknown_note(db, res.text), meta=meta)
    db.add(msg)
    db.commit()
    return {"id": msg.id, "role": "assistant", "text": msg.text, "meta": meta, "at": msg.at.isoformat()}


@router.get("/usage/ai")
def ai_usage(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=30)
    rows = db.execute(select(LLMCall.model_label, LLMCall.purpose, func.count(LLMCall.id),
                             func.sum(LLMCall.prompt_tokens), func.sum(LLMCall.completion_tokens),
                             func.avg(LLMCall.latency_ms))
                      .where(LLMCall.org_id == user.org_id, LLMCall.at >= since)
                      .group_by(LLMCall.model_label, LLMCall.purpose)).all()
    return [{"model": r[0], "purpose": r[1], "calls": r[2], "prompt_tokens": int(r[3] or 0),
             "completion_tokens": int(r[4] or 0), "avg_latency_ms": int(r[5] or 0)} for r in rows]


"""Builds the context the AI sees: the organisation's state, packed into a token budget.

The model never gets raw tables or logs. Each area writes a short section, and
`pack_context` keeps the most important sections whole and trims or drops the
rest, so the prompt fits the deployment's context window however big the
estate is. Every read is limited to a `Scope`: the projects the person is looking at,
and whether they may read the account's raw logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import INCIDENT_CLOSED, Collector, Incident, LogDigest, NetworkFinding, Project, Remediation, Signal
from . import deploy, posture
from .decisions import waiting
from .llm import Section

# The Overview shows this many rows. The count in the briefing is the whole queue.
SHOWN = 8


@dataclass(frozen=True)
class Scope:
    """What one briefing may read: these projects (None is the whole account), and raw account data or not."""

    project_ids: tuple[int, ...] | None
    account_data: bool

    def owned(self, model, org_id: int) -> list:
        clauses = [model.org_id == org_id]
        if self.project_ids is not None:
            clauses.append(model.project_id.in_(self.project_ids))
        return clauses


WHOLE_ACCOUNT = Scope(project_ids=None, account_data=True)


def one_project(project_id: int, *, account_data: bool) -> Scope:
    return Scope(project_ids=(project_id,), account_data=account_data)


def _queue(db: Session, org_id: int, scope: Scope) -> list[dict]:
    if scope.project_ids is None:
        return waiting(db, org_id)
    return [item for pid in scope.project_ids for item in waiting(db, org_id, project_id=pid)]


def _posture_text(db: Session, org_id: int, scope: Scope) -> str:
    if scope.project_ids is not None and len(scope.project_ids) > 1:
        names = dict(db.execute(select(Project.id, Project.name).where(Project.id.in_(scope.project_ids))).all())
        return " ".join(
            f"{names.get(pid, 'Project')}: score {p['score']}/1000 ({p['grade']})."
            for pid in scope.project_ids for p in [posture.compute(db, org_id, project_id=pid)])
    pid = scope.project_ids[0] if scope.project_ids else None
    p = posture.compute(db, org_id, project_id=pid)
    return f"Score {p['score']}/1000 ({p['grade']}). " + "; ".join(
        f"{b['area']}: -{b['penalty']} ({b['detail']})" for b in p["breakdown"] if b["penalty"])


def sections(db: Session, org_id: int, scope: Scope) -> list[Section]:
    out = [Section("Posture", _posture_text(db, org_id, scope), 1)]

    inc = db.scalars(select(Incident).where(*scope.owned(Incident, org_id), Incident.status.notin_(INCIDENT_CLOSED))
                     .order_by(Incident.created_at.desc()).limit(8)).all()
    out.append(Section("Open incidents", "\n".join(
        f"- [{i.ref}] {i.severity.upper()} {i.status}: {i.title}. {i.summary[:220]}" for i in inc) or "None.", 1))

    queue = _queue(db, org_id, scope)
    rem = db.scalars(select(Remediation).where(*scope.owned(Remediation, org_id),
                                               Remediation.status == "pending")).all()
    shown = queue[:SHOWN]
    extra = len(queue) - len(shown)
    lines = [f"- {item['title']}" for item in shown]
    if extra:
        lines.append(f"- and {extra} more")
    out.append(Section("Decisions waiting", f"{len(queue)} decisions wait for you.\n" + "\n".join(lines), 1))
    out.append(Section("Fixes waiting for approval", "\n".join(
        f"- [FIX-{r.id}] {r.title} on {r.target} via {r.destination}. Risk {r.risk}."
        for r in rem) or "None.", 2))

    sig = db.scalars(select(Signal).where(*scope.owned(Signal, org_id), Signal.created_at >= utcnow() - timedelta(days=2),
                                          Signal.learning.is_(False))
                     .order_by(Signal.created_at.desc()).limit(15)).all()
    out.append(Section("Recent signals (48h)", "\n".join(
        f"- [SIG-{s.id}] {s.severity} {s.agent.name if s.agent else s.source}: {s.title}" for s in sig) or "None.", 3))

    nf = db.scalars(select(NetworkFinding).where(*scope.owned(NetworkFinding, org_id),
                                                 NetworkFinding.status == "open")
                    .order_by(NetworkFinding.severity)).all()
    out.append(Section("Network findings", "\n".join(
        f"- [NET-{f.id}] {f.severity}: {f.title} on {f.host}" for f in nf[:10]) or "None.", 3))

    # A digest is written from the account's raw logs, which only the roles that see every project read.
    dg = db.scalar(select(LogDigest).where(LogDigest.org_id == org_id)
                   .order_by(LogDigest.created_at.desc())) if scope.account_data else None
    if dg:
        out.append(Section("Latest log digest", f"{dg.lines} lines -> {dg.templates} shapes -> {dg.notable} notable."
                           f"\n{dg.ai_summary[:900]}", 4))

    cols = db.scalars(select(Collector).where(Collector.org_id == org_id)).all()
    out.append(Section("Coverage", watch_summary(db, org_id, scope)
                       + " Collectors: " + ", ".join(f"{c.name} {deploy.health(c)}" for c in cols), 5))
    return out


# What Ask Big Hat also reads (BH.2): what the business depends on, and what the security audits found.
SPHERE_SHOWN = 40
AUDIT_SHOWN = 12
_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def knowledge_sections(db: Session, org_id: int, scope: Scope) -> list[Section]:
    """The project's knowledge sphere (confirmed rules first, then proposed ones and accepted exceptions) and the open
    findings its security audits confirmed. Only for one project's briefing: the sphere belongs to a project."""
    if not scope.project_ids:
        return []
    from ..sphere import store

    lines, found = [], []
    for pid in scope.project_ids:
        nodes = store.state(db, pid).as_dict()["nodes"]
        concepts = {n["key"]: n["text"] for n in nodes if n["kind"] == "Concept"}
        ranked = sorted((n for n in nodes if n["kind"] in ("Rule", "Exception")),
                        key=lambda n: (n["kind"] != "Rule", n.get("status") != "known"))
        for n in ranked[:SPHERE_SHOWN]:
            what = "Accepted exception" if n["kind"] == "Exception" else (
                "Known rule" if n.get("status") == "known" else "Proposed rule, not yet confirmed")
            protects = f", protects {n['protects']}" if n.get("protects") else ""
            lines.append(f"- {what}{protects}: {n['text']}")
        if concepts and len(lines) < SPHERE_SHOWN:
            lines.append("- Things the business deals in: " + ", ".join(sorted(concepts.values())[:20]))
    rows = db.scalars(select(Signal).where(*scope.owned(Signal, org_id), Signal.audit_id.is_not(None),
                                           Signal.closed_at.is_(None), Signal.learning.is_(False))).all()
    for row in sorted(rows, key=lambda r: (_RANK.get(r.severity, 5), -r.id))[:AUDIT_SHOWN]:
        found.append(f"- [SIG-{row.id}] {row.severity} (audit {row.audit_id}): {row.title}")
    extra = len(rows) - len(found)
    if extra > 0:
        found.append(f"- and {extra} more")
    return [Section("What the business depends on (knowledge sphere)", "\n".join(lines) or "Nothing learned yet.", 2),
            Section("Open security audit findings", "\n".join(found) or "None.", 2)]


def watch_summary(db: Session, org_id: int, scope: Scope) -> str:
    """Names from whitehat_watch, the same count the WhiteHats page and the posture score use."""
    ids = None if scope.project_ids is None else list(scope.project_ids)
    watched = posture.whitehat_watch(db, org_id, project_ids=ids)
    waiting = [row["name"] for row in watched["agents"] if row["state"] == "waiting"]
    blind = [row["name"] for row in watched["agents"] if row["state"] == "blind"]
    parts = []
    if waiting:
        parts.append("Waiting for data: " + ", ".join(waiting))
    if blind:
        parts.append("Not watching: " + ", ".join(blind))
    if not parts:
        parts.append("Every WhiteHat is watching")
    return ". ".join(parts) + "."


_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _open_incidents(db: Session, org_id: int, scope: Scope) -> list[Incident]:
    rows = db.scalars(select(Incident).where(*scope.owned(Incident, org_id), Incident.status.notin_(INCIDENT_CLOSED))).all()
    return sorted(rows, key=lambda i: (_SEV.get(i.severity, 9), i.ref))


def suggestions(db: Session, org_id: int, scope: Scope) -> list[str]:
    """Questions that follow this project's records. An empty project never names an incident."""
    out = []
    incidents = _open_incidents(db, org_id, scope)
    if incidents:
        worst = incidents[0]
        out.append(f"Explain {worst.ref} like I am a board member")
    queue = _queue(db, org_id, scope)
    if queue:
        out.append(f"What should I decide first about {queue[0]['title']}?")
    ids = None if scope.project_ids is None else list(scope.project_ids)
    unwatched = [row["name"] for row in posture.whitehat_watch(db, org_id, project_ids=ids)["agents"]
                 if row["state"] != "watching"]
    if unwatched:
        names = ", ".join(unwatched[:3])
        out.append(f"Why is {names} not watching?")
    if not out:
        out.append("What is our security posture today?")
    return out[:5]


def rules_answer(db: Session, org_id: int, _question: str, scope: Scope) -> str:
    """Offline answer built straight from the data, used when no model is reachable."""
    incidents = _open_incidents(db, org_id, scope)
    queue = _queue(db, org_id, scope)
    findings = db.scalars(select(NetworkFinding).where(*scope.owned(NetworkFinding, org_id),
                                                       NetworkFinding.status == "open")).all()
    findings = sorted(findings, key=lambda f: _SEV.get(f.severity, 9))
    lines = [
        "No model is connected. These are the facts on record.",
        _posture_sentence(db, org_id, scope),
    ]
    if incidents:
        worst = incidents[0]
        lines.append(f"{len(incidents)} incidents are open. The most serious is [{worst.ref}] {worst.title}.")
    else:
        lines.append("No incidents are open.")
    if queue:
        lines.append(f"{len(queue)} decisions wait for you. The first is {queue[0]['title']}.")
    else:
        lines.append("Nothing is waiting for a decision.")
    lines.append(watch_summary(db, org_id, scope))
    if findings:
        lines.append(f"Network: {len(findings)} open findings. The worst is {findings[0].title}.")
    else:
        lines.append("No network findings are open.")
    return " ".join(lines)


def _posture_sentence(db: Session, org_id: int, scope: Scope) -> str:
    if scope.project_ids is not None and len(scope.project_ids) > 1:
        return "Posture by project: " + _posture_text(db, org_id, scope)
    pid = scope.project_ids[0] if scope.project_ids else None
    p = posture.compute(db, org_id, project_id=pid)
    return f"Posture is {p['score']}/1000 ({p['grade']})."

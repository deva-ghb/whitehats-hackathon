"""Threat posture score (0-1000): one number a board can track, with the reasons behind it."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    INCIDENT_CLOSED,
    Agent,
    Collector,
    Connector,
    Incident,
    NetworkFinding,
    Organization,
    PostureSnapshot,
    Project,
    Remediation,
    Signal,
)
from ..projects import main_project_id
from ..whitehats import CATALOG
from . import deploy

log = logging.getLogger("whitehats.posture")

INCIDENT_WEIGHT = {"critical": 90, "high": 45, "medium": 20, "low": 6}
FINDING_WEIGHT = {"critical": 60, "high": 40, "medium": 12, "low": 3}
SIGNAL_WEIGHT = {"critical": 25, "high": 10}
NOTHING_WATCHED = 400
# A high-criticality project counts three times in the account score, medium twice, low once.
CRITICALITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}

# Cloud log sync runs every 5 minutes. "Recent" uses the same online window as deploy.health:
# two intervals plus 10 seconds.
_CLOUD_SYNC_INTERVAL_S = 300

# No table links a collector or a connector to a WhiteHat. Delivery is attributed by data source.
# A log collector feeds logs. A network analyzer feeds network. A process monitor feeds processes.
# A cloud-mode collector stores the connector key on Collector.host and uses the connector map.
_KIND_SOURCES = {
    "log": ("logs",),
    "network": ("network",),
    "process": ("processes",),
}
_CONNECTOR_SOURCES = {
    "cloudwatch": ("logs", "cloud_audit"),
    "aws": ("cloud_audit",),  # the AWS connection reads CloudTrail itself
    "azure_monitor": ("logs",),
    "gcp_logging": ("logs",),
    "gcp": ("cloud_audit",),  # the Google Cloud connection reads the admin activity log itself
    "okta": ("identity",),
    "entra": ("identity",),
    "google": ("identity",),
    "github": ("code",),
}


def _collector_sources(collector: Collector) -> tuple[str, ...]:
    """Data sources a collector heartbeat stands in for. Cloud mode uses the connector key on host."""
    if collector.mode == "cloud":
        mapped = _CONNECTOR_SOURCES.get(collector.host)
        if mapped:
            return mapped
    return _KIND_SOURCES.get(collector.kind, ())


def _sync_recent(when: datetime, every_s: int = _CLOUD_SYNC_INTERVAL_S) -> bool:
    return (utcnow() - when).total_seconds() <= every_s * 2 + 10


def _mark_delivery(ever: dict[str, bool], recent: dict[str, bool], sources: tuple[str, ...], fresh: bool) -> None:
    for source in sources:
        ever[source] = True
        if fresh:
            recent[source] = True


def _source_delivery(db: Session, org_id: int) -> tuple[dict[str, bool], dict[str, bool]]:
    """Each data source: whether real data ever arrived, and whether the latest arrival is recent.

    Signals are not read. A sample signal and a live signal are both ignored.
    """
    ever: dict[str, bool] = {}
    recent: dict[str, bool] = {}
    collectors = db.scalars(select(Collector).where(Collector.org_id == org_id)).all()
    for collector in collectors:
        if collector.last_heartbeat is None:
            continue
        fresh = deploy.health(collector) == "online"
        _mark_delivery(ever, recent, _collector_sources(collector), fresh)
    connectors = db.scalars(select(Connector).where(
        Connector.org_id == org_id, Connector.last_sync_at.is_not(None))).all()
    for connector in connectors:
        sources = _CONNECTOR_SOURCES.get(connector.key, ())
        if any(f.get("destination") == "logs" for f in (connector.state or {}).get("flow_logs") or []):
            sources = (*sources, "network")  # it reads a network's traffic log (NET.2)
        every = 3600 if connector.key in ("okta", "entra", "google") else _CLOUD_SYNC_INTERVAL_S  # read hourly (ID.2)
        _mark_delivery(ever, recent, sources, _sync_recent(connector.last_sync_at, every))
    return ever, recent


def _watch_state(status: str, sources: tuple[str, ...], ever: dict[str, bool], recent: dict[str, bool]) -> str:
    """watching, waiting, or blind. Waiting means no declared source has ever delivered real data."""
    if status == "dark":
        return "blind"
    if any(recent.get(source) for source in sources):
        return "watching"
    if any(ever.get(source) for source in sources):
        return "blind"
    return "waiting"


def whitehat_watch(db: Session, org_id: int, *, project_ids: list[int] | None = None) -> dict:
    """Each switched-on WhiteHat is watching, waiting, or a blind spot.

    A WhiteHat is watching when it is not dark and at least one data source it declares has a recent
    collector heartbeat or connector sync. It is waiting when none of those sources has ever delivered,
    and a blind spot when it is dark or the last real delivery is no longer recent. Waiting counts as a
    blind spot. Sample signals, live signals and last_signal_at do not count. Planned WhiteHats are omitted.
    Heartbeats and syncs are attributed by source, because the tables do not point at a WhiteHat: log,
    Azure Monitor and Google Cloud Logging feed logs; a process monitor feeds processes; a network analyzer
    feeds network; CloudWatch feeds logs and cloud_audit; the AWS connection feeds cloud_audit; Okta, Entra ID and Google Workspace feed identity;
    GitHub feeds code. The Identity WhiteHat declares identity too, so a sign-in provider read hourly makes it watch.
    Recent is deploy.health online, or a sync inside that window with the 300 second cloud interval.
    WhiteHats belong to a project: with project_ids, only those projects' WhiteHats are listed.
    Sources stay the account's, because collectors and connections are shared.
    """
    ever, recent = _source_delivery(db, org_id)
    query = select(Agent).where(Agent.org_id == org_id)
    if project_ids is not None:
        query = query.where(Agent.project_id.in_(project_ids))
    agents = db.scalars(query.order_by(Agent.id)).all()
    rows = []
    for agent in agents:
        hat = CATALOG.get(agent.key)
        if hat is not None and not hat.available:
            continue
        sources = hat.data_sources if hat is not None else ()
        rows.append({"id": agent.id, "key": agent.key, "name": agent.name,
                     "state": _watch_state(agent.status, sources, ever, recent)})
    watching = sum(1 for row in rows if row["state"] == "watching")
    return {"agents": rows, "watching": watching, "blind_spots": len(rows) - watching}


def grade_for(score: int, *, unseen: bool) -> str:
    if unseen:
        return "Unseen"
    if score >= 800:
        return "Strong"
    if score >= 650:
        return "Fair"
    if score >= 450:
        return "At risk"
    return "Critical"


def _owned(model, org_id: int, project_id: int | None):
    clauses = [model.org_id == org_id]
    if project_id is not None:
        clauses.append(model.project_id == project_id)
    return clauses


def _open_signals(project_ids: list[int], *columns):
    """A finding is open while it is not closed (a later commit removed it) and not on a resolved incident."""
    return (select(*columns).select_from(Signal)
            .outerjoin(Incident, Incident.id == Signal.incident_id)
            .where(Signal.project_id.in_(project_ids), Signal.closed_at.is_(None), Signal.learning.is_(False),
                   or_(Signal.incident_id.is_(None), Incident.status.notin_(INCIDENT_CLOSED))))


def findings_once(db: Session, project_ids: list[int]) -> dict:
    """Open findings in these projects, for the account roll-up.

    A finding is a signal not on a resolved incident. The same real resource with the same title
    counts once for the account, however many projects hold it; each project counts its own.
    """
    if not project_ids:
        return {"account": 0, "projects": {}}
    slugs = dict(db.execute(select(Project.id, Project.slug).where(Project.id.in_(project_ids))).all())
    counts = {slugs[pid]: 0 for pid in project_ids if pid in slugs}
    keyed = _open_signals(project_ids, Signal.resource, Signal.title).where(Signal.resource != "").distinct()
    shared = db.scalar(select(func.count()).select_from(keyed.subquery())) or 0
    alone = db.scalar(_open_signals(project_ids, func.count(Signal.id)).where(Signal.resource == "")) or 0
    grouped = db.execute(_open_signals(project_ids, Signal.project_id, func.count(Signal.id))
                         .group_by(Signal.project_id)).all()
    for pid, count in grouped:
        if pid in slugs:
            counts[slugs[pid]] = int(count)
    return {"account": int(shared) + int(alone), "projects": counts}


def open_findings(db: Session, project_id: int) -> int:
    """Open findings in one project, by the same rule findings_once counts the account with."""
    return int(db.scalar(_open_signals([project_id], func.count(Signal.id))) or 0)


def _finding_keys(db: Session, project_id: int) -> set[tuple[str, str]]:
    """A project's open findings, keyed as findings_once keys them. A finding with no resource is its own key."""
    rows = db.execute(_open_signals([project_id], Signal.id, Signal.resource, Signal.title)).all()
    return {(resource, title) if resource else ("", f"signal:{sid}") for sid, resource, title in rows}


def union_views(db: Session, projects: list) -> set[int]:
    """Projects that gather other projects' findings, such as a Whole estate (spec 0A: projects may overlap).

    A union view holds every open finding of at least two other projects, and nothing those other projects
    do not also hold. The account score leaves it out, so its findings are not weighed a second time, in the
    same way findings_once counts each finding once. Two projects that merely share a resource both count.
    """
    keys = {project.id: _finding_keys(db, project.id) for project in projects}
    views = set()
    for pid, mine in keys.items():
        others = [theirs for other, theirs in keys.items() if other != pid and other not in views and theirs]
        covered = [theirs for theirs in others if theirs <= mine]
        if mine and len(covered) >= UNION_MIN_PROJECTS and mine <= set().union(*others):
            views.add(pid)
    return views


# One project that holds another's findings is a shared resource, not a view over the estate.
UNION_MIN_PROJECTS = 2


def roll_up(db: Session, org_id: int, projects: list) -> dict:
    """Each project's own score, the account score weighted by criticality, and the lowest project.

    A union view (union_views) is listed with its own score but not weighted into the account's.
    """
    left_out = union_views(db, projects)
    rows = []
    for project in projects:
        scored = compute(db, org_id, project_id=project.id)
        rows.append({
            "slug": project.slug, "name": project.name,
            "criticality": project.criticality or "medium",
            "score": scored["score"], "grade": scored["grade"],
            "in_account_score": project.id not in left_out,
        })
    counted = [row for row in rows if row["in_account_score"]]
    weights = [CRITICALITY_WEIGHT.get(row["criticality"], CRITICALITY_WEIGHT["medium"]) for row in counted]
    total = sum(weights)
    if total == 0:
        account_score, lowest, grade = None, None, "Unseen"
    else:
        weighted = sum(row["score"] * weight for row, weight in zip(counted, weights, strict=True))
        account_score = (weighted + total // 2) // total
        lowest_row = min(rows, key=lambda row: (row["score"], row["name"]))
        lowest = {"slug": lowest_row["slug"], "name": lowest_row["name"], "score": lowest_row["score"]}
        grade = grade_for(account_score, unseen=all(row["grade"] == "Unseen" for row in counted))
    return {
        "projects": rows,
        "account": {"score": account_score, "grade": grade, "weights": dict(CRITICALITY_WEIGHT)},
        "lowest": lowest,
        "findings": findings_once(db, [project.id for project in projects]),
    }


def compute(db: Session, org_id: int, *, project_id: int | None = None) -> dict:
    parts = []
    incidents = db.scalars(select(Incident).where(
        *_owned(Incident, org_id, project_id), Incident.status.notin_(INCIDENT_CLOSED))).all()
    # One said to be fixed, waiting for WhiteHats to check, still matters, but half as much as one being worked.
    parts.append(("Open incidents", sum(INCIDENT_WEIGHT.get(i.severity, 10) // (2 if i.status == "verifying" else 1)
                                        for i in incidents),
                  f"{len(incidents)} open"))
    findings = db.scalars(select(NetworkFinding).where(
        *_owned(NetworkFinding, org_id, project_id), NetworkFinding.status == "open")).all()
    parts.append(("Network exposure", sum(FINDING_WEIGHT.get(f.severity, 3) for f in findings),
                  f"{len(findings)} open findings"))
    # An open finding counts until it is fixed, however old; closing it (a merged fix) raises the score at once.
    # One on an incident is counted with the incident, and one a VEX statement says does not apply is not counted.
    loose = db.scalars(select(Signal).where(
        *_owned(Signal, org_id, project_id), Signal.incident_id.is_(None), Signal.learning.is_(False),
        Signal.closed_at.is_(None), Signal.vex_status == "", Signal.severity.in_(["critical", "high"]))).all()
    parts.append(("Open findings", min(150, sum(SIGNAL_WEIGHT[s.severity] for s in loose)),
                  f"{len(loose)} high or critical findings not yet fixed or tied to an incident"))
    agents = db.scalars(select(Agent).where(*_owned(Agent, org_id, project_id))).all()
    dark = [a for a in agents if a.status == "dark"]
    collectors = db.scalars(select(Collector).where(Collector.org_id == org_id)).all()
    silent = [c for c in collectors if deploy.health(c) in ("offline", "late")]
    # The count is whitehat_watch. The penalty is unchanged: 400 until a heartbeat or a connector
    # sync exists, then dark WhiteHats and silent collectors. Waiting does not add points.
    watched = whitehat_watch(db, org_id, project_ids=None if project_id is None else [project_id])
    # Sample signals are not delivery. Only a collector heartbeat or a connector read counts.
    ever_seen = any(c.last_heartbeat for c in collectors) or db.scalar(
        select(Connector.id).where(Connector.org_id == org_id, Connector.last_sync_at.is_not(None)).limit(1)
    ) is not None
    if not ever_seen:
        # No data has ever arrived, so a quiet dashboard means "unseen", not "safe".
        parts.append(("Blind spots", NOTHING_WATCHED, "Nothing is reporting yet. Connect cloud logs or deploy a collector."))
    else:
        parts.append(("Blind spots", 30 * len(dark) + 15 * len(silent),
                      f"{len(dark)} dark WhiteHats, {len(silent)} silent collectors"))
    pending = db.scalars(select(Remediation).where(
        *_owned(Remediation, org_id, project_id), Remediation.status == "pending")).all()
    parts.append(("Fixes waiting", 6 * len(pending), f"{len(pending)} awaiting approval"))
    penalty = sum(p[1] for p in parts)
    score = max(0, min(1000, 1000 - penalty))
    grade = grade_for(score, unseen=not ever_seen)
    breakdown = [{"area": a, "penalty": p, "detail": d} for a, p, d in parts]
    for item in breakdown:
        if item["area"] == "Blind spots":
            item["count"] = watched["blind_spots"]
    return {"score": score, "grade": grade, "blind_spots": watched["blind_spots"], "breakdown": breakdown}


def snapshot(db: Session, org_id: int, day: date | None = None) -> dict:
    """Today's score for every live project of the company, each filed under its own project.

    Returns Main's, which the screens show until URLs carry a project.
    """
    day = day or date.today()
    main_id = main_project_id(db, org_id)
    project_ids = db.scalars(select(Project.id).where(
        Project.org_id == org_id, Project.archived.is_(False)).order_by(Project.id)).all()
    results = {pid: compute(db, org_id, project_id=pid) for pid in project_ids}
    for pid, result in results.items():
        row = db.scalar(select(PostureSnapshot).where(
            PostureSnapshot.project_id == pid, PostureSnapshot.day == day))
        if row is None:
            db.add(PostureSnapshot(org_id=org_id, project_id=pid, day=day, score=result["score"],
                                   breakdown=result))
        else:
            row.score, row.breakdown = result["score"], result
    db.commit()
    return results.get(main_id) or compute(db, org_id, project_id=main_id)


def history(db: Session, org_id: int, days: int = 30, *, project_id: int | None = None) -> list[dict]:
    """One project's daily scores. Each project keeps its own history, so an account-wide read would repeat days."""
    since = date.today() - timedelta(days=days - 1)
    pid = main_project_id(db, org_id) if project_id is None else project_id
    rows = db.scalars(select(PostureSnapshot).where(
        PostureSnapshot.project_id == pid, PostureSnapshot.day >= since,
    ).order_by(PostureSnapshot.day)).all()
    return [{"day": r.day.isoformat(), "score": r.score} for r in rows]


def history_started(db: Session, org_id: int, *, project_id: int | None = None) -> str | None:
    pid = main_project_id(db, org_id) if project_id is None else project_id
    day = db.scalar(select(func.min(PostureSnapshot.day)).where(PostureSnapshot.project_id == pid))
    return day.isoformat() if day else None


def record_daily_snapshots(db: Session, day: date | None = None) -> int:
    """Write today's score for every live project of every live company.

    Each company is its own transaction. One failure is logged and the rest still run.
    """
    orgs = db.scalars(select(Organization.id).where(
        Organization.is_platform.is_(False),
        Organization.status.notin_(["offboarded", "deleted"]),
    ).order_by(Organization.id)).all()
    written = 0
    for org_id in orgs:
        try:
            snapshot(db, org_id, day)
            written += 1
        except Exception:
            db.rollback()
            log.exception("posture snapshot failed for company %s", org_id)
    return written

"""Security audits that start themselves (AUD.5): when a project is first connected, when its cloud changes, and on a
pushed change. They run one at a time in the background, under the model limits.

A request is recorded as a queued audit at once and the worker takes the oldest. A second request for the same
project and trigger while one waits joins it (its paths are added to the scope), so a burst of cloud changes is one
audit, not ten. The worker reads the repository through the project's GitHub connection into a snapshot, writes the
live map's nodes beside it as the cloud facts agents may read, runs the audit, and records what it found, including
a fix for each confirmed finding that has a proof check. A failure is recorded with its reason, never retried
silently. The audit's progress lines update the row as it goes, so the screens can draw it running.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..audit import store
from ..audit.azure import Client, ModelError
from ..audit.conductor import CHANGE, CHANGE_DIFF, PRIOR_ARCHITECTURE, Run, audit
from ..config import DATA_DIR
from ..connectors import routing
from ..connectors.base import SourceError
from ..connectors.github import REVIEW_CHECK_NAME, GitHub
from ..db import SessionLocal, utcnow
from ..models import Audit, ChangeEvent, Connector, Project
from ..scanning import twin_nodes
from ..scanning.floor import _write
from ..sphere import brief as sphere_brief
from ..sphere import learn as sphere_learn

log = logging.getLogger("whitehats.audit_jobs")

WORK = DATA_DIR / "audits"
TRIGGERS = ("onboarding", "connections", "cloud_change", "code_change", "schedule", "request")
FULL = ("onboarding", "connections", "schedule", "request")  # audits of everything the project's connections reach
WEEK = timedelta(days=7)
BUDGET_USD = {"quick": 15.0, CHANGE: 4.0}
PROFILE = {"code_change": CHANGE}  # a pull request's review is the quick method narrowed to the change
CODE_NODE_TYPES = ("code.", "delivery.github")  # map nodes that describe code, not the live account

_lock = threading.Lock()
# Two queues, each drained one audit at a time: pull request reviews, which someone is waiting on beside their pull
# request, and every other audit (onboarding, cloud change), which can take an hour and must not hold a review up.
REVIEWS, AUDITS = "reviews", "audits"
_workers: dict[str, threading.Thread] = {}


def _queue_of(trigger: str) -> str:
    return REVIEWS if trigger == "code_change" else AUDITS


def request(db: Session, org_id: int, project_id: int, trigger: str, detail: str = "",
            scope: list[str] | None = None, *, change_id: int | None = None, head_sha: str = "",
            pull_number: int | None = None, branch: str = "", repo: str = "") -> Audit:
    """Queue an audit, or join the one already waiting for this project and trigger. A pull request's review
    (code_change) joins only a review of the same pull request still waiting, and moves it to the newest commit."""
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown audit trigger {trigger}")
    if trigger == "code_change":
        # The same pull request number in another repository is another pull request.
        held = db.scalar(select(Audit).where(Audit.org_id == org_id, Audit.project_id == project_id,
                                             Audit.trigger == trigger, Audit.status == "queued",
                                             Audit.pull_number == pull_number, Audit.repo == repo))
        if held is None:
            held = Audit(org_id=org_id, project_id=project_id, trigger=trigger, status="queued", step="queued",
                         profile=PROFILE[trigger], pull_number=pull_number, repo=repo)
            db.add(held)
        held.trigger_detail, held.scope, held.change_id = detail[:2000], sorted(set(scope or [])), change_id
        held.head_sha, held.branch = head_sha, branch
        db.commit()
        _announce(db, held, "queued")
        wake()
        return held
    held = db.scalar(select(Audit).where(Audit.org_id == org_id, Audit.project_id == project_id,
                                         Audit.trigger == trigger, Audit.status == "queued"))
    if held is not None:
        if held.scope and scope:
            held.scope = sorted(set(held.scope) | set(scope))
        elif not scope:
            held.scope = []  # a request for everything widens a scoped one to everything
        held.trigger_detail = "; ".join(x for x in (held.trigger_detail, detail) if x)[:2000]
        db.commit()
        return held
    row = Audit(org_id=org_id, project_id=project_id, trigger=trigger, trigger_detail=detail[:2000],
                scope=sorted(set(scope or [])), status="queued", step="queued",
                connections=connection_prints(db, project_id) if trigger in FULL else {})
    db.add(row)
    db.commit()
    wake()
    return row


# ------------------------------------------------------------------ which connections a full audit read (AUD.6)


def connection_prints(db: Session, project_id: int) -> dict[str, str]:
    """The project's connections by what they are, not by their rows: its AWS account and role, and each of its GitHub
    repositories on the map. Connecting the same ones again gives the same prints."""
    from ..services.coverage import map_nodes

    out: dict[str, str] = {}
    for connector in db.scalars(select(Connector).where(Connector.key.in_(("aws", "github")),
                                                        Connector.status.in_(("checked", "reading", "verified")))):
        if project_id not in [pid for pid, _ in routing.attachments(db, connector)]:
            continue
        if connector.key == "aws":
            role = str((connector.config or {}).get("role_arn") or "").strip()
            if role:
                parts = role.split(":")
                account = parts[4] if len(parts) > 4 else "?"
                out[f"aws:{role}"] = f"AWS account {account} (role {role.rsplit('/', 1)[-1]})"
    for node in map_nodes(db, project_id):
        if str(node.type).split("@", 1)[0] == "repo.github":
            name = str((node.properties or {}).get("repo") or node.name)
            out[f"github:{name.lower()}"] = f"GitHub repository {name}"
    return out


def last_full(db: Session, project_id: int) -> Audit | None:
    """The newest full audit that finished (complete, or incomplete with its findings); a failed one does not count."""
    return db.scalar(select(Audit).where(Audit.project_id == project_id, Audit.trigger.in_(FULL),
                                         Audit.status.in_(("complete", "incomplete")))
                     .order_by(Audit.finished_at.desc(), Audit.id.desc()).limit(1))


def _waiting_full(db: Session, project_id: int) -> Audit | None:
    return db.scalar(select(Audit).where(Audit.project_id == project_id, Audit.trigger.in_(FULL),
                                         Audit.status.in_(("queued", "running", "cancelling"))).limit(1))


def _ago(when) -> str:
    days = (utcnow() - when).days
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} ago"
    hours = int((utcnow() - when).total_seconds() // 3600)
    return f"{hours} hour{'s' if hours != 1 else ''} ago" if hours else "less than an hour ago"


def after_connections(db: Session, project: Project, *, connected: bool) -> Audit | None:
    """Decide, after the project's map was read, whether its connections need a full audit (AUD.6).

    Something the last finished full audit did not read (another AWS account or role, a repository it did not see)
    starts one at once. The same connections, or fewer, start nothing: the weekly audit comes 7 days after the last.
    When a person has just connected (`connected`), a skip is recorded with the reason, so the screens can say so.
    Returns the audit queued or the skip recorded, or None when nothing was said."""
    now = connection_prints(db, project.id)
    if not any(key.startswith("github:") for key in now):
        return None  # an audit reads code: it waits until the project's code is connected
    if _waiting_full(db, project.id) is not None:
        return None
    last = last_full(db, project.id)
    if last is not None and not last.connections:
        last.connections = now  # finished before audits remembered their connections: it read what is there now
        db.commit()
    new = {key: words for key, words in now.items() if last is None or key not in (last.connections or {})}
    if new:
        trigger = "onboarding" if last is None else "connections"
        detail = ("The project's code is connected: a first full audit." if last is None else
                  "Not read by the last full audit: " + "; ".join(sorted(new.values())))
        return request(db, project.org_id, project.id, trigger, detail=detail)
    if not connected:
        return None
    when = last.finished_at or last.created_at
    nxt = (when + WEEK).strftime("%A %d %B") if project.weekly_audits else ""
    message = (f"An audit with these same connections finished {_ago(when)}, so this one is skipped. "
               + (f"The next weekly audit is on {nxt}." if nxt else "Weekly audits are switched off for this project."))
    row = Audit(org_id=project.org_id, project_id=project.id, trigger="connections", trigger_detail=message,
                status="skipped", step="", connections=now, started_at=utcnow(), finished_at=utcnow())
    db.add(row)
    db.commit()
    return row


def weekly_due(db: Session) -> int:
    """A full audit for each project whose weekly audits are on, 7 days after its last full one finished. Returns how
    many were queued."""
    queued = 0
    for project in db.scalars(select(Project).where(Project.archived.is_(False), Project.weekly_audits.is_(True))):
        last = last_full(db, project.id)
        if last is None or _waiting_full(db, project.id) is not None:
            continue  # never audited: the first one starts when its code is connected
        if utcnow() - (last.finished_at or last.created_at) < WEEK:
            continue
        if not any(key.startswith("github:") for key in connection_prints(db, project.id)):
            continue
        request(db, project.org_id, project.id, "schedule", detail="The weekly full audit.")
        queued += 1
    return queued


def enabled() -> bool:
    """Audits run only where the Azure models are configured; elsewhere a request waits, queued and visible."""
    from . import local_workspace

    return not local_workspace.ENABLED and bool(
        os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY")
    )


def resume_after_restart(db: Session) -> int:
    """At start-up: an audit that was running when the server stopped goes back in the queue (its finished agents are
    saved in its run folder, so it picks up where it was), and the queue is woken, so nothing waits for a new request.
    Returns how many audits were put back."""
    stopped = list(db.scalars(select(Audit).where(Audit.status == "running")))
    for row in stopped:
        row.status, row.step = "queued", "queued"
    db.commit()
    if db.scalar(select(Audit.id).where(Audit.status == "queued").limit(1)) is not None:
        wake()
    return len(stopped)


_asked: set[str] = set()  # queues woken while their worker was finishing: it looks again instead of stopping


def wake() -> None:
    """Make sure each queue has a worker. A worker that is about to stop because it found nothing queued is told to
    look once more, so an audit queued at that moment is never left waiting for the next restart."""
    if not enabled():
        return
    with _lock:
        for queue in (REVIEWS, AUDITS):
            worker = _workers.get(queue)
            if worker is None or not worker.is_alive():
                _workers[queue] = threading.Thread(target=_drain, args=(queue,), name=f"whitehats-{queue}", daemon=True)
                _workers[queue].start()
            else:
                _asked.add(queue)


def _drain(queue: str = AUDITS) -> None:
    reviews = Audit.trigger == "code_change"
    while True:
        with SessionLocal() as db:
            next_id = db.scalar(select(Audit.id).where(Audit.status == "queued", reviews if queue == REVIEWS else ~reviews)
                                .order_by(Audit.id).limit(1))
        if next_id is None:
            with _lock:
                if queue in _asked:  # woken while looking: look again
                    _asked.discard(queue)
                    continue
                _workers.pop(queue, None)
                return
        run_one(next_id)


def keep_going(db: Session) -> None:
    """Once a minute: anything queued with no worker on it is started (a safety net under wake)."""
    if db.scalar(select(Audit.id).where(Audit.status == "queued").limit(1)) is not None:
        wake()


_checked: dict[int, tuple[float, bool]] = {}


def _cancelled(audit_id: int) -> bool:
    """Whether a person cancelled this audit (asked at most every 3 seconds, from any agent thread)."""
    import time

    at, answer = _checked.get(audit_id, (0.0, False))
    if answer or time.time() - at < 3:
        return answer
    with SessionLocal() as db:
        answer = db.scalar(select(Audit.status).where(Audit.id == audit_id)) == "cancelling"
    _checked[audit_id] = (time.time(), answer)
    return answer


def cancel(db: Session, row: Audit) -> Audit:
    """AUD.7: a queued audit is cancelled at once; a running one stops at its next step and says so."""
    if row.status == "queued":
        row.status, row.step, row.finished_at = "cancelled", "cancelled", utcnow()
    elif row.status == "running":
        row.status = "cancelling"
    else:
        raise ValueError("This audit is not running or waiting, so there is nothing to cancel.")
    db.commit()
    return row


def run_one(audit_id: int) -> None:
    with SessionLocal() as db:
        row = db.get(Audit, audit_id)
        if row is None or row.status != "queued":
            return
        row.status, row.step, row.started_at = "running", "reading the code", utcnow()
        db.commit()
        try:
            _run(db, row)
        except Exception as exc:
            if _cancelled(audit_id):
                db.rollback()
                stopped = db.get(Audit, audit_id)
                stopped.status, stopped.step, stopped.finished_at = "cancelled", "cancelled", utcnow()
                stopped.error = "Cancelled by a person while it ran."
                db.commit()
                _checked.pop(audit_id, None)
                if stopped.trigger == "code_change":
                    _announce(db, stopped, "cancelled")
                return
            if not isinstance(exc, (ModelError, OSError, RuntimeError, ValueError)):
                raise
            log.exception("audit %s failed", audit_id)
            db.rollback()
            failed = db.get(Audit, audit_id)
            if failed is not None:
                failed.status, failed.step, failed.error = "failed", "failed", str(exc)[:2000]
                failed.finished_at = utcnow()
                db.commit()


def _run(db: Session, row: Audit) -> None:
    found = _repository(db, row)
    if found is None:
        raise RuntimeError("No GitHub repository is connected to this project, so there is no code to audit.")
    source, repo = found
    full, branch = str(repo["full_name"]), str(repo.get("default_branch") or "")
    run_dir = WORK / f"{row.org_id}-{row.project_id}-{row.id}"
    target = run_dir / "target"
    target.mkdir(parents=True, exist_ok=True)
    reviewing = row.trigger == "code_change" and bool(row.head_sha)
    change = None
    if reviewing:
        _announce(db, row, "in_progress", source=source)
        # The pull request's own commit, its diff against what it is merged into, and the map of an earlier audit.
        change = db.get(ChangeEvent, row.change_id) if row.change_id else None
        base = (change.base if change and change.base else "") or branch
        written, _ = _write(source.repository_files(repo, ref=row.head_sha), target)
        (run_dir / CHANGE_DIFF).write_text(source.compare_diff(full, base, row.head_sha))
        prior = _prior_architecture(db, row)
        if prior:
            (run_dir / PRIOR_ARCHITECTURE).write_text(prior)
        branch = row.branch or branch
    else:
        written, _ = _write(source.repository_files(repo), target)
    (run_dir / "cloud-facts.json").write_text(json.dumps(_cloud_facts(db, row.project_id)))
    # KS.1: the business rules and accepted exceptions approvers confirmed, around the changed files for a review
    known = sphere_brief.brief(db, row.project_id, list(change.files or []) if reviewing and change else None)
    if known:
        (run_dir / sphere_brief.FILE).write_text(known)
    row.repo, row.branch, row.run_dir = full, branch, str(run_dir)
    at = f"{branch} {row.head_sha[:7]}" if reviewing else branch
    row.source_ref = f"{full}@{at} ({len(written)} files)"[:200]
    db.commit()
    audit_id = row.id

    def progress(entry: dict) -> None:
        with SessionLocal() as other:
            live = other.get(Audit, audit_id)
            if live is not None:
                live.step = str(entry.get("step", ""))[:20]
                other.commit()

    job = Run(target=target, out=run_dir, repo=full, source_ref=row.source_ref, profile=row.profile,
              budget_usd=BUDGET_USD.get(row.profile, 15.0), scope=list(row.scope or []),
              scope_reason=row.trigger_detail if row.trigger in ("cloud_change", "code_change") or row.scope else "",
              listener=progress, cancelled=lambda: _cancelled(audit_id))
    client = Client(run_dir / "usage.jsonl")
    client.stop = lambda: _cancelled(audit_id)
    audit(job, client)
    db.refresh(row)
    store.import_run(db, row.org_id, row.project_id, run_dir, row.trigger, full, branch=branch,
                     trigger_detail=row.trigger_detail, audit=row)
    if row.trigger == "onboarding" and row.status in ("complete", "incomplete"):
        # KS.1: the first audit proposes what the business depends on; nothing is known until an approver confirms
        sphere_learn.note(db, org_id=row.org_id, project_id=row.project_id, kind="propose", ref=f"audit:{row.id}",
                          actor="WhiteHats", payload={"audit_id": row.id})
        db.commit()
    if reviewing:
        publish_review(db, row, source)


def _prior_architecture(db: Session, row: Audit) -> str:
    """The architecture the newest finished full audit of this project mapped, for a pull request's review to start
    from. Empty when there is none: the review then maps the code itself, as a full audit does."""
    for earlier in db.scalars(select(Audit).where(Audit.project_id == row.project_id, Audit.id != row.id,
                                                  Audit.trigger != "code_change",
                                                  Audit.status.in_(("complete", "incomplete")))
                              .order_by(Audit.id.desc())):
        path = Path(earlier.run_dir or "") / "architecture.md"
        if earlier.run_dir and path.is_file():
            return path.read_text()
    return ""


def review_output(records: list[dict], status: str) -> tuple[str, str, str, list[dict], list[dict]]:
    """The "WhiteHats AI review" check for a pull request: conclusion, title, summary, annotations, and one inline
    comment per confirmed weakness. It reports, it does not block: a confirmed weakness ends the check as neutral."""
    confirmed = [r for r in records if r.get("verdict") == "confirmed"]
    unsure = [r for r in records if r.get("verdict") == "needs_validation"]
    annotations, comments, lines = [], [], []
    for r in confirmed:
        sink = (r.get("trace") or [{}])[-1]
        severity = str((r.get("severity") or {}).get("overall_severity") or "medium")
        text = f"{r.get('description', '')}\n\nWhy: {r.get('root_cause', '')}".strip()
        lines.append(f"- **{r.get('title')}** ({severity}), `{sink.get('file')}:{sink.get('line')}`. {r.get('description', '')}")
        if sink.get("file") and sink.get("line"):
            annotations.append({"path": sink["file"], "start_line": int(sink["line"]), "end_line": int(sink["line"]),
                                "annotation_level": "failure" if severity in ("critical", "high") else "warning",
                                "title": str(r.get("title"))[:255], "message": text[:60000]})
            comments.append({"key": f"review:{r.get('fingerprint')}", "path": sink["file"], "line": int(sink["line"]),
                             "body": f"**WhiteHats AI review: {r.get('title')}** ({severity})\n\n{text}\n\n"
                                     "A separate agent confirmed this by triggering it in a sealed container with "
                                     "dummy data. The fix is waiting in WhiteHats."})
    if confirmed:
        title = f"{len(confirmed)} security weakness{'es' if len(confirmed) > 1 else ''} confirmed in this change"
        summary = ("WhiteHats audited what this pull request changes. Each weakness below was confirmed by a separate "
                   "agent that triggered it in a sealed container.\n\n" + "\n".join(lines))
        conclusion = "neutral"
    else:
        title = "No security weakness confirmed in this change"
        summary = "WhiteHats audited what this pull request changes and confirmed no security weakness."
        conclusion = "success"
    if unsure:
        summary += f"\n\n{len(unsure)} more lead(s) need a person to check; they are in WhiteHats."
    if status == "incomplete":
        summary += "\n\nThe review did not finish every part it planned; what it did not cover is listed in WhiteHats."
    return conclusion, title, summary, annotations, comments


PROGRESS = {
    "queued": ("Waiting to start", ("WhiteHats will audit what this pull request changes: it plans around the changed "
                                   "files, sends agents to look for security weaknesses, and has a separate agent try "
                                   "to disprove each one in a sealed container before anything is reported.")),
    "in_progress": ("Reviewing the change", ("WhiteHats is auditing what this pull request changes. Agents are looking "
                                            "for security weaknesses, and a separate agent tries to disprove each one "
                                            "in a sealed container. This usually takes 3 to 15 minutes.")),
    "cancelled": ("Cancelled", ("A person cancelled this review in WhiteHats before it finished. Push again, or ask "
                                "for an audit, to review the change.")),
}


def _announce(db: Session, row: Audit, status: str, source: GitHub | None = None) -> None:
    """Show the review on the pull request before it has a result: queued, then in progress. Never fatal."""
    try:
        if source is None:
            found = _repository(db, row)
            if found is None:
                return
            source = found[0]
        title, summary = PROGRESS[status]
        repo = row.repo or _repo_of_change(db, row)
        if not repo or not row.head_sha:
            return
        done = status == "cancelled"
        source.publish_check(repo, row.head_sha, status="completed" if done else status, title=title, summary=summary,
                             annotations=[], name=REVIEW_CHECK_NAME, conclusion="cancelled" if done else "")
    except (SourceError, RuntimeError) as exc:
        log.warning("audit %s: the review's %s check could not be written: %s", row.id, status, exc)


def _repo_of_change(db: Session, row: Audit) -> str:
    change = db.get(ChangeEvent, row.change_id) if row.change_id else None
    return change.repo if change else ""


def publish_review(db: Session, row: Audit, source: GitHub) -> None:
    """Write the review's check on the pull request's commit, and its inline comments. The findings already stand in
    WhiteHats; a refusal from GitHub is logged, never fatal."""
    path = Path(row.run_dir) / "findings.json"
    records = json.loads(path.read_text()) if path.is_file() else []
    conclusion, title, summary, annotations, comments = review_output(records, row.status)
    try:
        row.check_url = source.publish_check(row.repo, row.head_sha, conclusion=conclusion, title=title,
                                             summary=summary, annotations=annotations, name=REVIEW_CHECK_NAME)
        if comments and row.pull_number:
            source.comment_findings(row.repo, row.pull_number, row.head_sha, comments)
    except SourceError as exc:
        log.warning("audit %s: the review could not be written to GitHub: %s", row.id, exc)
    db.commit()


def _repository(db: Session, row: Audit) -> tuple[GitHub, dict] | None:
    """The repository to read: a pull request's own (its review must never read another repository of the project),
    else the first a GitHub connection of this company routes to this project."""
    wanted = (row.repo or "").lower()
    if not wanted and row.change_id:
        change = db.get(ChangeEvent, row.change_id)
        wanted = (change.repo if change else "").lower()
    for connector in db.scalars(select(Connector).where(Connector.org_id == row.org_id, Connector.key == GitHub.key)
                                .order_by(Connector.id)):
        source = connectors.for_row(db, connector)
        if not isinstance(source, GitHub) or connector.status == "available":
            continue
        for repo in source.repositories():
            full = str(repo["full_name"])
            projects = routing.projects_for(db, connector, routing.Resource(
                identity=str(repo.get("html_url") or full), repo=full))
            if row.project_id in projects and (not wanted or full.lower() == wanted):
                return source, repo
    return None


def _cloud_facts(db: Session, project_id: int) -> list[dict]:
    """The map's live nodes (not its code nodes), as agents may read them through cloud_facts."""
    return [{"id": n.get("id"), "type": n.get("type"), "name": n.get("name"), "properties": n.get("properties") or {}}
            for n in twin_nodes(db, [project_id])
            if not str(n.get("type") or "").startswith(CODE_NODE_TYPES)]

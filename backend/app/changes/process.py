"""Judge each kept change: read its diff and changed files, hand them to the Decisions API, keep the answer.

Steps after this one (model confirmation, live signals, the check on the pull request) read the stored
changes; they are VS.3 and VS.4. Each runs in the background, so a webhook is answered at once.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import connectors, scanning
from ..confirmation import confirm, gateway_ask
from ..confirmation.confirm import Knowledge
from ..connectors.base import SourceError
from ..connectors.github import GitHub
from ..db import SessionLocal, utcnow
from ..decisions_api import decide
from ..decisions_api.suspect import NOT_AFFECTED, Acceptance
from ..models import ChangeEvent, Connector
from ..registry import statements as registry_statements
from ..scanning import planner, vex
from . import findings
from .fetch import fetched

log = logging.getLogger("whitehats.changes")

MAX_ATTEMPTS = 3  # a read that fails is tried again on the next pass, up to this many times
STALE_WORKING_S = 600  # a change still being judged after this long belongs to a process that died
RETRY_WAIT_S = 30  # a failed read waits this long, times the tries so far, so a short outage is ridden out
PROCESS_EVERY_S = 10  # how often the worker looks for changes the poll kept
JUDGED = ("decided", "incomplete")
COMMENT_WITHIN_DAYS = 7  # a pull request opened more than this long after its commit was judged gets no late comments


def process_pending(db: Session, *, decider: Callable | None = None, confirmer: Callable | None = None) -> int:
    """Judge every change waiting, newest commit per branch only. Returns how many were judged.

    `decider(root, diff)` is the Decisions API, and `confirmer(decision, root, diff)` is the confirmation step;
    both can be replaced, so a test needs neither engines nor a model."""
    _requeue_stale(db)
    waiting = db.scalars(select(ChangeEvent).where(ChangeEvent.status == "pending").order_by(ChangeEvent.id)).all()
    now = utcnow()
    judged = 0
    for change in _newest_per_branch(db, waiting):
        if _is_resting(change, now):
            continue
        if _claim(db, change):
            _judge(db, change, decider or decide, confirmer)
            judged += 1
    comment_late_pull_requests(db)
    return judged


def comment_late_pull_requests(db: Session) -> int:
    """Comment on, and ask the AI review of, pull requests opened after their commit was judged: the push came first,
    the pull request later."""
    since = utcnow() - timedelta(days=COMMENT_WITHIN_DAYS)
    rows = db.scalars(select(ChangeEvent).where(
        ChangeEvent.status.in_(JUDGED), ChangeEvent.pull_number.is_not(None), ChangeEvent.commented_at.is_(None),
        ChangeEvent.comment_error == "", ChangeEvent.decided_at >= since)).all()
    for change in rows:
        try:
            source = connectors.for_row(db, db.get(Connector, change.connector_id))
            if isinstance(source, GitHub):
                _comment(db, change, source, set())
                _review(db, change)
        except SourceError as exc:
            change.comment_error = str(exc)[:500]
            db.commit()
    return len(rows)


def process_pending_in_new_session() -> None:
    """For a background task: the request's own session is closed by the time this runs."""
    with SessionLocal() as db:
        process_pending(db)


def _newest_per_branch(db: Session, waiting: list[ChangeEvent]) -> list[ChangeEvent]:
    """The newest change of each branch. The older ones are covered by its diff, which starts before them."""
    newest: dict[tuple[int, str, str], ChangeEvent] = {}
    for change in waiting:  # oldest first
        key = (change.connector_id, change.repo, change.branch)
        older = newest.get(key)
        if older is not None:
            change.first_sha = older.first_sha
            older.status, older.error = "superseded", f"Covered by the newer commit {change.head_sha[:7]}."
        newest[key] = change
    db.commit()
    return list(newest.values())


def _is_resting(change: ChangeEvent, now: datetime) -> bool:
    """True while a change whose read failed waits before its next try."""
    return change.claimed_at is not None and now - change.claimed_at < timedelta(seconds=RETRY_WAIT_S * change.attempts)


def _claim(db: Session, change: ChangeEvent) -> bool:
    """Take the change for this process only. False when another process took it first."""
    taken = db.execute(update(ChangeEvent).where(ChangeEvent.id == change.id, ChangeEvent.status == "pending")
                       .values(status="working", attempts=ChangeEvent.attempts + 1, error="", claimed_at=utcnow())).rowcount
    db.commit()
    db.refresh(change)
    return taken == 1


def _requeue_stale(db: Session) -> None:
    cutoff = utcnow() - timedelta(seconds=STALE_WORKING_S)
    db.execute(update(ChangeEvent).where(ChangeEvent.status == "working", ChangeEvent.claimed_at < cutoff)
               .values(status="pending"))
    db.commit()


def _judge(db: Session, change: ChangeEvent, decider: Callable, confirmer: Callable | None) -> None:
    started = time.monotonic()
    try:
        source = connectors.for_row(db, db.get(Connector, change.connector_id))
        if not isinstance(source, GitHub):
            raise SourceError("This change belongs to a connection that is not GitHub.")
        _read_and_decide(db, change, source, decider, confirmer)
    except SourceError as exc:
        _give_up_or_retry(db, change, str(exc))
    except Exception as exc:  # one bad change must not stop the rest
        log.exception("github change failed repo=%s head=%s", change.repo, change.head_sha[:7])
        _give_up_or_retry(db, change, f"WhiteHats could not judge this change ({type(exc).__name__}).")
    else:
        log.info("github change judged repo=%s head=%s status=%s verdict=%s files=%d seconds=%.1f",
                 change.repo, change.head_sha[:7], change.status, change.verdict, len(change.files),
                 time.monotonic() - started)


def _read_and_decide(db: Session, change: ChangeEvent, source: GitHub, decider: Callable,
                     confirmer: Callable | None) -> None:
    last_error: SourceError | None = None
    for base in _bases(db, change):
        try:
            with fetched(source, change.repo, base, change.head_sha) as read:
                change.base, change.fetched_at = base, utcnow()
                change.files, change.skipped, change.diff_bytes = read.files, read.skipped, len(read.diff.encode())
                _map_due(db, change)
                _sphere_check_due(db, change)
                db.commit()
                planned = _plan(db, change, read)
                decision = decider(read.root, read.diff, planned=planned, acceptances=_not_affected(db, change))
                confirmations = (confirmer or _gateway_confirmer(db, change))(decision, read.root, read.diff)
                whole, supplier = _whole_files(db, change, read, decider)
                removed = findings.deleted_paths(read.diff)
        except SourceError as exc:  # the base may be gone (a force push); the next one still tells the story
            last_error = exc
            continue
        change.decision, change.verdict, change.decided_at = decision.to_dict(), decision.verdict, utcnow()
        change.confirmations = [item.to_dict() for item in confirmations]
        change.status = "incomplete" if any(run.error for run in decision.engines) else "decided"
        db.commit()
        before = findings.open_keys(db, change)
        findings.apply(db, change, still_present=whole, deleted=removed, not_affected=supplier)
        for project_id in change.project_ids or []:
            vex.apply(db, project_id)
        _write_check(db, change, source)
        _comment(db, change, source, before - findings.open_keys(db, change))
        _review(db, change)
        return
    raise last_error or SourceError("GitHub gave no way to compare this change.")


FIX_FACTORY_BRANCH = "fix-factory/"  # Fix Factory's own pull requests: its second-model review already covers them


def _review(db: Session, change: ChangeEvent) -> None:
    """Queue the AI review of a pull request's change (AUD.5): a security audit narrowed to what it changes, after the
    scanners' check. It runs in the background and writes its own "WhiteHats AI review" check when done."""
    from ..services import audit_jobs  # the audit pulls in the scanners; imported here, not at start-up

    if not change.pull_number or not change.files or not change.project_ids or not audit_jobs.enabled():
        return
    if change.branch.startswith(FIX_FACTORY_BRANCH):
        return
    from ..models import Audit

    if db.scalar(select(Audit.id).where(Audit.change_id == change.id).limit(1)) is not None:
        return  # this commit's review was asked for already
    try:
        audit_jobs.request(db, change.org_id, change.project_ids[0], "code_change",
                           detail=f"Pull request #{change.pull_number} in {change.repo}: {change.branch} at "
                                  f"{change.head_sha[:7]}",
                           scope=list(change.files), change_id=change.id, head_sha=change.head_sha,
                           pull_number=change.pull_number, branch=change.branch, repo=change.repo)
    except Exception:  # the scanners' check already stands; a review that cannot be queued is logged
        log.exception("could not queue the review of pull request %s in %s", change.pull_number, change.repo)


def _not_affected(db: Session, change: ChangeEvent) -> list:
    """The VEX statements in force on this repository's findings (SCAN.5): such a finding is still reported, marked not
    affected, and stops escalating, so it no longer fails the check."""
    found = []
    for project_id in change.project_ids or []:
        for row in vex.in_force(db, project_id, utcnow()):
            parts = row.fingerprint.split("|")
            if row.status == NOT_AFFECTED and len(parts) == 3 and parts[0] != "image":
                found.append(Acceptance(parts[0], parts[1], parts[2], vex.words(row), status=NOT_AFFECTED))
    return [*found, *registry_statements.for_company(db, change.org_id)]  # the company's fixed builds (REG.1)


def _plan(db: Session, change: ChangeEvent, read) -> dict[str, list[str]] | None:
    """The scan plan for this push (SCAN.1), checked and kept; {scanner: files} for the decision. None when the push
    changed no file this run can read: then nothing is planned and the decision has nothing to scan either."""
    if not read.files:
        return None
    nodes = scanning.twin_nodes(db, list(change.project_ids or []))
    scope = scanning.Scope(files={change.repo: frozenset(read.files)}, images=scanning.twin_images(nodes))
    data = planner.for_push(change.repo, change.branch, change.head_sha, read.files, nodes)
    record, plan = scanning.store(db, org_id=change.org_id, project_ids=list(change.project_ids or []), kind="push",
                                  repo=change.repo, data=data, scope=scope, fallback="every changed file",
                                  change_id=change.id)
    change.scan_plan_id = record.id
    scanning.request_images(db, plan.images(), utcnow)
    return plan.paths_by_scanner(change.repo)


def _gateway_confirmer(db: Session, change: ChangeEvent) -> Callable:
    """Confirm with the company's own models through the gateway; with none connected, findings stay unconfirmed."""
    ask = gateway_ask(db, change.org_id)
    knowledge = Knowledge.of(db)  # advisory facts for dependency findings, and a check on any id the model names (KB.1)
    return lambda decision, root, diff: confirm(decision, root, ask, diff=diff, knowledge=knowledge)


def _whole_files(db: Session, change: ChangeEvent, read, decider: Callable) -> tuple[set[str] | None, set[str]]:
    """What a whole-file check of the touched files still finds, when an open finding sits in one of them, and which
    of those a supplier's word marks not affected (a WhiteHats Registry build, REG.1).

    The diff check reports only changed lines, so a weakness left alone in an unchanged line would look fixed.
    None means no check was needed, or it could not run completely: nothing is closed then.
    """
    if not findings.open_paths(db, change) & set(read.files):
        return None, set()
    whole = decider(read.root, None, acceptances=_not_affected(db, change))
    if any(run.error for run in whole.engines):
        return None, set()
    # Present is present: a weakness someone accepted or said does not apply is still there, so it is not fixed.
    present = {findings.fingerprint(suspect.to_dict()) for suspect in whole.suspects if suspect.is_above_low}
    supplier = {findings.fingerprint(suspect.to_dict()) for suspect in whole.suspects if suspect.status == NOT_AFFECTED}
    return present, supplier


def _write_check(db: Session, change: ChangeEvent, source: GitHub) -> None:
    """The "WhiteHats" check run on the commit. A refusal is kept as a reason; the findings are already written."""
    conclusion, title, summary, annotations = findings.check_output(change)
    try:
        change.check_url, change.check_error = source.publish_check(
            change.repo, change.head_sha, conclusion=conclusion, title=title, summary=summary,
            annotations=annotations), ""
    except SourceError as exc:
        change.check_error = str(exc)[:500]
    except Exception as exc:  # the findings stand even when GitHub cannot be written to
        log.exception("github check run failed repo=%s head=%s", change.repo, change.head_sha[:7])
        change.check_error = f"The check run was not written ({type(exc).__name__})."
    db.commit()


_PULL_REQUEST_LOCKS: dict[tuple[str, int], threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _pull_request_lock(repo: str, number: int) -> threading.Lock:
    """One pull request is commented on by one thread at a time: comment_findings reads the comments already there and
    then posts the missing ones, so two passes at once both found none and both posted (seen live on #12)."""
    with _LOCKS_GUARD:
        return _PULL_REQUEST_LOCKS.setdefault((repo, int(number)), threading.Lock())


def _comment(db: Session, change: ChangeEvent, source: GitHub, closed: set[str]) -> None:
    """Inline comments on the pull request for what is open, and an answer on each comment whose finding closed.

    A push to a pull request's branch often arrives before GitHub says which pull request it belongs to, so the
    number is taken from an earlier change on the same branch when this one has none. No pull request: nothing.
    """
    number = change.pull_number or db.scalar(select(ChangeEvent.pull_number).where(
        ChangeEvent.connector_id == change.connector_id, ChangeEvent.repo == change.repo,
        ChangeEvent.branch == change.branch, ChangeEvent.pull_number.is_not(None)).order_by(ChangeEvent.id.desc()))
    if number is None:
        return
    try:
        with _pull_request_lock(change.repo, number):  # the push's own pass and the late pass may both arrive at once
            added = source.comment_findings(change.repo, number, change.head_sha, findings.pull_request_comments(change))
            answered = source.reply_fixed(change.repo, number, sorted(closed), findings.fixed_text(change))
        change.pull_number, change.commented_at, change.comment_error = number, utcnow(), ""
        log.info("github pull request comments repo=%s pull=%s added=%d answered=%d", change.repo, number, added, answered)
    except SourceError as exc:
        change.comment_error = str(exc)[:500]
    except Exception as exc:  # the findings and the check stand even when a comment cannot be written
        log.exception("github pull request comment failed repo=%s head=%s", change.repo, change.head_sha[:7])
        change.comment_error = f"The pull request comments were not written ({type(exc).__name__})."
    db.commit()


def _bases(db: Session, change: ChangeEvent) -> list[str]:
    """What to compare against, best first: the last commit judged on this branch, then where the branch began."""
    judged = db.scalar(select(ChangeEvent.head_sha).where(
        ChangeEvent.connector_id == change.connector_id, ChangeEvent.repo == change.repo,
        ChangeEvent.branch == change.branch, ChangeEvent.status.in_(JUDGED), ChangeEvent.id != change.id)
        .order_by(ChangeEvent.decided_at.desc()).limit(1))
    began = f"{change.first_sha}~1" if change.branch == change.default_branch else change.default_branch
    return [base for base in (judged, began) if base]


def _give_up_or_retry(db: Session, change: ChangeEvent, message: str) -> None:
    change.error = message
    change.status = "pending" if change.attempts < MAX_ATTEMPTS else "failed"
    db.commit()
    log.warning("github change not read repo=%s head=%s attempt=%d: %s", change.repo, change.head_sha[:7],
                change.attempts, message)


def _sphere_check_due(db: Session, change: ChangeEvent) -> None:
    """A push to the default branch that removes or renames files may take away what the knowledge sphere cites (KS.1):
    ask for its citations to be checked. A pull request never changes the sphere."""
    from ..sphere import learn as sphere_learn

    if change.branch != change.default_branch or not change.files:
        return
    for project_id in change.project_ids or []:
        sphere_learn.note(db, org_id=change.org_id, project_id=int(project_id), kind="check", ref=f"push:{change.id}",
                          actor="WhiteHats")


def _map_due(db: Session, change: ChangeEvent) -> None:
    """A push to the default branch that changes how the estate is built asks for the map to be read again (STUDIO.6)."""
    from ..services import auto_map  # the map reads the connections, which read changes; imported here

    why = auto_map.touched_the_build(change.files or []) if change.branch == change.default_branch else ""
    connector = db.get(Connector, change.connector_id) if why else None
    if connector is not None:
        auto_map.mark_due(connector, why)

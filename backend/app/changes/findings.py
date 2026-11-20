"""VS.4: what a judged change shows people. Confirmed findings become live signals, and a "WhiteHats" check run
says the same on the commit. A finding closes by itself when a later commit removes the weakness.

A finding is matched across commits by a fingerprint (rule, file, resource) on its branch, not by line number, so
the same weakness in a later commit is the same finding and a moved line does not open a second one.

What closes a finding, and what does not:
- a later commit on the same branch that touches the file and no longer has the weakness (a whole-file check,
  because the diff check only reports changed lines, and a weakness left alone in an untouched line is still there);
- a later commit that deletes the file;
- the branch itself being deleted (a pull request branch, after the pull request is closed and cleaned up).
A change that could not be judged completely (an engine did not run) never closes a finding because it is absent:
an engine that did not run cannot say a weakness is gone. A deleted file or branch closes its findings regardless.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from ..db import utcnow
from ..decisions_api.suspect import NOT_AFFECTED
from ..models import ChangeEvent, Project, Signal

log = logging.getLogger("whitehats.changes")

SOURCE = "whitehats:code"
FIXED = "fixed by a later commit"
FILE_REMOVED = "the file was removed"
BRANCH_DELETED = "the branch was deleted"
LEVEL = {"critical": "failure", "high": "failure", "medium": "warning", "low": "notice"}
FAILING = frozenset({"critical", "high"})
SUMMARY_LINES = 25  # findings written out in a check run's summary; the annotations carry up to 50 more


def fingerprint(suspect: dict) -> str:
    """The same weakness in the same file is one finding, whatever line it has moved to."""
    evidence = suspect["evidence"]
    return "|".join((suspect["rule_id"], evidence["path"], evidence.get("resource", "")))[:240]


def repo_url(repo: str) -> str:
    return f"https://github.com/{repo}"


def commit_url(repo: str, sha: str) -> str:
    return f"{repo_url(repo)}/commit/{sha}"


def open_items(confirmations: list[dict]) -> list[dict]:
    """The confirmations a person still sees as findings: everything but a medium or low the model rejected."""
    return [item for item in confirmations if item.get("status") != "rejected"]


def deleted_paths(diff: str) -> set[str]:
    """Files a diff removes: a `--- a/<path>` line directly followed by `+++ /dev/null`."""
    lines = diff.splitlines()
    gone = set()
    for index, line in enumerate(lines[:-1]):
        if line.startswith("--- a/") and lines[index + 1].startswith("+++ /dev/null"):
            gone.add(line[len("--- a/"):])
    return gone


def open_keys(db: Session, change: ChangeEvent) -> set[str]:
    """The fingerprints of the findings still open on this change's branch, in any project."""
    return {row.fingerprint for row in _open_rows(db, change, project_ids=None)}


def pull_request_comments(change: ChangeEvent) -> list[dict]:
    """One inline comment per finding a person still sees: `{key, path, line, body}`, most severe first."""
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items = sorted(open_items(change.confirmations or []), key=lambda item: order.get(item["severity"], 4))
    comments = []
    for item in items:
        suspect = item["suspect"]
        evidence = suspect["evidence"]
        cwe = f", {', '.join(suspect['cwe'])}" if suspect.get("cwe") else ""
        body = (f"**WhiteHats: {suspect['title']}** ({item['severity']}{cwe})\n\n{_explain(item)}\n\n"
                f"Seen in commit `{change.head_sha[:7]}`. This comment is answered when a later commit removes it.")
        comments.append({"key": fingerprint(suspect), "path": evidence["path"],
                         "line": max(int(evidence["end_line"]), int(evidence["start_line"]), 1), "body": body})
    return comments


def fixed_text(change: ChangeEvent) -> str:
    return f"Fixed in commit `{change.head_sha[:7]}`: WhiteHats no longer finds this weakness, and its finding is closed."


def open_paths(db: Session, change: ChangeEvent) -> set[str]:
    """Files that hold an open finding on this change's branch, in the projects that hold the repository."""
    return {_path(row) for row in _open_rows(db, change, project_ids=None)}


def apply(db: Session, change: ChangeEvent, *, still_present: set[str] | None = None, deleted: set[str] | None = None,
          not_affected: set[str] | None = None) -> dict:
    """Write the change's findings as signals and close the ones it removes. Returns counts for the log.

    `still_present` is the fingerprints a whole-file check of the touched files still finds; None means the check
    was not run, and then nothing is closed by absence.
    """
    items = open_items(change.confirmations or [])
    current = {fingerprint(item["suspect"]): item for item in items}
    # Suspects a supplier's word marks not affected (a WhiteHats Registry build, REG.1) never reach confirmation; an
    # open finding they match is marked, not closed: still reported, never counted.
    supplier = {fingerprint(s) for s in (change.decision or {}).get("suspects", []) if s.get("status") == NOT_AFFECTED}
    supplier |= not_affected or set()  # the whole-file check's, for a finding on a line this change did not touch
    touched, gone = set(change.files or []), deleted or set()
    counts = {"opened": 0, "updated": 0, "closed": 0, "marked": 0}
    for project_id in change.project_ids or []:
        if db.get(Project, project_id) is None:
            continue
        rows = {row.fingerprint: row for row in _open_rows(db, change, project_ids=[project_id])}
        for key, item in current.items():
            row = rows.get(key)
            if row is None:
                row = _new_signal(change, project_id, key, item)
                db.add(row)
                db.flush()
                from ..services import fix_jobs  # it reads findings' fingerprints, so it is imported here
                fix_jobs.suggest(db, row, item)  # a high or critical finding is offered as a fix at once (FF.SRV)
                counts["opened"] += 1
            else:
                _refresh(row, change, item)
                counts["updated"] += 1
        for key, row in rows.items():
            if key in current:
                continue
            if key in supplier and row.vex_statement_id is None:
                if row.vex_status != NOT_AFFECTED:
                    row.vex_status = NOT_AFFECTED
                    counts["marked"] += 1
                continue
            path = _path(row)
            if path in gone:  # a deleted file has nothing left to hold the weakness, whatever the engines did
                _close(row, FILE_REMOVED)
            elif change.status == "decided" and still_present is not None and path in touched and key not in still_present:
                _close(row, FIXED)  # an engine that did not run could say nothing is fixed, so only a full run does
            else:
                continue
            counts["closed"] += 1
    db.commit()
    log.info("github findings repo=%s head=%s %s", change.repo, change.head_sha[:7], counts)
    return counts


def close_branch(db: Session, org_id: int, repo: str, branch: str) -> int:
    """A deleted branch takes its findings with it: they were about code that no longer exists on GitHub."""
    rows = db.scalars(select(Signal).where(
        Signal.org_id == org_id, Signal.resource == repo_url(repo), Signal.branch == branch,
        Signal.fingerprint != "", Signal.closed_at.is_(None))).all()
    for row in rows:
        _close(row, BRANCH_DELETED)
    if rows:
        db.commit()
        log.info("github findings closed repo=%s branch=%s count=%d", repo, branch, len(rows))
    return len(rows)


def check_output(change: ChangeEvent) -> tuple[str, str, str, list[dict]]:
    """What the "WhiteHats" check run says: how it ended, a title, a summary, and one annotation per finding."""
    items = open_items(change.confirmations or [])
    incomplete = change.status == "incomplete"
    failing = [i for i in items if i["severity"] in FAILING]
    if failing:
        conclusion = "failure"
    elif items or incomplete:
        conclusion = "neutral"  # something to look at, or a check that could not finish: never a plain pass
    else:
        conclusion = "success"
    if not items:
        title = "WhiteHats could not check everything" if incomplete else "WhiteHats found nothing to review"
    else:
        title = f"WhiteHats found {len(items)} finding{'s' if len(items) != 1 else ''} to review"
    lines = []
    for item in items[:SUMMARY_LINES]:
        suspect = item["suspect"]
        evidence = suspect["evidence"]
        lines.append(f"- **{item['severity']}** {suspect['title']} at `{evidence['path']}:{evidence['start_line']}`"
                     + (f" ({', '.join(suspect['cwe'])})" if suspect.get("cwe") else "")
                     + f": {_explain(item)}")
    if len(items) > SUMMARY_LINES:
        lines.append(f"- and {len(items) - SUMMARY_LINES} more in WhiteHats")
    parts = [f"Commit `{change.head_sha[:7]}` on `{change.branch or 'the default branch'}`.", *lines]
    if incomplete:
        parts.append("Some checks did not run on this change, so this result is not a clean bill of health.")
    if not items and not incomplete:
        parts.append(f"{len(change.files or [])} changed file(s) were read and none holds a finding.")
    annotations = [_annotation(item) for item in items]
    return conclusion, title, "\n\n".join(parts), annotations


def _annotation(item: dict) -> dict:
    suspect = item["suspect"]
    evidence = suspect["evidence"]
    start = max(int(evidence["start_line"]), 1)
    return {"path": evidence["path"], "start_line": start, "end_line": max(int(evidence["end_line"]), start),
            "annotation_level": LEVEL.get(item["severity"], "warning"), "title": suspect["title"][:255],
            "message": _explain(item)[:60000]}


def _explain(item: dict) -> str:
    text = item.get("explanation") or item["suspect"]["title"]
    status = item.get("status")
    if status == "unconfirmed":
        text += " (not confirmed by a model: none answered)"
    elif status == "disputed":
        text += " (a model disagreed; kept at full severity for a person to decide)"
    return text


def _open_rows(db: Session, change: ChangeEvent, *, project_ids: list[int] | None) -> list[Signal]:
    query = select(Signal).where(
        Signal.org_id == change.org_id, Signal.resource == repo_url(change.repo), Signal.branch == change.branch,
        Signal.fingerprint != "", Signal.closed_at.is_(None))
    if project_ids is not None:
        query = query.where(Signal.project_id.in_(project_ids))
    return list(db.scalars(query).all())


def _path(row: Signal) -> str:
    return row.entity.rsplit(":", 1)[0]


def _where(item: dict) -> str:
    evidence = item["suspect"]["evidence"]
    return f"{evidence['path']}:{evidence['start_line']}"


def _detail(change: ChangeEvent, item: dict) -> str:
    suspect = item["suspect"]
    cwe = f" ({', '.join(suspect['cwe'])})" if suspect.get("cwe") else ""
    return (f"{_where(item)}{cwe}. {_explain(item)}\nCommit: {commit_url(change.repo, change.head_sha)}"
            f"\nBranch: {change.branch or 'default'}")


def _new_signal(change: ChangeEvent, project_id: int, key: str, item: dict) -> Signal:
    row = Signal(
        org_id=change.org_id, project_id=project_id, severity=item["severity"], title=item["suspect"]["title"][:240],
        detail=_detail(change, item), entity=_where(item), resource=repo_url(change.repo), source=SOURCE, origin="live",
        fingerprint=key, branch=change.branch, change_id=change.id, scan_plan_id=change.scan_plan_id)
    _mark_supplier(row, item)
    return row


def _refresh(row: Signal, change: ChangeEvent, item: dict) -> None:
    row.severity, row.detail, row.entity, row.change_id = item["severity"], _detail(change, item), _where(item), change.id
    row.scan_plan_id = change.scan_plan_id
    _mark_supplier(row, item)


def _mark_supplier(row: Signal, item: dict) -> None:
    """A scan that found it not affected by a supplier's word (a WhiteHats Registry build, REG.1) marks it so; a
    person's VEX statement (vex.apply) keeps its own mark."""
    if row.vex_statement_id is None:
        row.vex_status = NOT_AFFECTED if item["suspect"].get("status") == NOT_AFFECTED else ""


def _close(row: Signal, reason: str) -> None:
    row.closed_at, row.closed_reason = utcnow(), reason
    session = object_session(row)
    if session is not None:
        from ..services import fix_jobs  # it reads findings' fingerprints, so it is imported here
        fix_jobs.finding_closed(session, row)

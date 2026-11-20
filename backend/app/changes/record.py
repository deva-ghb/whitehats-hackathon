"""Turn what a source read into changes to judge: one per commit, kept once however it was noticed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..connectors import routing
from ..models import ChangeEvent, Connector
from . import findings

log = logging.getLogger("whitehats.changes")

# A pull request is judged when it is opened or its branch moves. A label, a comment or a close adds no code.
PULL_REQUEST_ACTIONS = frozenset({"opened", "reopened", "synchronize", "ready_for_review"})


def record(db: Session, connector: Connector, lines: list[dict], *, source: str = "poll") -> list[ChangeEvent]:
    """Keep a change for each head commit and each opened pull request in `lines` that is not kept yet.

    `source` is how the lines arrived (webhook or poll). Lines for repositories no project holds are
    left alone: nothing is read for code nobody asked WhiteHats to watch. Returns the changes kept now.
    """
    for line in lines:
        if line.get("kind") == "branch" and line.get("repo") and line.get("branch"):
            findings.close_branch(db, connector.org_id, line["repo"], line["branch"])
    kept = [change for group in _push_groups(lines) if (change := _keep(db, connector, group, source))]
    for line in lines:
        if line.get("kind") == "pull_request" and line.get("action") in PULL_REQUEST_ACTIONS:
            change = _keep(db, connector, [line], source)
            if change:
                kept.append(change)
    return kept


def _push_groups(lines: list[dict]) -> list[list[dict]]:
    """Commit lines grouped by repository and branch, in order: a burst of commits is one change."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for line in lines:
        if line.get("kind") == "commit" and line.get("sha"):
            groups.setdefault((line["repo"], line["branch"]), []).append(line)
    return list(groups.values())


def _keep(db: Session, connector: Connector, group: list[dict], source: str) -> ChangeEvent | None:
    """The new change for this group, or None when the head was kept before or no project holds the repository."""
    last = group[-1]
    head = str(last.get("head") or last["sha"])
    existing = db.scalar(select(ChangeEvent).where(
        ChangeEvent.connector_id == connector.id, ChangeEvent.repo == last["repo"], ChangeEvent.head_sha == head))
    if existing is not None:
        _link_pull_request(existing, last)
        return None
    resource = routing.Resource(identity=str(last.get("url") or last["repo"]), repo=last["repo"])
    projects = routing.projects_for(db, connector, resource)
    if not projects:
        return None
    change = ChangeEvent(
        org_id=connector.org_id, connector_id=connector.id, project_ids=projects, repo=last["repo"],
        branch=last.get("branch", ""), default_branch=last.get("default_branch", ""), head_sha=head,
        first_sha=group[0]["sha"], source=source, pushed_at=_when(last.get("pushed_at")),
        pull_number=last.get("pull_number"))
    try:
        with db.begin_nested():  # the webhook and the poll can both arrive here; the second one loses quietly
            db.add(change)
    except IntegrityError:
        return None
    log.info("github change kept repo=%s branch=%s head=%s source=%s projects=%d", change.repo, change.branch,
             head[:7], source, len(projects))
    return change


def _link_pull_request(change: ChangeEvent, line: dict) -> None:
    """A pull request opened on a commit already kept says which pull request it belongs to."""
    if line.get("kind") == "pull_request" and change.pull_number is None:
        change.pull_number = line.get("pull_number")


def _when(text: str | None) -> datetime | None:
    """GitHub's time text as the database keeps times: UTC with no zone."""
    if not text:
        return None
    return datetime.fromisoformat(text).astimezone(UTC).replace(tzinfo=None)

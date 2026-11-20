"""GitHub webhook deliveries as lines, and the one bookmark a webhook and a poll share.

Pure: no network and no database, so every event shape is easy to test. The webhook route
(routers/github.py) checks the signature and the delivery id before anything here runs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SEEN_KEPT = 200  # the same limit the poll keeps, in github.py
NO_COMMIT = "0" * 40  # GitHub's "before" or "after" when a branch is created or deleted


def event_lines(event: str, payload: dict) -> list[dict]:
    """The lines one delivery adds. Events WhiteHats does not read (ping, installation) add none."""
    reader = _READERS.get(event)
    if reader is None or not isinstance(payload.get("repository"), dict):
        return []
    return reader(payload)


def _push(payload: dict) -> list[dict]:
    repo = payload["repository"]
    ref = str(payload.get("ref", ""))
    if not ref.startswith("refs/heads/"):
        return [_line(repo, f"tag {ref.removeprefix('refs/tags/')} pushed", kind="tag")]
    branch = ref.removeprefix("refs/heads/")
    if payload.get("deleted"):
        return [_line(repo, f"branch {branch} deleted", kind="branch", branch=branch)]
    lines = []
    after = str(payload.get("after") or "")
    for commit in payload.get("commits") or []:
        subject = str(commit.get("message", "")).split("\n", 1)[0][:200]
        author = (commit.get("author") or {}).get("name") or "unknown author"
        # GitHub lists at most 20 commits of a bigger push; `after` is always the branch's new head.
        lines.append(_line(repo, f"push to {branch}: {commit['id'][:7]} {subject} by {author}", kind="commit",
                           branch=branch, sha=commit["id"], head=after))
    if not lines and after and after != NO_COMMIT:
        # A new branch at an existing commit, or a push too large for GitHub to list its commits.
        lines.append(_line(repo, f"push to {branch}: {after[:7]}", kind="commit", branch=branch, sha=after))
    return lines


def _pull_request(payload: dict) -> list[dict]:
    pull = payload.get("pull_request") or {}
    head = str((pull.get("head") or {}).get("sha", ""))
    title = str(pull.get("title", ""))[:200]
    message = f"pull request #{pull.get('number')} {payload.get('action')}: {title} ({head[:7]})"
    return [_line(payload["repository"], message, kind="pull_request", sha=head,
                  branch=str((pull.get("head") or {}).get("ref", "")), action=str(payload.get("action", "")),
                  pull_number=pull.get("number"), base_branch=str((pull.get("base") or {}).get("ref", "")))]


def _workflow_run(payload: dict) -> list[dict]:
    run = payload.get("workflow_run") or {}
    if payload.get("action") != "completed":
        return []  # requested and in-progress runs say nothing yet
    message = (f"workflow {run.get('name')} {run.get('conclusion')} on {run.get('head_branch')} "
               f"({str(run.get('head_sha', ''))[:7]})")
    return [_line(payload["repository"], message, kind="workflow_run", sha=str(run.get("head_sha", "")),
                  branch=str(run.get("head_branch", "")))]


_READERS = {"push": _push, "pull_request": _pull_request, "workflow_run": _workflow_run}


def _line(repo: dict, message: str, *, kind: str, branch: str = "", sha: str = "", **more: Any) -> dict:
    return {"source": f"github:{repo['full_name']}", "message": message, "kind": kind,
            "repo": repo["full_name"], "url": repo.get("html_url", ""), "sha": sha, "branch": branch,
            "default_branch": repo.get("default_branch", ""), "pushed_at": _iso(repo.get("pushed_at")), **more}


def absorb(bookmark: dict, lines: list[dict]) -> tuple[list[dict], dict]:
    """The lines no earlier delivery or poll reported, and the bookmark that remembers them.

    A commit is reported once, whichever of the webhook and the poll sees it first. A push to the
    default branch also moves the poll's head, so the next poll starts after it.
    """
    marks = {name: dict(mark) for name, mark in (bookmark.get("repos") or {}).items()}
    fresh = []
    for line in lines:
        if line.get("kind") != "commit":
            fresh.append(line)
            continue
        mark = marks.setdefault(line["repo"], {"head": None, "seen": []})
        if line["sha"] in mark["seen"] or line["sha"] == mark.get("head"):
            continue
        fresh.append(line)
        mark["seen"] = [line["sha"], *mark["seen"]][:SEEN_KEPT]
        if line["branch"] == line.get("default_branch"):
            mark["head"] = line["sha"]
            mark["pushed_at"] = line.get("pushed_at")
    return fresh, {**bookmark, "repos": marks}


def _iso(value: Any) -> str | None:
    """Push payloads give pushed_at as seconds; the REST API gives text. The bookmark keeps the text form."""
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value

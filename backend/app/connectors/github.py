"""GitHub, read through the WhiteHats GitHub App installed on the customer's repositories.

Discovery finds the repositories, their branches, default branch protection, CODEOWNERS, dependency
manifests and Actions workflows. Pushes arrive by webhook (github_events.py) and, as the fallback,
by polling the default branch. The connector only reads; it never holds a personal token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import quote, urlencode

from .base import Batch, ConnectionTest, Discovery, Permission, Probe, Source, SourceError
from .github_app import APP_PERMISSIONS, RENEW_BEFORE_S, app_token_from_settings, excess_permissions, expires_epoch
from .github_events import event_lines
from .http import Answer, Transport, httpx_transport, next_link
from .retry import Pacer, Throttled, http_wait_hint, with_retries
from .webhooks import signature_matches

log = logging.getLogger("whitehats.connectors.github")

API = "https://api.github.com"
API_VERSION = "2022-11-28"
PAGE_SIZE = 100  # the largest page GitHub serves
MAX_PAGES = 20  # a listing still paging after this has run away; stop instead of looping
LOOKBACK_S = 3600  # a repository with no bookmark yet starts an hour back
SEEN_KEPT = 200  # commit ids remembered per repository, so a webhook and a poll never both report one
CALLS_PER_SECOND = 10  # well under the 5,000 an hour an installation may make, without bursts
UNKNOWN_ENVIRONMENT = "shared"  # GitHub says nothing about the environment; a person sets it on the canvas
REPO_TYPE = "repo.github@1"
ACTIONS_TYPE = "delivery.github-actions@1"
# Files that declare dependencies, matched by exact file name anywhere in the default branch.
MANIFESTS = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "Pipfile", "Pipfile.lock", "pyproject.toml", "poetry.lock", "uv.lock", "setup.py",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle",
    "build.gradle.kts", "composer.json", "composer.lock", "packages.config", "packages.lock.json",
    ".terraform.lock.hcl",
})
# The three places GitHub reads CODEOWNERS from.
CODEOWNERS_PATHS = frozenset({"CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"})
SERVER_ERRORS = frozenset({500, 502, 503, 504})
DIFF_MEDIA_TYPE = "application/vnd.github.diff"
CONTENTS_LIMIT = 1_000_000  # the contents call returns no body above this size
CHECK_NAME = "WhiteHats"
REVIEW_CHECK_NAME = "WhiteHats AI review"  # the security audit of a pull request's change, after the scanners' check
CHECK_CONCLUSIONS = frozenset({"success", "failure", "neutral"})
CHECK_STATUSES = frozenset({"queued", "in_progress", "completed"})
CHECK_ANNOTATIONS_MAX = 50  # what GitHub accepts in one check run request
COMMENTS_MAX = 20  # inline comments one judged change may add to a pull request; the check run lists the rest
MARK = "whitehats:"  # each comment ends with a hidden marker naming its finding, so a re-scan never posts twice


@dataclass(frozen=True)
class RepositoryFiles:
    """One repository as a scan sees it: every file path on the default branch, a way to read a file's text, and the
    repository and workflows nodes that discovery makes. Reading is by the commit's own blobs, so a file is always the one
    the listing named, even if someone pushes while the scan runs."""

    full_name: str
    paths: frozenset[str]
    read: Callable[[str], str | None]
    node: dict
    actions: dict | None


class GitHub(Source):
    """One customer's installation of the WhiteHats GitHub App."""

    key = "github"
    has_events = True
    permissions = (
        Permission("metadata:read", "list the repositories the App is installed on, with their branches",
                   covers=("*",)),
        Permission("contents:read", "read new commits, and find CODEOWNERS and dependency manifests",
                   covers=("repo.github",)),
        Permission("actions:read", "list GitHub Actions workflows and their runs",
                   covers=("delivery.github-actions",)),
        Permission("pull_requests:read", "see open pull requests; comments are a separate feature",
                   covers=("repo.github",)),
    )
    page_size: ClassVar[int] = PAGE_SIZE  # recordings use a small page so paging is exercised
    lookback_s: ClassVar[int] = LOOKBACK_S  # recordings reach back far enough to hold several commits

    def __init__(self, config: Mapping[str, str], secret: Callable[[], str] = lambda: "", *,
                 transport: Transport | None = None, app_token: Callable[[], str] | None = None,
                 webhook_secret: Callable[[], str] | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__(config, secret)
        # Looked up when built, not when defined, so a test can stand in for the network.
        self._transport, self._clock, self._sleep = transport or httpx_transport, clock, sleep
        self._app_token = app_token or app_token_from_settings(clock)
        self._webhook_secret = webhook_secret or _webhook_secret_from_settings
        self._pacer = Pacer(1 / CALLS_PER_SECOND, sleep=sleep)
        self._token: tuple[str, float] | None = None
        self._token_permissions: dict[str, str] = {}  # what the installation token was granted
        self._repos: list[dict] | None = None

    # -- the contract -------------------------------------------------------------------

    def test(self) -> ConnectionTest:
        self._installation_token()
        sample, read_error = self._sample()
        repos = self._repositories()
        owners = sorted({r["owner"]["login"] for r in repos})
        identity = (f"WhiteHats GitHub App, installation {self._installation()} on {', '.join(owners) or 'no account'}"
                    f" ({len(repos)} repositories)")
        if not repos:
            return ConnectionTest(identity, (
                Probe("metadata:read", False, "The App is installed on no repositories. Choose at least one in "
                                              "GitHub, under the App's installation settings."),
                *(Probe(p.name, False, "Nothing to check without a repository.") for p in self.permissions[1:]),
            ), 0)
        first = repos[0]["full_name"]
        probes = (
            Probe("metadata:read", True, f"Listed {len(repos)} repositories."),
            Probe("contents:read", False, read_error) if read_error
            else Probe("contents:read", True, f"Read {sample} recent commit(s)."),
            self._probe("actions:read", f"/repos/{first}/actions/workflows", {"per_page": 1}),
            self._probe("pull_requests:read", f"/repos/{first}/pulls", {"state": "open", "per_page": 1}),
        )
        return ConnectionTest(identity, probes, sample)

    def discover(self) -> Discovery:
        nodes: list[dict] = []
        edges: list[dict] = []
        for repo in self._repositories():
            repo_node = self._repo_node(repo)
            nodes.append(repo_node)
            actions = self._workflows_node(repo)
            if actions is not None:
                nodes.append(actions)
                edges.append({"from": repo_node["id"], "to": actions["id"], "type": "built_by"})
        return Discovery(nodes, edges)

    def repositories(self) -> list[dict]:
        """The repositories this installation can see, as GitHub describes them."""
        return list(self._repositories())

    def repository_files(self, repo: dict, ref: str = "") -> RepositoryFiles:
        """One repository's files, for the repo scan, at its default branch or at `ref` (a commit, for a pull request's
        review). Only reads: one tree, then a blob for each file the scan asks for."""
        full, branch = repo["full_name"], ref or repo["default_branch"]
        tree = self._tree(full, branch)
        return RepositoryFiles(full, frozenset(tree), self._text_reader(full, tree),
                               self._repo_node(repo, frozenset(tree)), self._workflows_node(repo))

    def read(self, bookmark: dict, limit: int | None = None) -> Batch:
        marks = dict(bookmark.get("repos") or {})
        lines: list[dict] = []
        for repo in self._repositories():
            mark = marks.get(repo["full_name"]) or {}
            if mark and mark.get("pushed_at") == repo.get("pushed_at") and "branches" in mark:
                continue  # nothing was pushed anywhere in this repository since the last read
            marks[repo["full_name"]] = self._read_commits(repo, mark, lines, limit)
            if limit and len(lines) >= limit:
                break
            if not limit:  # a sample must not move the bookmark, and branch heads live in it
                after = marks[repo["full_name"]]
                after["branches"] = self._read_branch_heads(repo, mark, after, lines)
        if limit:
            return Batch(lines[:limit], bookmark)
        return Batch(lines, {**bookmark, "repos": marks})

    def handle_event(self, headers: Mapping[str, str], body: bytes) -> list[dict]:
        """A webhook delivery, checked by its signature, as lines. Unsigned or forged deliveries are refused."""
        lowered = {k.lower(): v for k, v in headers.items()}
        secret = self._webhook_secret()
        if not secret:
            raise SourceError("GitHub webhooks are not set up on this server: GITHUB_WEBHOOK_SECRET is empty.")
        if not signature_matches(secret, body, lowered.get("x-hub-signature-256", "")):
            raise SourceError("The webhook signature does not match, so the delivery was refused.")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise SourceError("The webhook body is not JSON.") from exc
        return event_lines(lowered.get("x-github-event", ""), payload)

    def publish_check(self, repo: str, head_sha: str, *, title: str, summary: str, annotations: list[dict],
                      conclusion: str = "", name: str = CHECK_NAME, status: str = "completed") -> str:
        """Write a WhiteHats check run ("WhiteHats", or the AI review's) on one commit and return its address.

        This is the connector's only write. It goes to the check-runs address of a repository the App already
        reads and to nothing else, and only when the installation really holds checks write. A check run lists
        at most 50 annotations per request, so a longer list is cut and the summary says how many were left out.
        """
        if status not in CHECK_STATUSES:
            raise SourceError(f"A check run cannot be {status!r}.")
        if status == "completed" and conclusion not in CHECK_CONCLUSIONS:
            raise SourceError(f"A check run cannot end as {conclusion!r}.")
        self._installation_token()
        if self._token_permissions.get("checks") != "write":
            raise SourceError("The WhiteHats GitHub App does not hold the checks permission, so no check run was written.")
        shown = annotations[:CHECK_ANNOTATIONS_MAX]
        left_out = len(annotations) - len(shown)
        text = summary + (f"\n\n{left_out} more finding(s) are in WhiteHats." if left_out else "")
        body = {"name": name, "head_sha": head_sha, "status": status,
                "output": {"title": title[:250], "summary": text[:60000], "annotations": shown}}
        if status == "completed":  # a queued or running check has no conclusion yet; GitHub shows it as pending
            body["conclusion"] = conclusion
        answer = self._post(f"/repos/{repo}/check-runs", body)
        return str((answer.body or {}).get("html_url", ""))

    def comment_findings(self, repo: str, number: int, head_sha: str, comments: list[dict]) -> int:
        """Add one inline comment per finding to the pull request, on its line, unless it already has one.

        Each entry of `comments` is `{key, path, line, body}`. A line outside the pull request's diff cannot take a
        line comment, so the comment is put on the file instead. Returns how many comments were added.
        """
        self._needs_pull_request_write()
        posted = self._marked_comments(repo, number)
        added = 0
        for item in comments[:COMMENTS_MAX]:
            marker = _marker(item["key"])
            if marker in posted:
                continue
            body = f"{item['body']}\n\n<!-- {marker} -->"
            path = f"/repos/{repo}/pulls/{int(number)}/comments"
            answer = self._post(path, {"body": body, "commit_id": head_sha, "path": item["path"],
                                       "line": int(item["line"]), "side": "RIGHT"}, unprocessable_ok=True)
            if answer.status == 422:  # the line is not part of the pull request's diff
                self._post(path, {"body": body, "commit_id": head_sha, "path": item["path"], "subject_type": "file"})
            added += 1
        return added

    def reply_fixed(self, repo: str, number: int, keys: list[str], text: str) -> int:
        """Answer each finding's comment once, saying it is fixed. Returns how many answers were added."""
        if not keys:
            return 0
        self._needs_pull_request_write()
        posted = self._marked_comments(repo, number)
        added = 0
        for key in keys:
            comment = posted.get(_marker(key))
            if comment is None or _marker(key, fixed=True) in posted:
                continue
            self._post(f"/repos/{repo}/pulls/{int(number)}/comments/{int(comment)}/replies",
                       {"body": f"{text}\n\n<!-- {_marker(key, fixed=True)} -->"})
            added += 1
        return added

    def _needs_pull_request_write(self) -> None:
        self._installation_token()
        if self._token_permissions.get("pull_requests") != "write":
            raise SourceError("The WhiteHats GitHub App does not hold pull request write, so no comment was added.")

    def _marked_comments(self, repo: str, number: int) -> dict[str, int]:
        """The markers already on the pull request's review comments, each with its comment's id."""
        found: dict[str, int] = {}
        for comment in self._pages(f"/repos/{repo}/pulls/{int(number)}/comments"):
            body = str(comment.get("body") or "")
            start = body.rfind("<!-- " + MARK)
            if start != -1:
                found[body[start + 5:body.find(" -->", start)]] = int(comment["id"])
        return found

    def compare_diff(self, repo: str, base: str, head: str) -> str:
        """What `head` changed, as `git diff` prints it: against the merge base when base is a branch name."""
        return self._get(f"/repos/{repo}/compare/{quote(base, safe='')}...{head}", accept=DIFF_MEDIA_TYPE).text

    def file_at(self, repo: str, path: str, sha: str) -> tuple[bytes | None, str]:
        """One file as it was at a commit, or None and the reason it was not read (too big, not a file)."""
        body = self._get(f"/repos/{repo}/contents/{quote(path)}", {"ref": sha}).body
        if not isinstance(body, dict) or body.get("type") != "file":
            return None, "not a regular file"
        if body.get("encoding") != "base64" or int(body.get("size", 0)) > CONTENTS_LIMIT:
            return None, f"larger than {CONTENTS_LIMIT // 1_000_000} MB"
        return base64.b64decode(body.get("content", "")), ""

    # -- reading ------------------------------------------------------------------------

    def _sample(self) -> tuple[int, str]:
        try:
            return len(self.read({}, limit=5).lines), ""
        except SourceError as exc:
            return 0, str(exc)

    def _read_commits(self, repo: dict, mark: dict, lines: list[dict], limit: int | None) -> dict:
        """Append the default branch's new commits, oldest first, and return the repository's next bookmark.

        The walk goes back from the branch head until it meets a commit already reported, so a
        fast-forward of old commits is not missed the way a date filter would miss it.
        """
        full, branch = repo["full_name"], repo["default_branch"]
        head, seen = mark.get("head"), list(mark.get("seen") or [])
        params: dict[str, Any] = {"sha": branch}
        if not head:
            params["since"] = _iso(self._clock() - self.lookback_s)
        fresh = []
        known = {head, *seen}
        for commit in self._pages(f"/repos/{full}/commits", params, empty_ok=True):
            if commit["sha"] in known:
                break
            fresh.append(commit)
            if limit and len(fresh) >= limit:
                break
        fresh.reverse()
        lines.extend(_commit_line(repo, branch, commit) for commit in fresh)
        return {"head": fresh[-1]["sha"] if fresh else head,
                "seen": ([c["sha"] for c in reversed(fresh)] + seen)[:SEEN_KEPT],
                "pushed_at": repo.get("pushed_at")}

    def _read_branch_heads(self, repo: dict, before: dict, after: dict, lines: list[dict]) -> dict[str, str]:
        """Append a line for each non-default branch whose head moved since the last read; return every head.

        A pull request branch is not the default branch, so the commit walk never sees it. A repository
        read for the first time reports none: the branches it already has are old news.
        """
        full, default = repo["full_name"], repo["default_branch"]
        heads = {b["name"]: b["commit"]["sha"] for b in self._pages(f"/repos/{full}/branches", empty_ok=True)}
        known = before.get("branches")
        if known is None:
            return heads
        reported = {after["head"], *after["seen"]}  # a branch cut from a commit already reported adds nothing
        for name, sha in sorted(heads.items()):
            if name != default and known.get(name) != sha and sha not in reported:
                lines.append(_branch_head_line(repo, name, sha))
        for name in sorted(set(known) - set(heads)):  # a branch that was here and is not now was deleted
            lines.append(_branch_gone_line(repo, name))
        return heads

    def _repositories(self) -> list[dict]:
        if self._repos is None:
            self._repos = list(self._pages("/installation/repositories", key="repositories"))
        return self._repos

    def _repo_node(self, repo: dict, files: frozenset[str] | None = None) -> dict:
        full, branch = repo["full_name"], repo["default_branch"]
        branches = list(self._pages(f"/repos/{full}/branches", empty_ok=True))
        files = self._files(full, branch) if files is None else files
        props = {
            "url": repo["html_url"], "auth_method": "github_app", "default_branch": branch,
            "branch_protection": any(b["name"] == branch and b.get("protected") for b in branches),
            "codeowners": bool(files & CODEOWNERS_PATHS),
            "branches": sorted(b["name"] for b in branches),
            "dependency_manifests": sorted(p for p in files if p.rsplit("/", 1)[-1] in MANIFESTS),
            "repo": full,
        }
        return _node(_node_id("repo", full), REPO_TYPE, full, props)

    def _workflows_node(self, repo: dict) -> dict | None:
        workflows = list(self._pages(f"/repos/{repo['full_name']}/actions/workflows", key="workflows"))
        return self._actions_node(repo, workflows) if workflows else None

    def _actions_node(self, repo: dict, workflows: list[dict]) -> dict:
        full = repo["full_name"]
        props = {"repository": repo["html_url"], "workflows": sorted(w["path"] for w in workflows),
                 "url": f"{repo['html_url']}/actions", "repo": full}
        return _node(_node_id("actions", full), ACTIONS_TYPE, f"{full} workflows", props)

    def _files(self, full: str, branch: str) -> set[str]:
        """Every file path on the branch, from one recursive tree read. An empty repository has none."""
        return set(self._tree(full, branch))

    def _tree(self, full: str, branch: str) -> dict[str, dict]:
        """Each file of the branch with its blob id and size, from one recursive tree read. An empty repository has none."""
        answer = self._get(f"/repos/{full}/git/trees/{branch}", {"recursive": 1}, empty_ok=True)
        if answer.body is None:
            return {}
        if answer.body.get("truncated"):
            # GitHub stops a recursive tree at 100,000 entries; what came back is still listed.
            log.warning("github tree truncated repo=%s branch=%s", full, branch)
        return {item["path"]: item for item in answer.body.get("tree", []) if item.get("type") == "blob"}

    def _text_reader(self, full: str, tree: dict[str, dict]) -> Callable[[str], str | None]:
        """A reader of the tree's files as text. A file that is too big, or is not text, comes back as None. Each file is read once."""
        kept: dict[str, str | None] = {}

        def read(path: str) -> str | None:
            if path not in kept:
                entry = tree.get(path)
                kept[path] = None if entry is None or int(entry.get("size", 0)) > CONTENTS_LIMIT \
                    else self._blob_text(full, entry["sha"])
            return kept[path]

        return read

    def _blob_text(self, full: str, sha: str) -> str | None:
        body = self._get(f"/repos/{full}/git/blobs/{sha}").body
        if not isinstance(body, dict) or body.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(body.get("content", "")).decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _probe(self, name: str, path: str, params: dict) -> Probe:
        try:
            self._get(path, params)
        except SourceError as exc:
            return Probe(name, False, str(exc))
        return Probe(name, True, f"{name} is allowed.")

    # -- GitHub -------------------------------------------------------------------------

    def _installation(self) -> str:
        installation = str(self.config.get("installation_id") or "").strip()
        if not installation:
            raise SourceError("Install the WhiteHats GitHub App on your repositories first. "
                              "Personal access tokens are not accepted.")
        return installation

    def _installation_token(self) -> str:
        """The installation's token, renewed before it expires. Refuses an App given more than it needs."""
        if self._token and self._token[1] - self._clock() > RENEW_BEFORE_S:
            return self._token[0]
        installation = self._installation()
        url = f"{_api()}/app/installations/{installation}/access_tokens"
        answer = self._send("POST", url, f"Bearer {self._app_token()}", "sign-in")
        excess = excess_permissions(answer.body.get("permissions") or {})
        if excess:
            raise SourceError("The WhiteHats GitHub App holds more access than it needs (" + ", ".join(excess) +
                              "). Lower it in the App's permission settings; WhiteHats will not run with it.")
        self._token = (answer.body["token"], expires_epoch(answer.body["expires_at"]))
        self._token_permissions = dict(answer.body.get("permissions") or {})
        return self._token[0]

    def _get(self, path: str, params: Mapping[str, Any] | None = None, *, empty_ok: bool = False,
             accept: str = "application/vnd.github+json") -> Answer:
        url = f"{_api()}{path}" + (f"?{urlencode(params)}" if params else "")
        return self._send("GET", url, f"Bearer {self._installation_token()}", path, empty_ok=empty_ok, accept=accept)

    def _post(self, path: str, body: Mapping[str, Any], *, unprocessable_ok: bool = False) -> Answer:
        """The write path. Only a repository's check runs and its pull requests' review comments are allowed."""
        if not _writable(path):
            raise SourceError(f"The GitHub connector does not write to {path}.")
        return self._send("POST", f"{_api()}{path}", f"Bearer {self._installation_token()}", path, body=body,
                          also_ok=(422,) if unprocessable_ok else ())

    def _pages(self, path: str, params: Mapping[str, Any] | None = None, *, key: str | None = None,
               empty_ok: bool = False) -> Iterator[dict]:
        """Every item of a listing, page by page, fetched only as the caller reads on."""
        answer = self._get(path, {**(params or {}), "per_page": self.page_size}, empty_ok=empty_ok)
        for _ in range(MAX_PAGES):
            body = answer.body or ([] if key is None else {})
            yield from (body if key is None else body.get(key, []))
            following = next_link(answer.headers)
            if not following:
                return
            answer = self._send("GET", following, f"Bearer {self._installation_token()}", path)
        raise SourceError(f"GitHub kept paging {path} past {MAX_PAGES} pages; stopped rather than loop.")

    def _send(self, method: str, url: str, authorization: str, what: str, *, empty_ok: bool = False,
              accept: str = "application/vnd.github+json", body: Mapping[str, Any] | None = None,
              also_ok: tuple[int, ...] = ()) -> Answer:
        headers = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION,
                   "Authorization": authorization, "User-Agent": "WhiteHats"}

        def attempt() -> Answer:
            self._pacer.wait()
            answer = self._transport(method, url, headers, body)
            wait = _throttle_wait(answer, self._clock())
            if wait is not None:
                raise Throttled(wait)
            return answer

        try:
            answer = with_retries(attempt, http_wait_hint, sleep=self._sleep)
        except Throttled as exc:
            raise SourceError(f"GitHub asked WhiteHats to slow down on {what} and the wait ran out.") from exc
        if answer.status < 300 or answer.status in also_ok:
            return answer
        if empty_ok and answer.status in (404, 409) and _is_empty(answer):
            return Answer(answer.status, answer.headers, None)
        raise SourceError(_refusal(what, answer))


def _writable(path: str) -> bool:
    """`/repos/{owner}/{repo}/check-runs`, `/repos/{owner}/{repo}/pulls/{n}/comments`, or a reply to one comment."""
    parts = path.strip("/").split("/")
    if len(parts) < 4 or parts[0] != "repos" or not parts[1] or not parts[2]:
        return False
    rest = parts[3:]
    if rest == ["check-runs"]:
        return True
    if len(rest) == 3 and rest[0] == "pulls" and rest[1].isdigit() and rest[2] == "comments":
        return True
    return len(rest) == 5 and rest[0] == "pulls" and rest[1].isdigit() and rest[2] == "comments" \
        and rest[3].isdigit() and rest[4] == "replies"


def _marker(key: str, *, fixed: bool = False) -> str:
    """The hidden marker for one finding. A finding's key never holds "--", which would end the HTML comment."""
    return MARK + ("fixed:" if fixed else "") + key.replace("--", "- -")


def _api() -> str:
    """GitHub's API address: github.com's, unless this server is set up for a GitHub Enterprise Server."""
    from ..config import settings

    return (settings.github_api_url or API).rstrip("/")


def _webhook_secret_from_settings() -> str:
    from ..config import settings

    return settings.github_webhook_secret


def _throttle_wait(answer: Answer, now: float) -> float | None:
    """How long GitHub asked us to wait, or None when this answer is not a request to slow down."""
    if answer.status in SERVER_ERRORS:
        return 0.0
    if answer.status not in (403, 429):
        return None
    retry_after = answer.headers.get("retry-after")
    if retry_after:
        return float(retry_after)
    if answer.headers.get("x-ratelimit-remaining") == "0":
        return max(float(answer.headers.get("x-ratelimit-reset", now)) - now, 0.0)
    return 0.0 if answer.status == 429 else None


def _is_empty(answer: Answer) -> bool:
    """GitHub's answer for a repository with no commits yet (409) or a branch with no tree (404)."""
    message = str((answer.body or {}).get("message", "")).lower()
    return "empty" in message or answer.status == 404


def _refusal(what: str, answer: Answer) -> str:
    message = str((answer.body or {}).get("message", "")) if isinstance(answer.body, dict) else ""
    if what == "sign-in" and answer.status in (401, 403, 404):
        return (f"GitHub denied the WhiteHats App's sign-in ({answer.status}: {message or 'no reason given'}). "
                "Check that the App is set up on this server and still installed on your repositories.")
    if answer.status in (401, 403):
        return f"GitHub denied {what} ({answer.status}: {message or 'no reason given'})."
    if answer.status == 404:
        return f"GitHub could not find {what}, or the App is not allowed to see it (404)."
    return f"GitHub answered {answer.status} for {what}: {message[:160]}"


def _commit_line(repo: dict, branch: str, commit: dict) -> dict:
    detail = commit.get("commit") or {}
    subject = str(detail.get("message", "")).split("\n", 1)[0][:200]
    author = (detail.get("author") or {}).get("name") or "unknown author"
    return {"source": f"github:{repo['full_name']}", "kind": "commit",
            "message": f"push to {branch}: {commit['sha'][:7]} {subject} by {author}",
            "repo": repo["full_name"], "url": repo["html_url"], "sha": commit["sha"], "branch": branch,
            "default_branch": branch, "pushed_at": repo.get("pushed_at")}


def _branch_head_line(repo: dict, branch: str, sha: str) -> dict:
    """The poll's report of a branch head it did not see arrive. Its commits are not listed, only the new head."""
    return {"source": f"github:{repo['full_name']}", "kind": "commit", "message": f"push to {branch}: {sha[:7]}",
            "repo": repo["full_name"], "url": repo["html_url"], "sha": sha, "branch": branch,
            "default_branch": repo["default_branch"], "pushed_at": repo.get("pushed_at")}


def _branch_gone_line(repo: dict, branch: str) -> dict:
    """The poll's report of a branch that is no longer listed, the same line a webhook's delete event makes."""
    return {"source": f"github:{repo['full_name']}", "kind": "branch", "message": f"branch {branch} deleted",
            "repo": repo["full_name"], "url": repo["html_url"], "branch": branch}


def _node(node_id: str, type_ref: str, name: str, properties: dict) -> dict:
    return {"id": node_id, "type": type_ref, "name": name, "environment": UNKNOWN_ENVIRONMENT,
            "provenance": {"kind": "discovered", "source": "github"}, "properties": properties}


def _node_id(kind: str, full_name: str) -> str:
    return f"github_{kind}_" + hashlib.sha256(full_name.lower().encode()).hexdigest()[:16]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["APP_PERMISSIONS", "GitHub"]

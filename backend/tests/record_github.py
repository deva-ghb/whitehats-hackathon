"""Records the GitHub connector's answers for the contract tests.

    uv run python tests/record_github.py --checkout <proving-ground clone>   # live reads plus the local clone
    uv run python tests/record_github.py --app                               # the real App, once it exists
    uv run python tests/record_github.py --scan                              # a repository scan through the real App

Until the WhiteHats GitHub App exists, the answers are assembled from what is real: the repository,
its workflows and pull requests read live and read-only with GITHUB_TOKEN, and the commits, branches
and files of the proving-ground repository's main branch from a local clone (`git` read commands only).
signin-refused is always GitHub's own answer to an App token it cannot decode.
With --app every scenario except throttled is recorded from GitHub through the App.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

os.environ["WH_DATA_DIR"] = tempfile.mkdtemp(prefix="wh-record-")
os.environ["WH_LOAD_DOTENV"] = "0"
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]

from connector_contract import GITHUB_CONFIG, GITHUB_WEBHOOK_SECRET, RecordedGitHub
from connector_kit import RECORDINGS, HttpRecorder

from app.connectors.base import SourceError
from app.connectors.github import API
from app.connectors.github_app import APP_PERMISSIONS, app_jwt
from app.connectors.http import Answer, httpx_transport

ASSEMBLED = ("Assembled on {day} from the proving-ground repository: repository, workflows and pull requests "
             "read live with a read-only token; commits, branches and files from main ({head}). The App's "
             "token exchange is GitHub's documented answer, because the WhiteHats App does not exist yet.")
# A public App id (GitHub Actions' own), so GitHub answers about the key, not a missing App.
PUBLIC_APP_ID = "15368"
FROM_APP = "Recorded from GitHub through the WhiteHats GitHub App on {day}."


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(checkout: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True).stdout


def _live(path: str) -> Any:
    """One read-only GET with GITHUB_TOKEN. The token is sent, never printed or saved."""
    token = os.environ["GITHUB_TOKEN"]
    answer = httpx_transport("GET", f"{API}{path}", {"Authorization": f"Bearer {token}",
                                                     "Accept": "application/vnd.github+json"})
    if answer.status != 200:
        raise SystemExit(f"GET {path} answered {answer.status}")
    return answer.body


class Snapshot:
    """The proving-ground repository as GitHub would describe it, from live reads and the local clone."""

    def __init__(self, full_name: str, checkout: Path):
        self.repo = _live(f"/repos/{full_name}")
        self.workflows = _live(f"/repos/{full_name}/actions/workflows")["workflows"]
        self.pulls = _live(f"/repos/{full_name}/pulls?state=open&per_page=1")
        ref = f"origin/{self.repo['default_branch']}"
        self.head = _git(checkout, "rev-parse", ref).strip()
        self.commits = []
        for row in _git(checkout, "log", ref, "--format=%H%x1f%cI%x1f%an%x1f%ae%x1f%B%x1e").split("\x1e"):
            if not row.strip():
                continue
            sha, committed, name, email, message = row.strip("\n").split("\x1f")
            self.commits.append({"sha": sha, "commit": {"message": message.strip(), "author": {
                "name": name, "email": email, "date": committed}, "committer": {"date": committed}}})
        self.files = [p for p in _git(checkout, "ls-tree", "-r", "--name-only", ref).splitlines() if p]
        heads = _git(checkout, "ls-remote", "--heads", "origin").splitlines()
        self.branches = [{"name": line.split("refs/heads/", 1)[1], "commit": {"sha": line.split()[0]},
                          "protected": False} for line in heads]  # this free private repo cannot protect branches


class Simulator:
    """A Transport that answers the GitHub REST calls the connector makes, paging the way GitHub does."""

    def __init__(self, snapshot: Snapshot, clock: float, throttle_once: str = ""):
        self.s, self.clock, self.throttle_once = snapshot, clock, throttle_once

    def __call__(self, method: str, url: str, headers: Any = None, body: Any = None) -> Answer:
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        path = parts.path
        if self.throttle_once and self.throttle_once in path:
            self.throttle_once = ""
            return Answer(429, {"retry-after": "1"}, {"message": "You have exceeded a secondary rate limit."})
        if method == "POST" and path.endswith("/access_tokens"):
            return Answer(201, {}, {"token": "recorded-placeholder", "expires_at": _iso(self.clock + 3600),
                                    "permissions": dict(APP_PERMISSIONS), "repository_selection": "selected"})
        full = self.s.repo["full_name"]
        if path == "/installation/repositories":
            return self._page(url, query, [self.s.repo], key="repositories")
        if path == f"/repos/{full}/commits":
            since = query.get("since")
            items = [c for c in _utc(self.s.commits) if not since or c["commit"]["committer"]["date"] >= since]
            return self._page(url, query, items)
        if path == f"/repos/{full}/branches":
            return self._page(url, query, self.s.branches)
        if path == f"/repos/{full}/git/trees/{self.s.repo['default_branch']}":
            tree = [{"path": p, "type": "blob", "mode": "100644"} for p in self.s.files]
            return Answer(200, {}, {"sha": self.s.head, "tree": tree, "truncated": False})
        if path == f"/repos/{full}/actions/workflows":
            return self._page(url, query, self.s.workflows, key="workflows")
        if path == f"/repos/{full}/pulls":
            return Answer(200, {}, self.s.pulls)
        raise AssertionError(f"the simulator does not answer {method} {url}")

    @staticmethod
    def _page(url: str, query: dict, items: list, key: str | None = None) -> Answer:
        size, page = int(query.get("per_page", 30)), int(query.get("page", 1))
        chunk = items[(page - 1) * size: page * size]
        headers = {}
        if page * size < len(items):
            base = url.split("?", 1)[0]
            headers["link"] = f'<{base}?{urlencode({**query, "page": page + 1})}>; rel="next"'
        body = chunk if key is None else {"total_count": len(items), key: chunk}
        return Answer(200, headers, body)


def _utc(commits: list[dict]) -> list[dict]:
    """GitHub gives commit dates in UTC; git gives the committer's own offset."""
    out = []
    for c in commits:
        when = datetime.fromisoformat(c["commit"]["committer"]["date"]).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({**c, "commit": {**c["commit"], "author": {**c["commit"]["author"], "date": when},
                                    "committer": {"date": when}}})
    return out


def _source(transport: Any, clock: float) -> RecordedGitHub:
    return RecordedGitHub(GITHUB_CONFIG, transport=transport, app_token=lambda: "recorded-app-token",
                          webhook_secret=lambda: GITHUB_WEBHOOK_SECRET, clock=lambda: clock, sleep=lambda _s: None)


def record_assembled(snapshot: Snapshot) -> None:
    newest = max(datetime.fromisoformat(c["commit"]["committer"]["date"]).timestamp() for c in snapshot.commits)
    clock = float(int(newest) + 300)
    about = ASSEMBLED.format(day=time.strftime("%Y-%m-%d"), head=snapshot.head[:7])
    for name, run, throttle in (("test-ok", lambda s: s.test(), ""), ("discover-ok", lambda s: s.discover(), ""),
                                ("read-twice", _read_twice, ""), ("throttled", lambda s: s.read({}), "/commits")):
        recorder = HttpRecorder(Simulator(snapshot, clock, throttle), clock)
        run(_source(recorder, clock))
        print("wrote", recorder.save("github", name, about + (" The 429 is added by hand." if throttle else "")))


def _read_twice(source: RecordedGitHub) -> None:
    first = source.read({})
    source.read(first.bookmark)


def record_refused() -> None:
    """GitHub's real answer to an App token signed by a key it has never seen. No credential is involved."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    clock = time.time()
    recorder = HttpRecorder(httpx_transport, round(clock))
    source = RecordedGitHub(GITHUB_CONFIG, transport=recorder, app_token=lambda: app_jwt(PUBLIC_APP_ID, key, clock),
                            clock=lambda: clock, sleep=lambda _s: None)
    try:
        source.test()
    except SourceError as exc:
        print("refused:", exc)
    about = f"Recorded from api.github.com on {time.strftime('%Y-%m-%d')}: an App token signed by an unknown key."
    print("wrote", recorder.save("github", "signin-refused", about))


def record_events_forged() -> None:
    path = RECORDINGS / "github" / "events-forged.json"
    path.write_text(json.dumps({"about": "A delivery with a forged signature makes no call to GitHub.",
                                "clock": 0, "calls": []}, indent=2) + "\n")
    print("wrote", path)


def _installation_through_the_app() -> str:
    """The App's one installation, from its own list: the id in GITHUB_APP_INSTALLATION_ID goes stale when the App is reinstalled."""
    from connector_kit import app_installation

    from app.connectors.github_app import app_token_from_settings

    return app_installation(app_token_from_settings()(), os.environ.get("GITHUB_APP_INSTALLATION_ID", ""))


def record_from_app() -> None:
    from app.connectors.github_app import app_token_from_settings

    installation = _installation_through_the_app()
    clock = float(int(time.time()))  # whole seconds: the clock the calls used is the one recorded
    about = FROM_APP.format(day=time.strftime("%Y-%m-%d"))
    for name, run in (("test-ok", lambda s: s.test()), ("discover-ok", lambda s: s.discover()),
                      ("read-twice", _read_twice)):
        recorder = HttpRecorder(httpx_transport, clock)
        source = RecordedGitHub({"installation_id": installation}, transport=recorder,
                                app_token=app_token_from_settings(lambda: clock), clock=lambda: clock)
        run(source)
        print("wrote", recorder.save("github", name, about, replace={installation: GITHUB_CONFIG["installation_id"]}))


def record_scan_from_app() -> None:
    """Every call a repository scan makes through the real App: the file tree, then each file it asks for. Only GETs."""
    from app.connectors.github_app import app_token_from_settings
    from app.services.repo_scan import scan_repositories

    installation = _installation_through_the_app()
    clock = float(int(time.time()))
    recorder = HttpRecorder(httpx_transport, clock)
    source = RecordedGitHub({"installation_id": installation}, transport=recorder,
                            app_token=app_token_from_settings(lambda: clock), clock=lambda: clock)
    found = scan_repositories(source, environment="prod")
    about = FROM_APP.format(day=time.strftime("%Y-%m-%d")) + (
        f" A repository scan: {len(found.nodes)} nodes and {len(found.edges)} connections found. "
        "The files are the proving-ground repository's, read from its default branch.")
    print("wrote", recorder.save("github", "scan-ok", about, replace={installation: GITHUB_CONFIG["installation_id"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkout", type=Path, help="a clone of the proving-ground repository")
    parser.add_argument("--app", action="store_true", help="record through the real WhiteHats GitHub App")
    parser.add_argument("--scan", action="store_true", help="record a repository scan through the real App (read only)")
    args = parser.parse_args()
    if args.scan:
        record_scan_from_app()
        return
    if args.app:
        record_from_app()
    else:
        if not args.checkout:
            parser.error("--checkout is needed until the App exists")
        record_assembled(Snapshot(os.environ["PROVING_GROUND_REPO"], args.checkout))
    record_refused()
    record_events_forged()


if __name__ == "__main__":
    main()

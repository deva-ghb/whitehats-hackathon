"""The change trigger (VS.1): a push is kept once, only its diff and changed files are read, and the result is stored."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field

import pytest
from connector_contract import GITHUB_CONFIG, github
from connector_kit import HttpReplay, load
from sqlalchemy import select

from app import changes
from app.changes import process
from app.connectors import REGISTRY, routing
from app.connectors.base import SourceError
from app.connectors.github import GitHub
from app.db import SessionLocal
from app.decisions_api import decide
from app.models import ChangeEvent, Connector, Project
from app.services import cloudlogs

REPO = "deva-ghb/whitehats-proving-ground"
URL = f"https://github.com/{REPO}"
SECRET = "test-webhook-secret"
KEY = "pgsk_" + "live_" + "9f3k2m8q7w1z0x4c5v6b"  # split so this file is not itself a hit
HEAD, OLDER, OLDEST = "a" * 40, "b" * 40, "c" * 40
BRANCH = "demo/change-1"


def _diff(path: str, text: str) -> str:
    """A diff that adds one file, as git prints it."""
    body = text.splitlines()
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(body)} @@\n" + "".join(f"+{line}\n" for line in body))


LEAKY = 'TOKEN = "' + KEY + '"\n'


@dataclass
class Script:
    """What a pretend GitHub says, and what it was asked."""

    diffs: dict = field(default_factory=dict)  # (base, head) -> diff text
    files: dict = field(default_factory=dict)  # path -> bytes, or (None, reason)
    asked: list = field(default_factory=list)


class ScriptedGitHub(GitHub):
    """The GitHub connector with its two reading calls answered from a script: no network."""

    def __init__(self, config, secret=lambda: "", *, script: Script):
        super().__init__(config, secret)
        self.script = script

    def compare_diff(self, repo, base, head):
        self.script.asked.append(("diff", base, head))
        if (base, head) not in self.script.diffs:
            raise SourceError(f"GitHub could not find compare/{base}...{head}, or the App is not allowed to see it (404).")
        return self.script.diffs[(base, head)]

    def file_at(self, repo, path, sha):
        self.script.asked.append(("file", path, sha))
        found = self.script.files[path]
        return found if isinstance(found, tuple) else (found, "")


def _push_line(sha: str, *, branch: str = BRANCH, head: str | None = None, first: str | None = None) -> dict:
    return {"source": f"github:{REPO}", "message": f"push to {branch}: {sha[:7]}", "kind": "commit", "repo": REPO,
            "url": URL, "sha": sha, "branch": branch, "default_branch": "main", "pushed_at": "2026-10-26T18:00:00Z",
            **({"head": head} if head else {})}


@pytest.fixture
def script(monkeypatch):
    """A pretend GitHub for processing, with the Decisions API running its house rules only (no containers)."""
    script = Script()
    monkeypatch.setitem(REGISTRY, "github", lambda config, secret: ScriptedGitHub(config, secret, script=script))
    monkeypatch.setattr(process, "decide", lambda root, diff, **_: decide(root, diff, scanners=()))
    return script


def _changes() -> list[ChangeEvent]:
    with SessionLocal() as db:
        rows = db.scalars(select(ChangeEvent).order_by(ChangeEvent.id)).all()
        db.expunge_all()
        return list(rows)


def _record(row_id: int, lines: list[dict], source: str = "poll") -> list[ChangeEvent]:
    with SessionLocal() as db:
        kept = changes.record(db, db.get(Connector, row_id), lines, source=source)
        db.commit()
        return kept


def _judge() -> int:
    with SessionLocal() as db:
        return changes.process_pending(db)


# ---------------------------------------------------------------- keeping a change once


def test_a_pushed_commit_is_kept_once_however_often_it_is_reported(row_id):
    first = _record(row_id, [_push_line(HEAD)], "webhook")
    again = _record(row_id, [_push_line(HEAD)], "poll")
    assert len(first) == 1 and again == []
    [change] = _changes()
    assert (change.repo, change.branch, change.head_sha, change.source, change.status) == (
        REPO, BRANCH, HEAD, "webhook", "pending")
    assert change.pushed_at is not None and change.project_ids


def test_a_burst_of_commits_on_one_branch_is_one_change_from_the_oldest_to_the_head(row_id):
    lines = [_push_line(OLDEST), _push_line(OLDER), _push_line(HEAD)]
    assert len(_record(row_id, lines)) == 1
    [change] = _changes()
    assert (change.first_sha, change.head_sha) == (OLDEST, HEAD)


def test_a_big_push_is_judged_at_the_head_github_names_not_the_last_commit_it_lists(row_id):
    _record(row_id, [_push_line(OLDER, head=HEAD), _push_line(OLDEST, head=HEAD)])
    assert [c.head_sha for c in _changes()] == [HEAD]


def test_each_branch_gets_its_own_change(row_id):
    _record(row_id, [_push_line(HEAD, branch="one"), _push_line(OLDER, branch="two")])
    assert sorted(c.branch for c in _changes()) == ["one", "two"]


def test_a_pull_request_opened_on_a_kept_commit_names_its_number_and_does_not_keep_it_twice(row_id):
    _record(row_id, [_push_line(HEAD)])
    opened = {"kind": "pull_request", "action": "opened", "pull_number": 7, "repo": REPO, "url": URL, "sha": HEAD,
              "branch": BRANCH, "default_branch": "main", "pushed_at": "2026-10-26T18:00:01Z"}
    assert _record(row_id, [opened]) == []
    [change] = _changes()
    assert change.pull_number == 7


def test_a_pull_request_on_a_commit_nobody_saw_is_kept_and_a_closed_or_labelled_one_is_not(row_id):
    base = {"kind": "pull_request", "pull_number": 8, "repo": REPO, "url": URL, "sha": HEAD, "branch": BRANCH,
            "default_branch": "main", "pushed_at": "2026-10-26T18:00:01Z"}
    assert _record(row_id, [{**base, "action": "closed"}, {**base, "action": "labeled"}]) == []
    assert len(_record(row_id, [{**base, "action": "synchronize"}])) == 1
    assert _changes()[0].pull_number == 8


def test_a_repository_no_project_holds_is_not_read(row_id):
    with SessionLocal() as db:
        connector = db.get(Connector, row_id)
        before = routing.attachments(db, connector)
        assert before  # the demo account attaches its GitHub connection to projects
        for project_id, _scope in before:
            routing.attach(db, connector, db.get(Project, project_id), {"repos": ["deva-ghb/some-other-repo"]}, "test")
        db.commit()
    try:
        assert _record(row_id, [_push_line(HEAD)]) == []
    finally:
        with SessionLocal() as db:
            for project_id, scope in before:
                routing.attach(db, db.get(Connector, row_id), db.get(Project, project_id), scope, "test")
            db.commit()
    assert _changes() == []


# ---------------------------------------------------------------- reading and judging it


def test_only_the_changed_files_are_read_and_the_leak_is_found_where_it_was_added(row_id, script):
    script.diffs[("main", HEAD)] = _diff("shop/settings.py", LEAKY) + _diff("docs/notes.md", "hello\n")
    script.files = {"shop/settings.py": LEAKY.encode(), "docs/notes.md": b"hello\n"}
    _record(row_id, [_push_line(HEAD)])
    assert _judge() == 1
    [change] = _changes()
    assert change.status == "decided" and change.verdict == "suspect" and change.base == "main"
    assert change.files == ["shop/settings.py", "docs/notes.md"] and change.skipped == []
    [suspect] = change.decision["suspects"]
    assert (suspect["rule_id"], suspect["evidence"]["path"], suspect["evidence"]["start_line"]) == (
        "whitehats.secrets.provider-live-key", "shop/settings.py", 1)
    assert [a for a in script.asked if a[0] == "file"] == [
        ("file", "shop/settings.py", HEAD), ("file", "docs/notes.md", HEAD)]  # nothing outside the diff
    assert change.fetched_at and change.decided_at and change.decided_at >= change.fetched_at
    assert KEY not in json.dumps(change.decision)  # the secret itself is never kept


def test_a_clean_change_is_clean_and_a_deleted_file_is_not_read(row_id, script):
    removal = "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    script.diffs[("main", HEAD)] = _diff("shop/ok.py", "x = 1\n") + removal
    script.files = {"shop/ok.py": b"x = 1\n"}
    _record(row_id, [_push_line(HEAD)])
    _judge()
    [change] = _changes()
    assert (change.status, change.verdict, change.files) == ("decided", "clean", ["shop/ok.py"])
    assert ("file", "old.py", HEAD) not in script.asked


def test_a_file_that_cannot_be_written_safely_or_is_too_big_is_skipped_and_said_so(row_id, script):
    script.diffs[("main", HEAD)] = (_diff("../outside.py", "x = 1\n") + _diff("big.bin", "x = 1\n")
                                    + _diff("ok.py", "x = 1\n"))
    script.files = {"big.bin": (None, "larger than 1 MB"), "ok.py": b"x = 1\n"}
    _record(row_id, [_push_line(HEAD)])
    _judge()
    [change] = _changes()
    assert change.files == ["ok.py"]
    assert {s["path"]: s["reason"] for s in change.skipped} == {
        "../outside.py": "its path leaves the repository", "big.bin": "larger than 1 MB"}
    assert ("file", "../outside.py", HEAD) not in script.asked


def test_a_change_bigger_than_the_limit_is_read_as_far_as_it_goes_and_the_rest_is_skipped(row_id, script, monkeypatch):
    from app.changes import fetch

    monkeypatch.setattr(fetch, "MAX_TOTAL_BYTES", 10)
    monkeypatch.setattr(fetch, "WORKERS", 1)  # one at a time, so which file crosses the limit is known
    script.diffs[("main", HEAD)] = _diff("a.py", "x = 1\n") + _diff("b.py", "y = 2\n")
    script.files = {"a.py": b"x" * 20, "b.py": b"y = 2\n"}
    _record(row_id, [_push_line(HEAD)])
    _judge()
    [change] = _changes()
    assert change.files == ["a.py"]
    assert change.skipped == [{"path": "b.py", "reason": "the change is bigger than WhiteHats reads"}]


def test_the_same_commit_is_never_judged_twice(row_id, script):
    script.diffs[("main", HEAD)] = _diff("a.py", "x = 1\n")
    script.files = {"a.py": b"x = 1\n"}
    _record(row_id, [_push_line(HEAD)], "webhook")
    assert _judge() == 1
    _record(row_id, [_push_line(HEAD)], "poll")  # the poll finds the same commit later
    assert _judge() == 0
    assert [a for a in script.asked if a[0] == "diff"] == [("diff", "main", HEAD)]
    assert len(_changes()) == 1


def test_the_diff_starts_at_the_last_commit_judged_on_that_branch(row_id, script):
    script.diffs[("main", OLDER)] = _diff("a.py", "x = 1\n")
    script.diffs[(OLDER, HEAD)] = _diff("b.py", "y = 2\n")
    script.files = {"a.py": b"x = 1\n", "b.py": b"y = 2\n"}
    _record(row_id, [_push_line(OLDER)])
    _judge()
    _record(row_id, [_push_line(HEAD)])
    _judge()
    first, second = _changes()
    assert (first.base, second.base) == ("main", OLDER) and second.files == ["b.py"]


def test_the_newest_of_several_waiting_commits_on_a_branch_is_judged_and_covers_the_older(row_id, script):
    script.diffs[("main", HEAD)] = _diff("a.py", "x = 1\n")
    script.files = {"a.py": b"x = 1\n"}
    _record(row_id, [_push_line(OLDER)], "webhook")
    _record(row_id, [_push_line(HEAD)], "poll")
    assert _judge() == 1
    older, newest = _changes()
    assert (older.status, newest.status) == ("superseded", "decided")
    assert "Covered by the newer commit aaaaaaa" in older.error


def test_the_first_change_on_the_default_branch_starts_before_its_oldest_commit(row_id, script):
    script.diffs[(f"{OLDEST}~1", HEAD)] = _diff("a.py", "x = 1\n")
    script.files = {"a.py": b"x = 1\n"}
    _record(row_id, [_push_line(OLDEST, branch="main"), _push_line(HEAD, branch="main")])
    _judge()
    [change] = _changes()
    assert (change.status, change.base) == ("decided", f"{OLDEST}~1")


def test_a_base_github_has_lost_falls_back_to_where_the_branch_began(row_id, script):
    script.diffs[("main", OLDER)] = _diff("a.py", "x = 1\n")
    script.diffs[("main", HEAD)] = _diff("a.py", "x = 1\n")  # (OLDER, HEAD) is missing: the base is gone
    script.files = {"a.py": b"x = 1\n"}
    _record(row_id, [_push_line(OLDER)])
    _judge()
    _record(row_id, [_push_line(HEAD)])
    _judge()
    assert [c.base for c in _changes()] == ["main", "main"] and _changes()[1].status == "decided"


def test_a_read_that_fails_waits_before_it_is_tried_again(row_id, script):
    _record(row_id, [_push_line(HEAD)])
    assert _judge() == 1
    assert _judge() == 0  # still resting: GitHub may need a moment
    assert [(c.status, c.attempts) for c in _changes()] == [("pending", 1)]


def test_a_read_that_fails_is_tried_again_then_given_up_with_the_reason(row_id, script, monkeypatch):
    monkeypatch.setattr(process, "RETRY_WAIT_S", 0)
    _record(row_id, [_push_line(HEAD)])
    for attempt in range(1, process.MAX_ATTEMPTS + 1):
        _judge()
        [change] = _changes()
        assert change.attempts == attempt and "could not find" in change.error
        assert change.status == ("pending" if attempt < process.MAX_ATTEMPTS else "failed")
    assert _judge() == 0  # a failed change is left alone


def test_a_change_judged_while_an_engine_failed_is_marked_incomplete_never_clean(row_id, script, monkeypatch):
    from app.decisions_api.suspect import Decision, EngineRun

    monkeypatch.setattr(process, "decide", lambda root, diff, **_: Decision("clean", [], [EngineRun("semgrep", "x", 0.0, "Timeout")]))
    script.diffs[("main", HEAD)] = _diff("a.py", "x = 1\n")
    script.files = {"a.py": b"x = 1\n"}
    _record(row_id, [_push_line(HEAD)])
    _judge()
    [change] = _changes()
    assert (change.status, change.verdict) == ("incomplete", "clean")
    assert change.decision["engines"][0]["error"] == "Timeout"


def test_a_change_left_working_by_a_process_that_died_is_taken_up_again(row_id, script):
    from datetime import timedelta

    from app.db import utcnow

    script.diffs[("main", HEAD)] = _diff("a.py", "x = 1\n")
    script.files = {"a.py": b"x = 1\n"}
    _record(row_id, [_push_line(HEAD)])
    with SessionLocal() as db:
        change = db.scalar(select(ChangeEvent))
        change.status, change.claimed_at = "working", utcnow() - timedelta(seconds=process.STALE_WORKING_S + 5)
        db.commit()
    assert _judge() == 1
    assert _changes()[0].status == "decided"


def test_a_change_being_worked_on_now_is_left_to_the_process_working_on_it(row_id, script):
    from app.db import utcnow

    _record(row_id, [_push_line(HEAD)])
    with SessionLocal() as db:
        change = db.scalar(select(ChangeEvent))
        change.status, change.claimed_at = "working", utcnow()
        db.commit()
    assert _judge() == 0 and script.asked == []


# ---------------------------------------------------------------- how it is reached


def _signed(body: bytes) -> dict:
    sign = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-GitHub-Event": "push", "X-GitHub-Delivery": "vs1-delivery", "X-Hub-Signature-256": sign,
            "Content-Type": "application/json"}


def _push_body(ref: str, after: str) -> bytes:
    return json.dumps({
        "ref": ref, "before": "0" * 40, "after": after, "installation": {"id": int(GITHUB_CONFIG["installation_id"])},
        "repository": {"full_name": REPO, "default_branch": "main", "html_url": URL, "pushed_at": 1792778400},
        "commits": [{"id": after, "message": "Add settings\n\nbody", "author": {"name": "deva-ghb"}}]}).encode()


def test_a_signed_push_is_kept_and_judged_in_the_background_and_a_redelivery_changes_nothing(client, row_id, script):
    script.diffs[("main", HEAD)] = _diff("shop/settings.py", LEAKY)
    script.files = {"shop/settings.py": LEAKY.encode()}
    body = _push_body(f"refs/heads/{BRANCH}", HEAD)
    first = client.post("/api/github/webhook", content=body, headers=_signed(body))
    assert first.status_code == 200 and first.json()["read"] == 1
    [change] = _changes()
    assert (change.source, change.status, change.verdict, change.head_sha) == ("webhook", "decided", "suspect", HEAD)
    again = client.post("/api/github/webhook", content=body, headers=_signed(body))
    assert again.json()["duplicate"] is True
    assert len(_changes()) == 1 and len([a for a in script.asked if a[0] == "diff"]) == 1


def test_a_forged_push_keeps_no_change_and_reads_nothing(client, row_id, script):
    body = _push_body(f"refs/heads/{BRANCH}", HEAD)
    forged = {**_signed(body), "X-Hub-Signature-256": "sha256=" + "0" * 64}
    assert client.post("/api/github/webhook", content=body, headers=forged).status_code == 401
    assert _changes() == [] and script.asked == []


def test_a_push_to_a_branch_the_poll_finds_is_kept_with_the_poll_as_its_source(row_id, script, monkeypatch):
    replay = HttpReplay(load("github", "read-twice"))
    monkeypatch.setitem(REGISTRY, "github", lambda config, secret: github(replay, lambda _s: None, **config))
    with SessionLocal() as db:
        cloudlogs.sync(db, db.get(Connector, row_id))
    replay.verify()
    [change] = _changes()
    with SessionLocal() as db:
        head = db.get(Connector, row_id).state["cursors"]["repos"][REPO]["head"]
    assert (change.source, change.branch, change.head_sha, change.status) == ("poll", "main", head, "pending")
    assert change.first_sha != change.head_sha  # the hour's commits, read as one change


def test_a_change_waits_when_it_cannot_be_read_yet_and_the_connection_is_not_blamed(client, row_id):
    # No App is set up in the tests, so GitHub cannot be read: the change stays waiting, with the reason.
    body = _push_body(f"refs/heads/{BRANCH}", HEAD)
    assert client.post("/api/github/webhook", content=body, headers=_signed(body)).status_code == 200
    [change] = _changes()
    assert change.status == "pending" and change.attempts == 1 and "GitHub App" in change.error

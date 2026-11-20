"""VS.4: a finding is an inline comment on the pull request's line, added once, and answered when a later commit
removes the weakness. A pull request opened after its commit was judged still gets its comments."""

import pytest
from test_changes import BRANCH, HEAD, LEAKY, REPO, URL, _changes, _diff, _push_line, _record
from test_findings import CLEAN_LINES, NEXT, CheckingGitHub, judge, modify, script  # noqa: F401 - script is a fixture

from app.changes import findings, process
from app.connectors.base import SourceError
from app.connectors.github import GitHub
from app.connectors.http import Answer
from app.db import SessionLocal

PULL = 7


def _opened(sha: str = HEAD) -> dict:
    return {"kind": "pull_request", "action": "opened", "pull_number": PULL, "repo": REPO, "url": URL, "sha": sha,
            "branch": BRANCH, "default_branch": "main", "pushed_at": "2026-10-26T18:00:01Z"}


def _leaky_push(fake):
    fake.diffs[("main", HEAD)] = _diff("shop/settings.py", LEAKY)
    fake.files = {"shop/settings.py": LEAKY.encode()}


# ---------------------------------------------------------------- what the judged change asks GitHub for


def test_a_finding_on_a_pull_request_is_one_inline_comment_on_its_line(row_id, script):  # noqa: F811
    _leaky_push(script)
    _record(row_id, [_push_line(HEAD), _opened()])
    judge()
    [asked] = script.comments
    assert (asked["repo"], asked["number"], asked["head"]) == (REPO, PULL, HEAD)
    [comment] = asked["comments"]
    assert (comment["path"], comment["line"]) == ("shop/settings.py", 1)
    assert comment["key"] == "whitehats.secrets.provider-live-key|shop/settings.py|"
    assert "high, CWE-798" in comment["body"] and HEAD[:7] in comment["body"]
    [change] = _changes()
    assert change.commented_at is not None and change.comment_error == ""


def test_a_pull_request_opened_after_the_commit_was_judged_still_gets_its_comment(row_id, script):  # noqa: F811
    _leaky_push(script)
    _record(row_id, [_push_line(HEAD)])
    judge()
    assert script.comments == []  # no pull request yet: nothing to comment on
    _record(row_id, [_opened()])
    judge()
    assert [asked["number"] for asked in script.comments] == [PULL]
    judge()
    assert len(script.comments) == 1  # once, not on every pass


def test_a_pull_request_opened_after_the_commit_was_judged_still_gets_its_ai_review(row_id, script, monkeypatch):  # noqa: F811
    from app.models import Audit
    from app.services import audit_jobs

    asked = []

    def request(db, org_id, project_id, trigger, detail="", scope=None, **kw):
        row = Audit(org_id=org_id, project_id=project_id, trigger=trigger, status="queued", change_id=kw["change_id"],
                    pull_number=kw["pull_number"], head_sha=kw["head_sha"])
        db.add(row)
        db.commit()
        asked.append((kw["pull_number"], kw["head_sha"]))
        return row

    monkeypatch.setattr(audit_jobs, "enabled", lambda: True)
    monkeypatch.setattr(audit_jobs, "request", request)
    _leaky_push(script)
    _record(row_id, [_push_line(HEAD)])
    judge()
    assert asked == []  # a push with no pull request has nothing to review
    _record(row_id, [_opened()])
    judge()
    judge()
    assert asked == [(PULL, HEAD)]  # asked once when the pull request appears, not on every pass
    with SessionLocal() as db:
        db.query(Audit).filter(Audit.pull_number == PULL, Audit.head_sha == HEAD).delete()
        db.commit()


def test_the_commit_that_removes_the_weakness_answers_its_comment(row_id, script):  # noqa: F811
    _leaky_push(script)
    _record(row_id, [_push_line(HEAD), _opened()])
    judge()
    # The fix is pushed to the same branch. GitHub has not yet said it belongs to the pull request.
    script.diffs[(HEAD, NEXT)] = modify("shop/settings.py", [LEAKY.strip()], [line for line in CLEAN_LINES.splitlines()])
    script.files = {"shop/settings.py": CLEAN_LINES.encode()}
    _record(row_id, [_push_line(NEXT)])
    judge()
    [reply] = script.replies
    assert (reply["number"], reply["keys"]) == (PULL, ["whitehats.secrets.provider-live-key|shop/settings.py|"])
    assert NEXT[:7] in reply["text"]


def test_a_refused_comment_is_kept_as_a_reason_and_the_findings_stand(row_id, script, monkeypatch):  # noqa: F811
    def refuse(self, repo, number, head_sha, comments):
        raise SourceError("The WhiteHats GitHub App does not hold pull request write, so no comment was added.")
    monkeypatch.setattr(CheckingGitHub, "comment_findings", refuse)
    _leaky_push(script)
    _record(row_id, [_push_line(HEAD), _opened()])
    judge()
    [change] = _changes()
    assert "pull request write" in change.comment_error and change.commented_at is None
    assert change.check_url  # the check run was still written
    with SessionLocal() as db:
        assert process.comment_late_pull_requests(db) == 0  # a refusal is not retried on every pass


def test_the_comment_text_says_what_was_found_and_when_it_was_seen():
    from types import SimpleNamespace
    item = {"status": "confirmed", "severity": "high", "explanation": "A live key is committed.",
            "suspect": {"rule_id": "r", "title": "Key in source", "cwe": ["CWE-798"],
                        "evidence": {"path": "a.py", "start_line": 3, "end_line": 4, "resource": ""}}}
    change = SimpleNamespace(confirmations=[item], head_sha="f" * 40)
    [comment] = findings.pull_request_comments(change)
    assert comment == {"key": "r|a.py|", "path": "a.py", "line": 4, "body": (
        "**WhiteHats: Key in source** (high, CWE-798)\n\nA live key is committed.\n\n"
        "Seen in commit `fffffff`. This comment is answered when a later commit removes it.")}


# ---------------------------------------------------------------- what the connector sends to GitHub


def _github(calls, *, existing=(), line_status=201, permissions=None):
    """The real connector with a transport that records every call; `existing` are the pull request's comments."""
    def transport(method, url, headers, body):
        calls.append((method, url.split("api.github.com")[-1], body))
        if url.endswith("/access_tokens"):
            return Answer(201, {}, {"token": "t", "expires_at": "2099-01-01T00:00:00+00:00", "permissions": permissions or {
                "metadata": "read", "contents": "read", "pull_requests": "write", "checks": "write"}})
        if method == "GET":
            return Answer(200, {}, list(existing))
        if body and "line" in body:
            return Answer(line_status, {}, {"message": "pull_request_review_thread.line must be part of the diff"})
        return Answer(201, {}, {"id": 99})
    return GitHub({"installation_id": "12345678"}, transport=transport, app_token=lambda: "jwt",
                  webhook_secret=lambda: "x", clock=lambda: 0.0, sleep=lambda seconds: None)


COMMENT = {"key": "rule|a.py|", "path": "a.py", "line": 3, "body": "**WhiteHats: Key in source**"}


def test_a_comment_goes_on_the_line_with_a_hidden_marker():
    calls = []
    assert _github(calls).comment_findings(REPO, PULL, HEAD, [COMMENT]) == 1
    method, path, body = calls[-1]
    assert (method, path) == ("POST", f"/repos/{REPO}/pulls/{PULL}/comments")
    assert (body["commit_id"], body["path"], body["line"], body["side"]) == (HEAD, "a.py", 3, "RIGHT")
    assert body["body"].endswith("<!-- whitehats:rule|a.py| -->")


def test_a_line_outside_the_diff_gets_a_comment_on_the_file_instead():
    calls = []
    _github(calls, line_status=422).comment_findings(REPO, PULL, HEAD, [COMMENT])
    posts = [body for method, _, body in calls if method == "POST" and "access_tokens" not in _]
    assert [("line" in body, body.get("subject_type")) for body in posts] == [(True, None), (False, "file")]


def test_a_finding_that_already_has_its_comment_is_not_commented_again():
    calls = []
    existing = [{"id": 5, "body": "**WhiteHats: Key in source**\n\n<!-- whitehats:rule|a.py| -->"}]
    assert _github(calls, existing=existing).comment_findings(REPO, PULL, HEAD, [COMMENT]) == 0
    assert not [call for call in calls if call[0] == "POST" and "/comments" in call[1]]


def test_a_fix_answers_the_finding_comment_once():
    calls = []
    existing = [{"id": 5, "body": "x\n\n<!-- whitehats:rule|a.py| -->"}]
    assert _github(calls, existing=existing).reply_fixed(REPO, PULL, ["rule|a.py|", "other|b.py|"], "Fixed in `abc`.") == 1
    method, path, body = calls[-1]
    assert (method, path) == ("POST", f"/repos/{REPO}/pulls/{PULL}/comments/5/replies")
    assert body["body"].endswith("<!-- whitehats:fixed:rule|a.py| -->")
    answered = [*existing, {"id": 6, "body": "Fixed\n\n<!-- whitehats:fixed:rule|a.py| -->"}]
    assert _github([], existing=answered).reply_fixed(REPO, PULL, ["rule|a.py|"], "Fixed again") == 0


def test_no_comment_without_pull_request_write():
    calls = []
    with pytest.raises(SourceError, match="pull request write"):
        _github(calls, permissions={"metadata": "read", "pull_requests": "read", "checks": "write"}).comment_findings(
            REPO, PULL, HEAD, [COMMENT])
    assert not [call for call in calls if call[0] == "POST" and "/comments" in call[1]]


def test_the_connector_writes_comments_and_replies_and_nothing_else_on_a_pull_request():
    github = _github([])
    for path in (f"/repos/{REPO}/pulls/7/reviews", f"/repos/{REPO}/pulls/7/merge", f"/repos/{REPO}/issues/7/comments",
                 f"/repos/{REPO}/pulls/x/comments", f"/repos/{REPO}/pulls/7/comments/5", f"/repos/{REPO}/pulls/7/comments/5/replies/1"):
        with pytest.raises(SourceError, match="does not write to"):
            github._post(path, {})


def test_two_passes_at_once_comment_once(monkeypatch):
    """The push's own pass and the late pass can comment on one pull request at the same moment (seen live on #12:
    two identical comments in the same second). comment_findings reads what is there, then posts what is missing."""
    import threading
    import time
    from types import SimpleNamespace

    from app.changes import findings as change_findings
    from app.changes import process

    class Racy:
        def __init__(self):
            self.posted = []

        def comment_findings(self, repo, number, head, comments):
            there = set(self.posted)
            time.sleep(0.05)  # the gap between reading and posting
            new = [item["key"] for item in comments if item["key"] not in there]
            self.posted += new
            return len(new)

        def reply_fixed(self, repo, number, keys, text):
            return 0

    monkeypatch.setattr(change_findings, "pull_request_comments", lambda change: [{"key": "k", "path": "a.py", "line": 1, "body": "b"}])
    monkeypatch.setattr(change_findings, "fixed_text", lambda change: "")
    source, db = Racy(), SimpleNamespace(commit=lambda: None)
    changes = [SimpleNamespace(repo="acme/shop", pull_number=12, head_sha="a" * 40, branch="b", connector_id=1) for _ in range(2)]
    threads = [threading.Thread(target=process._comment, args=(db, change, source, set())) for change in changes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert source.posted == ["k"]

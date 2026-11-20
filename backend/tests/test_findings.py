"""VS.4: what a judged change shows people. A confirmed finding is a live signal and a "WhiteHats" check run on the
commit, the same weakness in a later commit is the same finding, and a finding closes only when it is really gone."""

import pytest
from sqlalchemy import delete, select
from test_changes import (
    BRANCH,
    HEAD,
    KEY,
    LEAKY,
    REPO,
    Script,
    ScriptedGitHub,
    _changes,
    _diff,
    _push_line,
    _record,
)

from app import changes
from app.changes import findings, process
from app.confirmation.verdict import Confirmation
from app.connectors import REGISTRY
from app.connectors.base import SourceError
from app.db import SessionLocal
from app.decisions_api import decide
from app.decisions_api.suspect import Decision, EngineRun
from app.models import Signal
from app.services import posture

NEXT = "d" * 40
LAST = "e" * 40
CLEAN_LINES = "import os\nTOKEN = os.environ['TOKEN']\n"


class CheckingGitHub(ScriptedGitHub):
    """The scripted GitHub that also records the check runs written to it (or refuses them)."""

    def publish_check(self, repo, head_sha, *, conclusion, title, summary, annotations):
        if self.script.refuse_check:
            raise SourceError(self.script.refuse_check)
        self.script.checks.append(
            {
                "repo": repo,
                "head": head_sha,
                "conclusion": conclusion,
                "title": title,
                "summary": summary,
                "annotations": annotations,
            }
        )
        return f"https://github.com/{repo}/runs/{len(self.script.checks)}"

    def comment_findings(self, repo, number, head_sha, comments):
        self.script.comments.append({"repo": repo, "number": number, "head": head_sha, "comments": comments})
        return len(comments)

    def reply_fixed(self, repo, number, keys, text):
        if not keys:
            return 0
        self.script.replies.append({"number": number, "keys": list(keys), "text": text})
        return len(keys)


@pytest.fixture
def script(monkeypatch):
    script = Script()
    script.checks, script.refuse_check = [], ""
    script.comments, script.replies = [], []
    monkeypatch.setitem(REGISTRY, "github", lambda config, secret: CheckingGitHub(config, secret, script=script))
    monkeypatch.setattr(process, "decide", lambda root, diff, **_: decide(root, diff, scanners=()))
    yield script
    with SessionLocal() as db:
        db.execute(delete(Signal).where(Signal.source == findings.SOURCE))
        db.commit()


def confirmed(explanation="A live key is committed to the repository."):
    """A confirmer that confirms every escalating suspect, with the model's reasoning."""
    return lambda decision, root, diff: [
        Confirmation(s, "confirmed", s.severity, explanation=explanation, category="vulnerability")
        for s in decision.escalating
    ]


def judge(confirmer=None) -> int:
    with SessionLocal() as db:
        return changes.process_pending(db, confirmer=confirmer or confirmed())


def signals() -> list[Signal]:
    with SessionLocal() as db:
        rows = db.scalars(select(Signal).where(Signal.source == findings.SOURCE).order_by(Signal.id)).all()
        db.expunge_all()
        return list(rows)


def modify(path: str, old: list[str], new: list[str], *, context: list[str] | None = None) -> str:
    """A diff that replaces some lines of one file, with unchanged lines around them when given."""
    before = [*(context or []), *old]
    after = [*(context or []), *new]
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,{len(before)} +1,{len(after)} @@\n"
        + "".join(f" {line}\n" for line in context or [])
        + "".join(f"-{line}\n" for line in old)
        + "".join(f"+{line}\n" for line in new)
    )


def remove(path: str) -> str:
    return f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"


def first_commit(row_id, script):
    """A branch whose first commit adds a file with a live key in it."""
    script.diffs[("main", HEAD)] = _diff("shop/settings.py", LEAKY)
    script.files = {"shop/settings.py": LEAKY.encode()}
    _record(row_id, [_push_line(HEAD)])
    judge()


# ---------------------------------------------------------------- a finding becomes a signal and a check run


def test_a_confirmed_finding_is_a_live_signal_with_the_file_line_explanation_and_commit(row_id, script):
    first_commit(row_id, script)
    [signal] = [s for s in signals() if s.project_id == signals()[0].project_id]
    assert (signal.origin, signal.source, signal.severity) == ("live", "whitehats:code", "high")
    assert (signal.entity, signal.branch, signal.resource) == (
        "shop/settings.py:1",
        BRANCH,
        f"https://github.com/{REPO}",
    )
    assert "A live key is committed to the repository." in signal.detail
    assert f"https://github.com/{REPO}/commit/{HEAD}" in signal.detail and BRANCH in signal.detail
    assert signal.closed_at is None and signal.fingerprint.endswith("|shop/settings.py|")
    assert KEY not in signal.detail and KEY not in signal.title  # the secret is never shown


def test_a_finding_reaches_every_project_that_holds_the_repository(row_id, script):
    first_commit(row_id, script)
    [change] = _changes()
    assert sorted(s.project_id for s in signals()) == sorted(change.project_ids)


def test_the_check_run_names_the_finding_on_its_line_and_fails_on_a_high_one(row_id, script):
    first_commit(row_id, script)
    [check] = script.checks
    assert (check["repo"], check["head"], check["conclusion"]) == (REPO, HEAD, "failure")
    assert check["title"] == "WhiteHats found 1 finding to review"
    [note] = check["annotations"]
    assert (note["path"], note["start_line"], note["end_line"], note["annotation_level"]) == (
        "shop/settings.py",
        1,
        1,
        "failure",
    )
    assert "A live key is committed" in note["message"] and KEY not in str(check)
    [change] = _changes()
    assert change.check_url.endswith("/runs/1") and change.check_error == ""


def test_a_clean_change_gets_a_passing_check_run_and_no_signal(row_id, script):
    script.diffs[("main", HEAD)] = _diff("shop/ok.py", CLEAN_LINES)
    script.files = {"shop/ok.py": CLEAN_LINES.encode()}
    _record(row_id, [_push_line(HEAD)])
    judge()
    [check] = script.checks
    assert (check["conclusion"], check["title"], check["annotations"]) == (
        "success",
        "WhiteHats found nothing to review",
        [],
    )
    assert "none holds a finding" in check["summary"] and signals() == []


def test_a_medium_finding_is_neutral_and_a_change_that_could_not_finish_is_never_a_pass(row_id, script, monkeypatch):
    monkeypatch.setattr(
        process, "decide", lambda root, diff, **_: Decision("clean", [], [EngineRun("semgrep", "x", 0.0, "Timeout")])
    )
    script.diffs[("main", HEAD)] = _diff("a.py", "x = 1\n")
    script.files = {"a.py": b"x = 1\n"}
    _record(row_id, [_push_line(HEAD)])
    judge()
    [check] = script.checks
    assert check["conclusion"] == "neutral" and check["title"] == "WhiteHats could not check everything"
    assert "not a clean bill of health" in check["summary"]


def test_a_check_run_that_cannot_be_written_keeps_the_finding_and_says_why(row_id, script):
    script.refuse_check = "The WhiteHats GitHub App does not hold the checks permission, so no check run was written."
    first_commit(row_id, script)
    [change] = _changes()
    assert change.check_url == "" and "does not hold the checks permission" in change.check_error
    assert len(signals()) >= 1 and change.status == "decided"


def test_a_disputed_high_finding_stays_at_full_severity_and_says_a_model_disagreed(row_id, script):
    def disputing(decision, root, diff):
        return [
            Confirmation(s, "disputed", s.severity, explanation="Looks like a test key.", category="false_positive")
            for s in decision.escalating
        ]

    script.diffs[("main", HEAD)] = _diff("shop/settings.py", LEAKY)
    script.files = {"shop/settings.py": LEAKY.encode()}
    _record(row_id, [_push_line(HEAD)])
    judge(disputing)
    [signal, *_] = signals()
    assert signal.severity == "high" and "a model disagreed; kept at full severity" in signal.detail
    assert script.checks[0]["conclusion"] == "failure"


def test_only_a_rejected_finding_is_left_out(row_id):
    shown = findings.open_items(
        [{"status": "rejected"}, {"status": "confirmed"}, {"status": "unconfirmed"}, {"status": "disputed"}]
    )
    assert [item["status"] for item in shown] == ["confirmed", "unconfirmed", "disputed"]


def test_with_no_model_a_finding_is_reported_as_unconfirmed_never_dropped(row_id, script):
    script.diffs[("main", HEAD)] = _diff("shop/settings.py", LEAKY)
    script.files = {"shop/settings.py": LEAKY.encode()}
    _record(row_id, [_push_line(HEAD)])
    with SessionLocal() as db:
        changes.process_pending(db)  # the real confirmer: no model is connected in a test company
    [signal, *_] = signals()
    assert signal.severity == "high" and "not confirmed by a model: none answered" in signal.detail


# ---------------------------------------------------------------- the same weakness is the same finding


def test_the_same_weakness_in_a_later_commit_is_one_finding_that_follows_it_to_its_new_line(row_id, script):
    first_commit(row_id, script)
    before = signals()
    other_key = "pgsk_" + "live_" + "a1b2c3d4e5f6g7h8i9j0"
    moved = f'# rotated\nTOKEN = "{other_key}"\n'
    script.diffs[(HEAD, NEXT)] = (
        "diff --git a/shop/settings.py b/shop/settings.py\n--- a/shop/settings.py\n+++ b/shop/settings.py\n"
        f'@@ -1,1 +1,2 @@\n-{LEAKY.rstrip(chr(10))}\n+# rotated\n+TOKEN = "{other_key}"\n'
    )
    script.files["shop/settings.py"] = moved.encode()
    _record(row_id, [_push_line(NEXT)])
    judge()
    after = signals()
    assert [s.id for s in after] == [s.id for s in before]  # nothing new was opened
    assert all(s.closed_at is None and s.entity == "shop/settings.py:2" for s in after)
    assert all(f"/commit/{NEXT}" in s.detail for s in after)  # it points at the commit that has it now


# ---------------------------------------------------------------- a finding closes when it is really gone


def test_a_later_commit_that_removes_the_weakness_closes_the_finding_and_says_so(row_id, script):
    first_commit(row_id, script)
    script.diffs[(HEAD, NEXT)] = modify("shop/settings.py", [LEAKY.rstrip("\n")], [CLEAN_LINES.splitlines()[1]])
    script.files["shop/settings.py"] = CLEAN_LINES.encode()
    _record(row_id, [_push_line(NEXT)])
    judge()
    rows = signals()
    assert rows and all(s.closed_at is not None and s.closed_reason == findings.FIXED for s in rows)
    [_, second] = script.checks
    assert second["conclusion"] == "success" and second["head"] == NEXT
    with SessionLocal() as db:
        assert db.scalar(select(Signal.id).where(Signal.source == findings.SOURCE, Signal.closed_at.is_(None))) is None


def test_a_later_commit_that_touches_the_file_but_leaves_the_weakness_does_not_close_it(row_id, script):
    first_commit(row_id, script)
    # Line 1 is added above the key. The diff check sees only that new line, which holds no weakness, but the key is
    # still there: calling this fixed would be wrong, so the touched file is checked whole.
    script.diffs[(HEAD, NEXT)] = (
        "diff --git a/shop/settings.py b/shop/settings.py\n--- a/shop/settings.py\n+++ b/shop/settings.py\n"
        f"@@ -1,1 +1,2 @@\n+# a comment above the key\n {LEAKY.rstrip(chr(10))}\n"
    )
    script.files["shop/settings.py"] = ("# a comment above the key\n" + LEAKY).encode()
    _record(row_id, [_push_line(NEXT)])
    judge()
    rows = signals()
    assert rows and all(s.closed_at is None for s in rows)
    assert script.checks[-1]["conclusion"] == "success"  # that commit adds nothing; the old finding is not its own


def test_a_commit_that_does_not_touch_the_file_leaves_the_finding_open(row_id, script):
    first_commit(row_id, script)
    script.diffs[(HEAD, NEXT)] = _diff("docs/readme.md", "hello\n")
    script.files["docs/readme.md"] = b"hello\n"
    _record(row_id, [_push_line(NEXT)])
    judge()
    assert all(s.closed_at is None for s in signals())
    assert script.checks[-1]["conclusion"] == "success"  # this commit holds nothing; the old finding is not its own


def test_a_later_commit_that_deletes_the_file_closes_the_finding(row_id, script):
    first_commit(row_id, script)
    script.diffs[(HEAD, NEXT)] = remove("shop/settings.py") + _diff("docs/readme.md", "hello\n")
    script.files["docs/readme.md"] = b"hello\n"
    _record(row_id, [_push_line(NEXT)])
    judge()
    rows = signals()
    assert rows and all(s.closed_reason == findings.FILE_REMOVED for s in rows)


def test_a_change_an_engine_could_not_finish_never_closes_a_finding_because_it_is_absent(row_id, script, monkeypatch):
    first_commit(row_id, script)

    def partly_failed(root, diff, **_):
        # The diff check did not finish; the whole-file check did, and finds nothing: still not proof of a fix.
        if diff is None:
            return decide(root, None, scanners=())
        return Decision("clean", [], [EngineRun("semgrep", "x", 0.0, "Timeout")])

    monkeypatch.setattr(process, "decide", partly_failed)
    script.diffs[(HEAD, NEXT)] = modify("shop/settings.py", [LEAKY.rstrip("\n")], [CLEAN_LINES.splitlines()[1]])
    script.files["shop/settings.py"] = CLEAN_LINES.encode()
    _record(row_id, [_push_line(NEXT)])
    judge()
    [change] = [c for c in _changes() if c.head_sha == NEXT]
    assert change.status == "incomplete"
    assert all(s.closed_at is None for s in signals())
    assert script.checks[-1]["conclusion"] == "neutral"  # and the check run says so too


def test_a_deleted_file_closes_its_finding_even_when_an_engine_did_not_finish(row_id, script, monkeypatch):
    first_commit(row_id, script)
    monkeypatch.setattr(process, "decide", lambda root, diff, **_: Decision("clean", [], [EngineRun("semgrep", "x", 0.0, "Timeout")]))
    script.diffs[(HEAD, NEXT)] = remove("shop/settings.py") + _diff("docs/readme.md", "hello\n")
    script.files["docs/readme.md"] = b"hello\n"
    _record(row_id, [_push_line(NEXT)])
    judge()
    assert all(s.closed_reason == findings.FILE_REMOVED for s in signals())


def test_a_deleted_branch_closes_its_findings_and_leaves_other_branches_alone(row_id, script):
    first_commit(row_id, script)
    script.diffs[("main", NEXT)] = _diff("shop/settings.py", LEAKY)
    script.files["shop/settings.py"] = LEAKY.encode()
    _record(row_id, [_push_line(NEXT, branch="demo/keep")])
    judge()
    gone = {"source": f"github:{REPO}", "kind": "branch", "repo": REPO, "branch": BRANCH, "message": "branch deleted"}
    with SessionLocal() as db:
        from app.models import Connector

        changes.record(db, db.get(Connector, row_id), [gone], source="webhook")
        db.commit()
    by_branch = {}
    for row in signals():
        by_branch.setdefault(row.branch, []).append(row)
    assert all(s.closed_reason == findings.BRANCH_DELETED for s in by_branch[BRANCH])
    assert all(s.closed_at is None for s in by_branch["demo/keep"])


def test_a_closed_finding_is_not_counted_as_open_and_a_new_one_after_it_is_a_new_signal(row_id, script):
    first_commit(row_id, script)
    project = signals()[0].project_id
    with SessionLocal() as db:
        assert posture.open_findings(db, project) >= 1
    script.diffs[(HEAD, NEXT)] = modify("shop/settings.py", [LEAKY.rstrip("\n")], [CLEAN_LINES.splitlines()[1]])
    script.files["shop/settings.py"] = CLEAN_LINES.encode()
    _record(row_id, [_push_line(NEXT)])
    judge()
    with SessionLocal() as db:
        closed = posture.open_findings(db, project)
    script.diffs[(NEXT, LAST)] = modify("shop/settings.py", [CLEAN_LINES.splitlines()[1]], [LEAKY.rstrip("\n")])
    script.files["shop/settings.py"] = LEAKY.encode()
    _record(row_id, [_push_line(LAST)])
    judge()
    with SessionLocal() as db:
        assert posture.open_findings(db, project) == closed + 1
    assert sum(1 for s in signals() if s.project_id == project) == 2  # the old one stays on record, closed


def test_the_deleted_paths_are_read_from_the_diff_text_and_nothing_else(row_id):
    diff = remove("old/a.py") + _diff("new/b.py", "x = 1\n") + remove("old/c.py")
    assert findings.deleted_paths(diff) == {"old/a.py", "old/c.py"}
    assert findings.deleted_paths("") == set()


def test_the_fingerprint_ignores_the_line_but_not_the_rule_file_or_resource(row_id):
    base = {"rule_id": "r", "evidence": {"path": "a.py", "start_line": 3, "end_line": 3, "resource": ""}}
    moved = {"rule_id": "r", "evidence": {"path": "a.py", "start_line": 40, "end_line": 41, "resource": ""}}
    other_file = {"rule_id": "r", "evidence": {"path": "b.py", "start_line": 3, "end_line": 3, "resource": ""}}
    other_rule = {"rule_id": "s", "evidence": {"path": "a.py", "start_line": 3, "end_line": 3, "resource": ""}}
    resource = {
        "rule_id": "r",
        "evidence": {"path": "a.py", "start_line": 3, "end_line": 3, "resource": "aws_s3_bucket.x"},
    }
    assert findings.fingerprint(base) == findings.fingerprint(moved)
    assert len({findings.fingerprint(x) for x in (base, other_file, other_rule, resource)}) == 4


# ---------------------------------------------------------------- the connector's one write, and the poll's report of a deleted branch


def _github(posted, *, permissions=None, answer_status=201):
    """The real GitHub connector with a transport that records every call and answers the sign-in itself."""
    from app.connectors.base import SourceError as _  # noqa: F401
    from app.connectors.github import GitHub
    from app.connectors.http import Answer

    def transport(method, url, headers, body):
        posted.append((method, url, body))
        if url.endswith("/access_tokens"):
            return Answer(
                201,
                {},
                {
                    "token": "t",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "permissions": {"metadata": "read", "contents": "read", "checks": "write"}
                    if permissions is None
                    else permissions,
                },
            )
        return Answer(answer_status, {}, {"html_url": f"https://github.com/{REPO}/runs/7"})

    return GitHub(
        {"installation_id": "12345678"},
        transport=transport,
        app_token=lambda: "jwt",
        webhook_secret=lambda: "x",
        clock=lambda: 0.0,
        sleep=lambda seconds: None,
    )


NOTE = {"path": "a.py", "start_line": 1, "end_line": 1, "annotation_level": "failure", "title": "t", "message": "m"}


def test_a_check_run_is_posted_to_the_repository_check_runs_address_and_nowhere_else():
    posted = []
    url = _github(posted).publish_check(REPO, HEAD, conclusion="failure", title="T", summary="S", annotations=[NOTE])
    assert url.endswith("/runs/7")
    method, address, body = posted[-1]
    assert (method, address) == ("POST", f"https://api.github.com/repos/{REPO}/check-runs")
    assert (body["name"], body["head_sha"], body["status"], body["conclusion"]) == (
        "WhiteHats",
        HEAD,
        "completed",
        "failure",
    )
    assert body["output"]["annotations"] == [NOTE]


def test_the_connector_refuses_any_other_write_address():
    github = _github([])
    for path in (
        "/repos/a/b/issues",
        "/repos/a/b/check-runs/9",
        "/user/repos",
        "/repos//b/check-runs",
        "/repos/a/b/pulls/1/reviews",
    ):
        with pytest.raises(SourceError, match="does not write to"):
            github._post(path, {})


def test_a_check_run_needs_the_checks_permission_the_installation_really_holds():
    posted = []
    with pytest.raises(SourceError, match="does not hold the checks permission"):
        _github(posted, permissions={"metadata": "read", "checks": "read"}).publish_check(
            REPO, HEAD, conclusion="success", title="T", summary="S", annotations=[]
        )
    assert not any(call[1].endswith("/check-runs") for call in posted)  # nothing was posted


def test_a_check_run_cannot_end_in_a_way_github_does_not_know():
    with pytest.raises(SourceError, match="cannot end as"):
        _github([]).publish_check(REPO, HEAD, conclusion="cancelled", title="T", summary="S", annotations=[])


def test_a_long_list_of_findings_is_cut_to_what_github_accepts_and_the_summary_says_so():
    posted = []
    _github(posted).publish_check(REPO, HEAD, conclusion="failure", title="T", summary="S", annotations=[NOTE] * 73)
    body = posted[-1][2]
    assert (
        len(body["output"]["annotations"]) == 50 and "23 more finding(s) are in WhiteHats." in body["output"]["summary"]
    )


def test_a_refused_check_run_is_a_clear_error_not_a_crash():
    with pytest.raises(SourceError, match="denied"):
        _github([], answer_status=403).publish_check(
            REPO, HEAD, conclusion="success", title="T", summary="S", annotations=[]
        )


def test_the_poll_reports_a_branch_that_is_gone_the_same_way_a_webhook_does(monkeypatch):
    github = _github([])
    repo = {"full_name": REPO, "default_branch": "main", "html_url": f"https://github.com/{REPO}"}
    monkeypatch.setattr(
        github,
        "_pages",
        lambda path, *a, **k: iter(
            [{"name": "main", "commit": {"sha": "1" * 40}}, {"name": "demo/stays", "commit": {"sha": "2" * 40}}]
        ),
    )
    lines = []
    before = {"branches": {"main": "1" * 40, "demo/stays": "2" * 40, "demo/gone": "3" * 40}}
    github._read_branch_heads(repo, before, {"head": "1" * 40, "seen": []}, lines)
    assert [(line["kind"], line["branch"], line["repo"]) for line in lines] == [("branch", "demo/gone", REPO)]


def test_a_push_keeps_its_scan_plan_and_its_findings_point_at_it(row_id, script):
    """SCAN.1: the plan names the changed file with the catalogue scanners that read it, and why."""
    from app.models import ChangeEvent, ScanPlanRecord

    first_commit(row_id, script)
    with SessionLocal() as db:
        change = db.scalar(select(ChangeEvent).order_by(ChangeEvent.id.desc()))
        plan = db.get(ScanPlanRecord, change.scan_plan_id)
        assert (plan.kind, plan.repo, plan.change_id, plan.refused) == ("push", REPO, change.id, "")
        assert plan.plan["targets"] == [{"repo": REPO, "path": "shop/settings.py", "scanners": ["gitleaks", "semgrep"],
                                         "reason": "changed in this push"}]
        db.execute(delete(ScanPlanRecord))
        db.commit()
    assert {signal.scan_plan_id for signal in signals()} == {plan.id}


def test_a_review_shows_on_the_pull_request_while_it_waits_and_runs():
    posted = []
    _github(posted).publish_check(REPO, HEAD, status="in_progress", title="Reviewing the change", summary="S",
                                  annotations=[], name="WhiteHats AI review")
    _, _, body = posted[-1]
    assert body["status"] == "in_progress" and "conclusion" not in body and body["name"] == "WhiteHats AI review"
    with pytest.raises(SourceError):
        _github([]).publish_check(REPO, HEAD, status="paused", title="T", summary="S", annotations=[])

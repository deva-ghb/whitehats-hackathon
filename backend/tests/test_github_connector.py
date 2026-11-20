"""GitHub connector: what discovery finds on the proving ground, least access, and one report per commit."""

import json

import pytest
from connector_contract import GITHUB_PUSH, github
from connector_kit import HttpReplay, load

from app.connectors.base import SourceError
from app.connectors.github_app import APP_PERMISSIONS, excess_permissions
from app.connectors.github_events import absorb, event_lines
from app.connectors.http import Answer, next_link

REPO = "deva-ghb/whitehats-proving-ground"


def _branch_calls() -> list[dict]:
    """The two recorded pages of the proving ground's branch list (main and two others)."""
    return [c for c in load("github", "discover-ok")["calls"] if "/branches" in c["url"]]


def _source(scenario: str, calls: list | None = None):
    recording = load("github", scenario)
    if calls is not None:
        recording = {**recording, "calls": calls}
    replay = HttpReplay(recording)
    return github(replay, lambda _s: None), replay


def test_discovery_finds_the_repo_its_workflows_and_its_manifests():
    source, replay = _source("discover-ok")
    found = source.discover()
    replay.verify()
    repo, actions = found.nodes
    assert repo["type"] == "repo.github@1" and repo["name"] == REPO
    props = repo["properties"]
    assert props["default_branch"] == "main" and props["repo"] == REPO
    assert props["branch_protection"] is False  # a free private repository cannot protect a branch
    assert props["codeowners"] is False
    assert "main" in props["branches"] and len(props["branches"]) == 3
    assert {"loader/pyproject.toml", "shop/uv.lock", "worker/pyproject.toml",
            "terraform/.terraform.lock.hcl"} <= set(props["dependency_manifests"])
    assert all(not path.endswith(".tf") for path in props["dependency_manifests"])
    assert actions["type"] == "delivery.github-actions@1"
    assert actions["properties"]["workflows"] == [".github/workflows/ci.yml", ".github/workflows/deploy.yml"]
    assert found.edges == [{"from": repo["id"], "to": actions["id"], "type": "built_by"}]


def test_an_app_with_more_access_than_it_needs_is_refused():
    recording = load("github", "test-ok")
    token = dict(recording["calls"][0])
    token["body"] = {**token["body"], "permissions": {**APP_PERMISSIONS, "contents": "write"}}
    source, _ = _source("test-ok", [token])
    with pytest.raises(SourceError, match=r"more access than it needs \(contents: write\)"):
        source.test()


def test_excess_permissions_names_each_one_above_the_app_list():
    assert excess_permissions(dict(APP_PERMISSIONS)) == []
    assert excess_permissions({"metadata": "read", "contents": "read"}) == []
    assert excess_permissions({"administration": "read", "checks": "write", "actions": "write"}) == [
        "actions: write", "administration: read"]


def test_no_installation_means_install_the_app_never_a_personal_token():
    source, _ = _source("test-ok", [])
    source.config.pop("installation_id")
    with pytest.raises(SourceError, match="Install the WhiteHats GitHub App"):
        source.test()


def test_a_used_up_rate_limit_waits_until_github_says_it_resets():
    calls = load("github", "read-twice")["calls"]
    clock = load("github", "read-twice")["clock"]
    limited = {**calls[1], "status": 403, "headers": {"x-ratelimit-remaining": "0",
                                                     "x-ratelimit-reset": str(int(clock) + 7)},
               "body": {"message": "API rate limit exceeded"}}
    waits: list[float] = []
    replay = HttpReplay({"clock": clock, "calls": [calls[0], limited, *calls[1:]]})
    batch = github(replay, waits.append).read({})
    replay.verify()
    unlimited = _source("read-twice")[0].read({})
    assert 7.0 in waits and len(batch.lines) == len(unlimited.lines) > 0


def test_an_empty_repository_has_no_commits_and_no_files():
    calls = load("github", "read-twice")["calls"]
    empty = {**calls[2], "status": 409, "headers": {}, "body": {"message": "Git Repository is empty."}}
    no_branches = {**calls[2], "url": f"https://api.github.com/repos/{REPO}/branches?per_page=2",
                   "status": 200, "headers": {}, "body": []}
    source, replay = _source("read-twice", [calls[0], calls[1], empty, no_branches])
    batch = source.read({})
    replay.verify()
    assert batch.lines == [] and batch.bookmark["repos"][REPO]["head"] is None


def test_a_poll_after_a_push_reads_only_the_new_commit():
    source, _ = _source("read-twice")
    first = source.read({})
    calls = load("github", "read-twice")["calls"]
    newer = {"sha": "a" * 40, "commit": {"message": "Add a route\n\nbody", "author": {"name": "Asha"},
                                        "committer": {"date": "2026-10-23T17:10:00Z"}}}
    head = first.bookmark["repos"][REPO]["head"]
    repos = {**calls[1], "body": {**calls[1]["body"], "repositories": [
        {**calls[1]["body"]["repositories"][0], "pushed_at": "2026-10-23T17:10:05Z"}]}}
    page = {"method": "GET", "url": f"https://api.github.com/repos/{REPO}/commits?sha=main&per_page=2",
            "status": 200, "headers": {"link": "<next>; rel=\"next\""},
            "body": [newer, {"sha": head, "commit": {}}]}
    again, replay = _source("read-twice", [calls[0], repos, page, *_branch_calls()])
    batch = again.read(first.bookmark)
    replay.verify()
    assert [line["sha"] for line in batch.lines] == ["a" * 40]
    assert batch.lines[0]["message"] == "push to main: aaaaaaa Add a route by Asha"
    assert batch.bookmark["repos"][REPO]["head"] == "a" * 40


def test_the_first_read_notes_every_branch_head_and_reports_none_of_them():
    source, _ = _source("read-twice")
    first = source.read({})
    heads = first.bookmark["repos"][REPO]["branches"]
    assert set(heads) == {"main", "claude-1c/PG.1.2", "claude-1c/R-044"} and all(len(h) == 40 for h in heads.values())
    assert all(line["branch"] == "main" for line in first.lines)


def _moved_branch_read(heads_before: dict, *, seen: list[str] = ()):
    """A poll where the repository was pushed to and its branch list is the recorded one."""
    calls = load("github", "read-twice")["calls"]
    repos = {**calls[1], "body": {**calls[1]["body"], "repositories": [
        {**calls[1]["body"]["repositories"][0], "pushed_at": "2026-10-26T17:00:00Z"}]}}
    head = "f" * 40
    page = {"method": "GET", "url": f"https://api.github.com/repos/{REPO}/commits?sha=main&per_page=2",
            "status": 200, "headers": {}, "body": [{"sha": head, "commit": {}}]}
    source, replay = _source("read-twice", [calls[0], repos, page, *_branch_calls()])
    mark = {"head": head, "seen": [head, *seen], "pushed_at": "2026-10-26T16:00:00Z", "branches": heads_before}
    batch = source.read({"repos": {REPO: mark}})
    replay.verify()
    return batch


def test_a_branch_whose_head_moved_is_reported_once_with_its_new_head():
    heads = {name: sha for name, sha in
             ((b["name"], b["commit"]["sha"]) for call in _branch_calls() for b in call["body"])}
    moved = {**heads, "claude-1c/R-044": "0" * 40}
    batch = _moved_branch_read(moved)
    assert [(line["branch"], line["sha"], line["kind"]) for line in batch.lines] == [
        ("claude-1c/R-044", heads["claude-1c/R-044"], "commit")]
    assert batch.lines[0]["default_branch"] == "main" and batch.lines[0]["repo"] == REPO
    assert batch.bookmark["repos"][REPO]["branches"] == heads
    assert _moved_branch_read(heads).lines == []  # nothing moved: nothing reported


def test_a_new_branch_is_reported_unless_its_head_was_already_reported_by_a_webhook():
    heads = {b["name"]: b["commit"]["sha"] for call in _branch_calls() for b in call["body"]}
    without = {name: sha for name, sha in heads.items() if name != "claude-1c/PG.1.2"}
    assert [line["branch"] for line in _moved_branch_read(without).lines] == ["claude-1c/PG.1.2"]
    already = _moved_branch_read(without, seen=[heads["claude-1c/PG.1.2"]])
    assert already.lines == []


def test_a_commit_seen_by_webhook_is_not_reported_again_by_the_poll():
    lines = event_lines("push", json.loads(GITHUB_PUSH))
    fresh, bookmark = absorb({}, lines)
    assert [line["sha"] for line in fresh] == ["58d7dc787d3aea48e584737fa88f43d752913de8"]
    again, same = absorb(bookmark, lines)
    assert again == [] and same == bookmark
    mark = bookmark["repos"][REPO]
    assert mark["head"] == "58d7dc787d3aea48e584737fa88f43d752913de8"
    assert mark["pushed_at"] == "2026-10-23T16:53:20Z"


def test_a_push_to_another_branch_does_not_move_the_polled_head():
    payload = json.loads(GITHUB_PUSH)
    payload["ref"] = "refs/heads/feature"
    fresh, bookmark = absorb({}, event_lines("push", payload))
    assert len(fresh) == 1 and bookmark["repos"][REPO]["head"] is None


@pytest.mark.parametrize(("event", "payload", "message"), [
    ("pull_request", {"action": "opened", "pull_request": {"number": 7, "title": "Add refunds",
                                                           "head": {"sha": "b" * 40, "ref": "refunds"}}},
     "pull request #7 opened: Add refunds (bbbbbbb)"),
    ("workflow_run", {"action": "completed", "workflow_run": {"name": "plan", "conclusion": "failure",
                                                              "head_branch": "main", "head_sha": "c" * 40}},
     "workflow plan failure on main (ccccccc)"),
    ("push", {"ref": "refs/heads/old", "deleted": True, "after": "0" * 40, "commits": []}, "branch old deleted"),
    ("push", {"ref": "refs/tags/v1.0", "after": "d" * 40, "commits": []}, "tag v1.0 pushed"),
    ("push", {"ref": "refs/heads/new", "after": "e" * 40, "commits": []}, "push to new: eeeeeee"),
])
def test_each_event_reads_as_one_plain_line(event, payload, message):
    repo = {"full_name": REPO, "default_branch": "main", "html_url": f"https://github.com/{REPO}"}
    lines = event_lines(event, {**payload, "repository": repo})
    assert [line["message"] for line in lines] == [message]


@pytest.mark.parametrize("event", ["ping", "installation", "workflow_run", "star"])
def test_events_that_say_nothing_new_add_no_lines(event):
    repo = {"full_name": REPO}
    assert event_lines(event, {"action": "requested", "repository": repo}) == []


def test_next_link_reads_only_the_next_page():
    headers = {"link": '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=9>; rel="last"'}
    assert next_link(headers) == "https://api.github.com/x?page=2"
    assert next_link({"link": '<https://api.github.com/x?page=1>; rel="prev"'}) is None
    assert next_link({}) is None


def test_github_errors_read_as_plain_words():
    calls = load("github", "test-ok")["calls"]
    hidden = {**calls[1], "status": 404, "headers": {}, "body": {"message": "Not Found"}}
    source, _ = _source("test-ok", [calls[0], hidden])
    with pytest.raises(SourceError, match="could not find /installation/repositories"):
        source.read({})


def test_answers_keep_their_headers_in_lower_case():
    assert Answer(200, {"link": "x"}).headers["link"] == "x"


def _reading_source(*answers: dict):
    """A source whose next calls are these answers, after the token exchange; it also keeps each Accept header."""
    calls = [load("github", "read-twice")["calls"][0], *answers]
    replay = HttpReplay({**load("github", "read-twice"), "calls": calls})
    accepted: list[str] = []

    def transport(method, url, headers, body):
        accepted.append(headers["Accept"])
        return replay(method, url, headers, body)

    source = github(replay, lambda _s: None)
    source._transport = transport
    return source, replay, accepted


def test_a_diff_is_read_as_text_against_the_merge_base_of_a_branch():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    url = f"https://api.github.com/repos/{REPO}/compare/claude-1c%2FPG.1.2...{'a' * 40}"
    source, replay, accepted = _reading_source(
        {"method": "GET", "url": url, "status": 200, "headers": {}, "body": None, "text": diff})
    assert source.compare_diff(REPO, "claude-1c/PG.1.2", "a" * 40) == diff
    replay.verify()
    assert accepted[-1] == "application/vnd.github.diff"


def test_a_file_is_read_at_the_commit_as_bytes_and_a_big_or_odd_one_is_refused_with_the_reason():
    import base64

    def contents(path: str, **body):
        return {"method": "GET", "url": f"https://api.github.com/repos/{REPO}/contents/{path}?ref={'b' * 40}",
                "status": 200, "headers": {}, "body": body}

    source, replay, _ = _reading_source(
        contents("shop/a.py", type="file", encoding="base64", size=5, content=base64.b64encode(b"x = 1").decode()),
        contents("big.bin", type="file", encoding="none", size=5_000_000, content=""),
        contents("src") | {"body": [{"name": "inner"}]})
    assert source.file_at(REPO, "shop/a.py", "b" * 40) == (b"x = 1", "")
    assert source.file_at(REPO, "big.bin", "b" * 40) == (None, "larger than 1 MB")
    assert source.file_at(REPO, "src", "b" * 40) == (None, "not a regular file")
    replay.verify()

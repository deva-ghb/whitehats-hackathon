"""Linking the GitHub App: only an installation GitHub says the person can manage is ever linked."""

from urllib.parse import parse_qs, urlsplit

import pytest
from connector_contract import github
from connector_kit import HttpReplay, load
from sqlalchemy import select

from app.config import settings
from app.connectors import REGISTRY
from app.connectors import github as github_connector
from app.connectors.http import Answer
from app.db import SessionLocal
from app.models import Connector, Organization, User
from app.security import issue_step_token

INSTALLATION = "12345678"


def _with_app_settings(names):
    before = {name: getattr(settings, name) for name in names}
    for name, value in names.items():
        object.__setattr__(settings, name, value)  # Settings is frozen everywhere else
    yield
    for name, value in before.items():
        object.__setattr__(settings, name, value)


@pytest.fixture
def app_settings():
    """The App's public settings, as a server that has the App would hold them. Put back afterwards."""
    yield from _with_app_settings({"github_app_slug": "whitehats-test", "github_app_client_id": "Iv1.test",
                                   "github_app_client_secret": "test-client-secret"})


@pytest.fixture
def no_app_settings():
    """A server without the App, whatever the local .env holds. Put back afterwards."""
    yield from _with_app_settings({"github_app_slug": "", "github_app_client_id": "",
                                   "github_app_client_secret": ""})


@pytest.fixture
def github_row(client):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        row = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "github"))
        before = (row.status, dict(row.config or {}), dict(row.state or {}), row.last_check, row.connected_at)
        row_id = row.id
    yield row_id
    with SessionLocal() as db:
        row = db.get(Connector, row_id)
        row.status, row.config, row.state, row.last_check, row.connected_at = before
        db.commit()


def _github_answers(monkeypatch, installations: list[dict]) -> list[tuple[str, str]]:
    """GitHub's sign-in and installation list, then the recorded test-ok answers for the connection check."""
    calls: list[tuple[str, str]] = []

    def transport(method, url, headers, body=None):
        calls.append((method, url))
        if url == "https://github.com/login/oauth/access_token":
            ok = body.get("code") == "good-code"
            return Answer(200, {}, {"access_token": "user-token"} if ok else {"error": "bad_verification_code"})
        if url.startswith("https://api.github.com/user/installations"):
            assert headers["Authorization"] == "Bearer user-token"
            return Answer(200, {}, {"total_count": len(installations), "installations": installations})
        raise AssertionError(f"unexpected call {method} {url}")

    monkeypatch.setattr(github_connector, "httpx_transport", transport)
    replay = HttpReplay(load("github", "test-ok"))
    monkeypatch.setitem(REGISTRY, "github", lambda config, secret: github(replay, lambda _s: None, **config))
    return calls


def _state(email: str) -> str:
    with SessionLocal() as db:
        return issue_step_token(db.scalar(select(User).where(User.email == email)), "github-install")


def _back(client, state: str, code: str = "good-code", installation: str = INSTALLATION) -> dict:
    res = client.get("/api/github/callback", params={"state": state, "code": code, "installation_id": installation,
                                                      "setup_action": "install"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].startswith("/connectors?")
    return {k: v[0] for k, v in parse_qs(urlsplit(res.headers["location"]).query).items()}


def test_the_install_link_carries_a_signed_state(client, ceo, app_settings):
    res = client.post("/api/github/install", headers=ceo)
    assert res.status_code == 200
    url = urlsplit(res.json()["url"])
    assert (url.netloc, url.path) == ("github.com", "/apps/whitehats-test/installations/new")
    assert parse_qs(url.query)["state"][0].count(".") == 2


def test_only_an_account_owner_or_admin_can_start_an_install(client, engineer, app_settings):
    assert client.post("/api/github/install", headers=engineer).status_code == 403


def test_without_the_app_the_install_says_so(client, ceo, no_app_settings):
    assert client.post("/api/github/install", headers=ceo).status_code == 503


def test_an_installation_github_lists_as_theirs_is_linked_and_checked(client, app_settings, github_row, monkeypatch):
    calls = _github_answers(monkeypatch, [{"id": int(INSTALLATION), "account": {"login": "deva-ghb"}}])
    assert _back(client, _state("ceo@acme.example")) == {"github": "checked"}
    with SessionLocal() as db:
        row = db.get(Connector, github_row)
        assert row.config == {"installation_id": INSTALLATION, "account": "deva-ghb"}
        assert "WhiteHats GitHub App" not in row.last_check and "The check read " in row.last_check and "read 0 recent" not in row.last_check
    assert [method for method, _ in calls] == ["POST", "GET"]


def test_an_installation_someone_else_manages_is_not_linked(client, app_settings, github_row, monkeypatch):
    _github_answers(monkeypatch, [{"id": 55, "account": {"login": "someone-else"}}])
    back = _back(client, _state("ceo@acme.example"))
    assert back["github"] == "not-linked" and "not one you can manage" in back["reason"]
    with SessionLocal() as db:
        assert "installation_id" not in (db.get(Connector, github_row).config or {})


@pytest.mark.parametrize(("state", "code", "reason"), [
    ("forged.state.token", "good-code", "expired or was not started from WhiteHats"),
    (None, "bad-code", "did not accept the sign-in code"),
])
def test_a_bad_state_or_code_links_nothing(client, app_settings, github_row, monkeypatch, state, code, reason):
    _github_answers(monkeypatch, [{"id": int(INSTALLATION), "account": {"login": "deva-ghb"}}])
    back = _back(client, state or _state("ceo@acme.example"), code=code)
    assert back["github"] == "not-linked" and reason in back["reason"]
    with SessionLocal() as db:
        assert "installation_id" not in (db.get(Connector, github_row).config or {})


def test_a_state_started_by_someone_who_cannot_manage_connections_links_nothing(client, app_settings, github_row,
                                                                                monkeypatch):
    _github_answers(monkeypatch, [{"id": int(INSTALLATION), "account": {"login": "deva-ghb"}}])
    back = _back(client, _state("engineer@acme.example"))
    assert back["github"] == "not-linked" and "Account owner or Account admin" in back["reason"]


def test_an_install_started_from_a_project_s_map_returns_to_that_map(client, ceo, app_settings, github_row, monkeypatch):
    # Started from the Health platform's Studio, GitHub's return lands back on that map with the connection open,
    # not on the company's first project.
    _github_answers(monkeypatch, [{"id": int(INSTALLATION), "account": {"login": "deva-ghb"}}])
    url = urlsplit(client.post("/api/github/install?project=health-platform", headers=ceo).json()["url"])
    state = parse_qs(url.query)["state"][0]
    res = client.get("/api/github/callback", params={"state": state, "code": "good-code", "installation_id": INSTALLATION,
                                                      "setup_action": "install"}, follow_redirects=False)
    assert res.status_code == 303
    where = urlsplit(res.headers["location"])
    assert where.path == "/p/health-platform/studio"
    assert parse_qs(where.query) == {"connection": ["github"], "github": ["checked"]}


def test_a_project_that_is_not_the_starter_s_is_not_where_the_return_lands(client, ceo, app_settings, github_row, monkeypatch):
    _github_answers(monkeypatch, [{"id": int(INSTALLATION), "account": {"login": "deva-ghb"}}])
    url = urlsplit(client.post("/api/github/install?project=no-such-project", headers=ceo).json()["url"])
    assert _back(client, parse_qs(url.query)["state"][0]) == {"github": "checked"}  # the account's connections page

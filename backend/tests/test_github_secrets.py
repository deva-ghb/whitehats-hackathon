"""The GitHub App's private key, webhook secret and client secret never reach a log, a response or an error."""

import logging
from dataclasses import replace

import pytest
from conftest import login
from connector_contract import CASES
from connector_kit import HttpReplay, load
from test_github_webhook import GITHUB_PUSH, SECRET, _headers, linked  # noqa: F401 - the fixture is used by name

from app.config import settings
from app.connectors.base import SourceError
from app.connectors.github_app import app_jwt

CLIENT_SECRET = "client-secret-canary-1f4e"
KEY_CANARY = "KEYCANARYmustneverleak"
# Built from parts, so secret scanners do not flag this test for a key it does not hold.
FENCE, KIND = "-" * 5, "RSA " + "PRIVATE KEY"
# Modules that read the App settings; each gets the planted copy.
SETTINGS_READERS = ("app.services.github_install", "app.routers.github")


def _everything(caplog, texts) -> str:
    return "\n".join([*texts, *(record.getMessage() for record in caplog.records),
                      *(str(record.exc_info) for record in caplog.records if record.exc_info)])


def test_webhook_answers_and_logs_never_hold_the_webhook_secret(client, linked, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    answers = [
        client.post("/api/github/webhook", content=GITHUB_PUSH, headers=_headers(GITHUB_PUSH, "leak-1")),
        client.post("/api/github/webhook", content=GITHUB_PUSH, headers=_headers(GITHUB_PUSH, "leak-2", secret="x")),
        client.post("/api/github/webhook", content=b"not json", headers=_headers(b"not json", "leak-3")),
    ]
    assert SECRET not in _everything(caplog, [a.text + str(a.headers) for a in answers])


def test_the_install_callback_never_shows_the_client_secret(client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    planted = replace(settings, github_app_client_secret=CLIENT_SECRET, github_app_client_id="Iv1.client")
    for module in SETTINGS_READERS:
        monkeypatch.setattr(f"{module}.settings", planted)
    back = client.get("/api/github/callback?state=forged&code=abc&installation_id=1", follow_redirects=False)
    setup = client.get("/api/github/app", headers=login(client))
    assert CLIENT_SECRET not in _everything(caplog, [back.text, str(back.headers), setup.text])


def test_a_bad_private_key_is_refused_without_quoting_it():
    with pytest.raises(Exception) as refused:
        app_jwt("123", f"{FENCE}BEGIN {KIND}{FENCE}\n{KEY_CANARY}\n{FENCE}END {KIND}{FENCE}\n", 1_800_000_000)
    assert KEY_CANARY not in str(refused.value) and KEY_CANARY not in repr(refused.value)


def test_a_refused_sign_in_names_no_token():
    case = next(c for c in CASES if c.name == "github")
    replay = HttpReplay(load("github", "signin-refused"))
    source = case.build(replay, lambda _s: None)
    with pytest.raises(SourceError) as refused:
        source.test()
    assert "recorded-app-token" not in str(refused.value)

"""The Fix Factory bridge refuses crafted run ids and never lets a slow engine stall the main screens."""

import dataclasses

import httpx
import pytest
from fastapi import HTTPException

from app.services import factory

RUN_ID = "WH-INT-2208-1-20261009192613763247"


@pytest.fixture
def engine_calls(monkeypatch):
    calls = []

    def record(org, method, path, body=None, timeout=factory.CALL_TIMEOUT_S):
        calls.append((method, path))
        return {"ok": True}

    monkeypatch.setattr(factory, "serves", lambda org: True)
    monkeypatch.setattr(factory, "call", record)
    return calls


@pytest.mark.parametrize("crafted", ["..", "x?y", "a.b", "run id", "%2e%2e", "x" * 81, ""])
def test_run_path_refuses_anything_but_a_plain_run_id(crafted):
    with pytest.raises(HTTPException) as refused:
        factory.run_path(crafted)
    assert refused.value.status_code == 404


def test_run_path_builds_the_engine_path():
    assert factory.run_path(RUN_ID) == f"/api/runs/{RUN_ID}"
    assert factory.run_path(RUN_ID, "approve") == f"/api/runs/{RUN_ID}/approve"


@pytest.mark.parametrize("crafted", ["%2e%2e", "x%3Fy", "a.b"])
def test_crafted_run_ids_never_reach_the_engine(client, ceo, engine_calls, crafted):
    assert client.get(f"/api/factory/runs/{crafted}", headers=ceo).status_code == 404
    assert client.post(f"/api/factory/runs/{crafted}/approve", headers=ceo).status_code == 404
    assert engine_calls == []
    assert client.get(f"/api/factory/runs/{RUN_ID}", headers=ceo).status_code == 200
    assert engine_calls == [("GET", f"/api/runs/{RUN_ID}")]


class SlowEngine:
    """Answers once, then stops answering in time."""

    def __init__(self):
        self.timeouts = []
        self.hung = False

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        self.timeouts.append(timeout)
        if self.hung:
            raise httpx.ReadTimeout("no answer")
        return httpx.Response(200, json={"summary": {"verified": 3, "approved": 1}, "runs": []})


@pytest.fixture
def slow_engine(monkeypatch):
    engine = SlowEngine()
    monkeypatch.setattr(factory, "serves", lambda org: True)
    monkeypatch.setattr(factory, "settings", dataclasses.replace(factory.settings, factory_token="test-token"))
    monkeypatch.setattr(factory.httpx, "request", engine)
    monkeypatch.setattr(factory, "_last_status", {})
    monkeypatch.setattr(factory, "_slow_until", {})
    return engine


def _org():
    from app.models import Organization

    return Organization(slug="acme", name="Acme")


def test_a_slow_engine_shows_its_last_status_without_asking_again(slow_engine):
    org = _org()
    fresh = factory.status(org)
    assert fresh == {"connected": True, "summary": {"verified": 3, "approved": 1}}
    slow_engine.hung = True
    late = factory.status(org)
    assert late == {"connected": True, "summary": {"verified": 3, "approved": 1}, "stale": True}
    assert factory.status(org) == late
    assert slow_engine.timeouts == [factory.STATUS_TIMEOUT_S, factory.STATUS_TIMEOUT_S]
    assert factory.STATUS_TIMEOUT_S <= 2


def test_a_slow_engine_with_no_status_yet_says_so(slow_engine):
    slow_engine.hung = True
    assert factory.status(_org()) == {"connected": False, "message": factory.SLOW}


def test_the_overview_asks_a_hung_engine_once(client, ceo, slow_engine):
    slow_engine.hung = True
    for _ in range(3):
        assert client.get("/api/overview", headers=ceo).status_code == 200
    assert slow_engine.timeouts == [factory.STATUS_TIMEOUT_S]


def test_screens_that_show_fixes_ask_a_hung_engine_once(client, ceo, slow_engine):
    slow_engine.hung = True
    for _ in range(3):
        assert client.get("/api/remediations", headers=ceo).status_code == 200
    assert slow_engine.timeouts == [factory.STATUS_TIMEOUT_S]

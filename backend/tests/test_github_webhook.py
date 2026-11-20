"""GitHub through the app: a forged delivery is refused, a real one is read once, and the poll agrees with it."""

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from connector_contract import GITHUB_CONFIG, GITHUB_PUSH, github
from connector_kit import HttpReplay, load
from sqlalchemy import select

from app.connectors import REGISTRY, bookmarks
from app.db import SessionLocal, utcnow
from app.models import Connector, LogTemplate, Organization
from app.services import cloudlogs, github_webhook

SECRET = "test-webhook-secret"
PUSHED = "58d7dc787d3aea48e584737fa88f43d752913de8"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _headers(body: bytes, delivery: str, event: str = "push", secret: str = SECRET) -> dict:
    return {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": _sign(body, secret),
            "Content-Type": "application/json"}


@pytest.fixture
def linked(client, monkeypatch):
    """Acme's GitHub connection linked to the recorded installation, put back as it was afterwards."""
    monkeypatch.setattr(github_webhook, "webhook_secret", lambda: SECRET)
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        row = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "github"))
        before = (row.status, dict(row.config or {}), dict(row.state or {}), row.last_sync_at)
        row.status, row.config, row.state, row.last_sync_at = "checked", dict(GITHUB_CONFIG), {}, None
        db.commit()
        row_id = row.id
    yield row_id
    with SessionLocal() as db:
        row = db.get(Connector, row_id)
        row.status, row.config, row.state, row.last_sync_at = before
        db.commit()


def _row(row_id: int) -> Connector:
    with SessionLocal() as db:
        row = db.get(Connector, row_id)
        db.expunge(row)
        return row


def test_a_forged_or_unsigned_delivery_is_refused(client, linked):
    forged = client.post("/api/github/webhook", content=GITHUB_PUSH,
                         headers=_headers(GITHUB_PUSH, "forged-1", secret="not-the-secret"))
    assert forged.status_code == 401
    unsigned = client.post("/api/github/webhook", content=GITHUB_PUSH,
                           headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "unsigned-1"})
    assert unsigned.status_code == 401
    tampered = client.post("/api/github/webhook", content=GITHUB_PUSH.replace(b"main", b"prod"),
                           headers=_headers(GITHUB_PUSH, "tampered-1"))
    assert tampered.status_code == 401
    odd = client.post("/api/github/webhook", content=GITHUB_PUSH, headers={
        "X-GitHub-Event": "push", "X-GitHub-Delivery": "odd-1",
        "X-Hub-Signature-256": b"sha256=" + b"\xe9" * 64})  # right length, not ASCII
    assert odd.status_code == 401
    assert "deliveries" not in _row(linked).state


def test_without_a_secret_the_server_takes_no_deliveries(client, monkeypatch):
    monkeypatch.setattr(github_webhook, "webhook_secret", lambda: "")
    res = client.post("/api/github/webhook", content=GITHUB_PUSH, headers=_headers(GITHUB_PUSH, "none-1"))
    assert res.status_code == 503


def test_a_signed_push_is_read_before_the_answer_and_a_redelivery_is_not(client, linked):
    first = client.post("/api/github/webhook", content=GITHUB_PUSH, headers=_headers(GITHUB_PUSH, "delivery-1"))
    assert first.status_code == 200, first.text
    assert first.json() == {"accounts": 1, "read": 1, "duplicate": False}
    with SessionLocal() as db:
        row = db.get(Connector, linked)
        assert db.scalar(select(LogTemplate).where(LogTemplate.org_id == row.org_id,
                                                   LogTemplate.source == "github:deva-ghb/whitehats-proving-ground"))
        assert bookmarks.load(row)["repos"]["deva-ghb/whitehats-proving-ground"]["head"] == PUSHED
        assert row.state["last_event"]["delivery"] == "delivery-1"
    again = client.post("/api/github/webhook", content=GITHUB_PUSH, headers=_headers(GITHUB_PUSH, "delivery-1"))
    assert again.json() == {"accounts": 1, "read": 0, "duplicate": True}
    # A new delivery about the same commit (GitHub's redeliver button) reads nothing new either.
    other = client.post("/api/github/webhook", content=GITHUB_PUSH, headers=_headers(GITHUB_PUSH, "delivery-2"))
    assert other.json() == {"accounts": 1, "read": 0, "duplicate": False}
    assert _row(linked).state["total"] == 1


def test_a_delivery_for_an_installation_no_account_holds_lands_nowhere(client, linked):
    body = GITHUB_PUSH.replace(b'"id": 12345678', b'"id": 99999999')
    res = client.post("/api/github/webhook", content=body, headers=_headers(body, "elsewhere-1"))
    assert res.json() == {"accounts": 0, "read": 0, "duplicate": False}
    assert "deliveries" not in _row(linked).state


def test_events_without_news_are_accepted_and_add_nothing(client, linked):
    body = json.dumps({"zen": "Keep it simple.", "hook_id": 1, "installation": {"id": 12345678}}).encode()
    res = client.post("/api/github/webhook", content=body, headers=_headers(body, "ping-1", event="ping"))
    assert res.json() == {"accounts": 1, "read": 0, "duplicate": False}


def test_the_minute_poll_reads_the_push_and_the_later_webhook_does_not_repeat_it(client, linked, monkeypatch):
    replay = HttpReplay(load("github", "read-twice"))
    monkeypatch.setitem(REGISTRY, "github", lambda config, secret: github(replay, lambda _s: None, **config))
    with SessionLocal() as db:
        row = db.get(Connector, linked)
        assert cloudlogs.can_read(row) and cloudlogs.is_due(row, utcnow())
        read = cloudlogs.sync(db, row)
        assert read > 0
    replay.verify()
    row = _row(linked)
    assert row.status == "reading" and row.state["last_count"] == read
    res = client.post("/api/github/webhook", content=GITHUB_PUSH, headers=_headers(GITHUB_PUSH, "after-poll-1"))
    assert res.json()["read"] == 0


def test_github_and_aws_are_read_every_minute_and_other_cloud_sources_every_five():
    is_due = cloudlogs.is_due
    now = utcnow()
    for key in ("github", "aws", "cloudwatch"):
        assert is_due(Connector(key=key, last_sync_at=now - timedelta(seconds=56)), now)
        assert not is_due(Connector(key=key, last_sync_at=now - timedelta(seconds=30)), now)
    assert not is_due(Connector(key="azure_monitor", last_sync_at=now - timedelta(seconds=120)), now)
    assert is_due(Connector(key="azure_monitor", last_sync_at=now - timedelta(seconds=296)), now)
    assert is_due(Connector(key="github", last_sync_at=None), now)


def test_the_app_details_name_every_permission_and_event(client, ceo):
    res = client.get("/api/github/app", headers=ceo)
    assert res.status_code == 200
    body = res.json()
    assert body["permissions"] == {"metadata": "read", "contents": "read", "actions": "read",
                                   "pull_requests": "write", "checks": "write"}
    assert body["events"] == ["push", "pull_request", "workflow_run"]
    assert [p["name"] for p in body["reads_with"]] == ["metadata:read", "contents:read", "actions:read",
                                                       "pull_requests:read"]


def test_a_connected_source_that_failed_on_its_first_read_is_tried_again():
    """An AWS account too busy for the first read once failed it, and was never read again (it had never read)."""
    failed_first = Connector(key="aws", category="Cloud logs", status="error", last_sync_at=None,
                             connected_at=utcnow() - timedelta(hours=1))
    never_set_up = Connector(key="aws", category="Cloud logs", status="error", last_sync_at=None, connected_at=None)
    assert cloudlogs.can_read(failed_first)
    assert not cloudlogs.can_read(never_set_up)

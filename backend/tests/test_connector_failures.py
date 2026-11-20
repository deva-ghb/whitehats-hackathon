"""A cloud log source that stops reading says so, and the background worker picks it up again."""

import json
import time

import boto3
from moto import mock_aws
from sqlalchemy import select

REGION = "ap-south-1"
EXTERNAL_ID = "failures-external-id"


def _cloudwatch(client, ceo) -> dict:
    return next(c for c in client.get("/api/connectors", headers=ceo).json() if c["key"] == "cloudwatch")


def _log_group(name: str) -> None:
    logs = boto3.client("logs", region_name=REGION)
    logs.create_log_group(logGroupName=name)
    logs.create_log_stream(logGroupName=name, logStreamName="s1")
    logs.put_log_events(logGroupName=name, logStreamName="s1", logEvents=[
        {"timestamp": int(time.time() * 1000) - 1000, "message": json.dumps({"eventName": "ListBuckets"})}])


def _role() -> str:
    iam = boto3.client("iam", region_name=REGION)
    iam.create_role(RoleName="whitehats-reader", AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17"}))
    return iam.get_role(RoleName="whitehats-reader")["Role"]["Arn"]


def _sync_all() -> dict:
    from app.db import SessionLocal
    from app.services import cloudlogs

    with SessionLocal() as db:
        return cloudlogs.sync_all(db)


def test_a_failed_sync_shows_the_error_and_the_worker_recovers_it(client, ceo):
    cw = _cloudwatch(client, ceo)
    try:
        with mock_aws():
            _log_group("audit")
            saved = client.put(f"/api/connectors/{cw['id']}", headers=ceo, json={"values": {
                "region": REGION, "log_groups": "audit", "role_arn": _role(), "external_id": EXTERNAL_ID}})
            assert saved.json()["status"] == "checked", saved.text
            assert client.post(f"/api/connectors/{cw['id']}/sync", headers=ceo).json()["read"] == 1

            boto3.client("logs", region_name=REGION).delete_log_group(logGroupName="audit")
            failed = client.post(f"/api/connectors/{cw['id']}/sync", headers=ceo)
            assert failed.status_code == 502
            shown = _cloudwatch(client, ceo)
            assert shown["status"] == "error"
            assert shown["last_check"].startswith("Reading audit failed")
            assert shown["sync"]["last_error"].startswith("Reading audit failed")

            _log_group("audit")
            read_now = client.post(f"/api/connectors/{cw['id']}/sync", headers=ceo)
            assert read_now.status_code == 200, read_now.text  # a person need not wait for the worker
            assert read_now.json()["connector"]["status"] == "reading"
            boto3.client("logs", region_name=REGION).delete_log_group(logGroupName="audit")
            assert client.post(f"/api/connectors/{cw['id']}/sync", headers=ceo).status_code == 502
            _log_group("audit")
            results = _sync_all()
            assert any(key.endswith(":cloudwatch") and value == 1 for key, value in results.items()), results
            recovered = _cloudwatch(client, ceo)
            assert recovered["status"] == "reading"
            assert recovered["sync"]["last_error"] == ""
            assert recovered["last_check"].startswith("Reading again")
    finally:
        client.delete(f"/api/connectors/{cw['id']}", headers=ceo)


def test_a_source_whose_first_check_failed_is_not_read_on_a_schedule(client, ceo):
    from app.db import SessionLocal
    from app.models import Connector, Organization

    cw = _cloudwatch(client, ceo)
    try:
        with SessionLocal() as db:
            org_id = db.scalar(select(Organization.id).where(Organization.slug == "acme"))
            row = db.scalar(select(Connector).where(Connector.org_id == org_id, Connector.key == "cloudwatch"))
            row.status, row.last_check, row.last_sync_at = "error", "AWS sign-in failed: denied", None
            db.commit()
        assert not any(key.endswith(":cloudwatch") for key in _sync_all())
    finally:
        client.delete(f"/api/connectors/{cw['id']}", headers=ceo)


def test_a_failed_check_is_not_recorded_as_connected(client, ceo):
    cw = _cloudwatch(client, ceo)
    try:
        with mock_aws():
            saved = client.put(f"/api/connectors/{cw['id']}", headers=ceo, json={"values": {
                "region": REGION, "log_groups": "missing", "role_arn": _role(), "external_id": EXTERNAL_ID}})
        body = saved.json()
        assert body["status"] == "error", body
        assert body["connected_at"] is None
        assert client.post(f"/api/connectors/{cw['id']}/sync", headers=ceo).status_code == 409
    finally:
        client.delete(f"/api/connectors/{cw['id']}", headers=ceo)

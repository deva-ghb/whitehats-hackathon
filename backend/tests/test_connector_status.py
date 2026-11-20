"""The contract status on the Connectors screen, and discoveries on the canvas with their problems (CN.5)."""

import json
import subprocess
import sys
from pathlib import Path

import boto3
import pytest

from app.services.connector_status import as_canvas

FAKE = Path(__file__).parent / "fake_aws_server.py"
EXTERNAL_ID = "status-external-id"


@pytest.fixture
def fake_aws(monkeypatch):
    """A moto account over HTTP with IAM enforced, so a permission can be taken away mid-test."""
    process = subprocess.Popen([sys.executable, str(FAKE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    info = json.loads(process.stdout.readline())
    monkeypatch.setenv("AWS_ENDPOINT_URL", info["endpoint"])
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", info["access_key_id"])
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", info["secret_access_key"])
    monkeypatch.setenv("AWS_DEFAULT_REGION", info["region"])
    # boto3's default session keeps the keys it first read. One made after the last account closed (a map read
    # on a thread, say) would still sign in with that account's keys, so each account starts with a fresh one.
    boto3.DEFAULT_SESSION = None

    def revoke(action: str) -> None:
        process.stdin.write(f"revoke {action}\n")
        process.stdin.flush()
        assert json.loads(process.stdout.readline()) == {"revoked": action}

    def delete_queue(name: str) -> None:
        process.stdin.write(f"delete-queue {name}\n")
        process.stdin.flush()
        assert json.loads(process.stdout.readline()) == {"deleted": name}

    yield {**info, "revoke": revoke, "delete_queue": delete_queue}
    # boto3 keeps the keys of its default session; the next fake account has other keys.
    boto3.DEFAULT_SESSION = None
    process.stdin.close()
    process.wait(timeout=10)
    process.stdout.close()


def _aws(client, ceo) -> dict:
    return next(row for row in client.get("/api/connectors", headers=ceo).json() if row["key"] == "aws")


def test_a_revoked_permission_is_named_on_screen_and_marks_the_nodes_it_reads(client, ceo, fake_aws):
    aws = _aws(client, ceo)
    try:
        saved = client.put(f"/api/connectors/{aws['id']}", headers=ceo, json={"values": {
            "role_arn": fake_aws["role_arn"], "external_id": EXTERNAL_ID, "region": fake_aws["region"]}}).json()
        assert saved["status"] == "checked", saved["last_check"]
        permissions = saved["contract"]["permissions"]
        assert [p["ok"] for p in permissions] == [True] * len(permissions)
        assert "whitehats-readonly" in saved["contract"]["identity"]

        found = client.post(f"/api/connectors/{aws['id']}/discover", headers=ceo)
        assert found.status_code == 200, found.text
        view = client.get("/api/discoveries", headers=ceo).json()
        names = {node["name"]: node for node in view["document"]["nodes"]}
        assert {"fintech-receipts", "orders", "payment-jobs", "admin-box"} <= set(names)
        assert names["admin-box"]["provenance"] == {"kind": "discovered", "source": "aws"}
        assert names["admin-box"]["last_confirmed_at"] == view["sources"][0]["discovered_at"]
        assert {group["kind"] for group in view["document"]["groups"]} == {"account", "region", "vpc"}
        vpc = next(group for group in view["document"]["groups"] if group["kind"] == "vpc")
        assert names["admin-box"]["location"] == vpc["id"]
        assert view["marks"] == {}

        fake_aws["revoke"]("ec2:DescribeInstances")
        checked = client.post(f"/api/connectors/{aws['id']}/check", headers=ceo).json()
        assert checked["status"] == "error"
        missing = [p for p in checked["contract"]["permissions"] if p["ok"] is False]
        assert [p["name"] for p in missing] == ["ec2:DescribeInstances"]
        assert "DescribeInstances" in checked["last_check"]

        view = client.get("/api/discoveries", headers=ceo).json()
        marked = {node["name"] for node in view["document"]["nodes"] if node["id"] in view["marks"]}
        assert marked == {"admin-box"}
        mark = view["marks"][names["admin-box"]["id"]]
        assert mark == ["AWS cannot read this any more: ec2:DescribeInstances is missing."]
        assert view["sources"][0]["missing"] == ["ec2:DescribeInstances"]

        again = client.post(f"/api/connectors/{aws['id']}/discover", headers=ceo)
        assert again.status_code == 200, again.text  # one missing describe does not stop discovery
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)
    assert client.get("/api/discoveries", headers=ceo).json()["document"]["nodes"] == []


def test_discover_and_check_need_a_connection_and_the_right_role(client, ceo, engineer):
    aws = _aws(client, ceo)
    assert client.post(f"/api/connectors/{aws['id']}/discover", headers=ceo).status_code == 409
    assert client.post(f"/api/connectors/{aws['id']}/check", headers=ceo).status_code == 409
    assert client.post(f"/api/connectors/{aws['id']}/discover", headers=engineer).status_code == 403
    okta = next(row for row in client.get("/api/connectors", headers=ceo).json() if row["key"] == "okta")
    assert client.post(f"/api/connectors/{okta['id']}/discover", headers=ceo).status_code == 409


def test_boundaries_become_containers_and_their_nesting_is_not_drawn_twice():
    def node(node_id, type_ref):
        return {"id": node_id, "type": type_ref, "name": node_id, "environment": "shared",
                "provenance": {"kind": "imported", "source": "aws"}, "properties": {}}

    nodes = [node("acct", "boundary.aws-account@1"), node("reg", "boundary.aws-region@1"),
             node("vpc", "boundary.vpc@1"), node("vm", "compute.vm@1"), node("role", "identity.iam-role@1")]
    edges = [{"from": "reg", "to": "acct", "type": "contained_in"}, {"from": "vpc", "to": "reg", "type": "contained_in"},
             {"from": "vm", "to": "vpc", "type": "contained_in"}, {"from": "vm", "to": "role", "type": "runs_as"}]
    document = as_canvas(nodes, edges)
    parents = {group["id"]: group.get("parent") for group in document["groups"]}
    assert parents == {"acct": None, "reg": "acct", "vpc": "reg"}
    assert {node["id"]: node.get("location") for node in document["nodes"]} == {"vm": "vpc", "role": None}
    assert document["edges"] == [{"from": "vm", "to": "role", "type": "runs_as"}]

"""The Google Cloud connection: the guard, the settings checks and the activity checks, without calling Google."""

import httpx
import pytest

from app.connectors.base import SourceError
from app.connectors.gcp import Gcp, line_of
from app.connectors.gcp_guard import blocked
from app.services.gcp_checks import changes, evaluate

CONFIG = {"project_id": "demo", "service_account": "whitehats-reader@demo.iam.gserviceaccount.com"}


def test_guard_allows_only_listed_reads():
    assert blocked("GET", "https://cloudasset.googleapis.com/v1/projects/demo/assets") == ""
    assert blocked("POST", "https://logging.googleapis.com/v2/entries:list") == ""
    assert "only makes its listed read calls" in blocked("POST", "https://cloudasset.googleapis.com/v1/projects/demo/assets")
    assert "only makes its listed read calls" in blocked("DELETE", "https://compute.googleapis.com/compute/v1/x")
    assert "objects in Cloud Storage" in blocked("GET", "https://storage.googleapis.com/storage/v1/b/x/o")


def test_a_refused_call_never_leaves():
    sent = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={})))
    g = Gcp(CONFIG, token=lambda _: "t", client=client)
    with pytest.raises(SourceError):
        g._call("GET", "https://storage.googleapis.com/storage/v1/b/x/o/file")
    assert sent == []


def test_missing_config_is_a_plain_error():
    with pytest.raises(SourceError, match="project ID"):
        Gcp({"project_id": "demo"})


def _asset(kind, name, data=None, policy=None):
    return {"assetType": kind, "name": name, "resource": {"data": data or {}}, "iamPolicy": policy or {}}


def test_settings_checks_find_each_planted_mistake():
    resources = [
        _asset("compute.googleapis.com/Firewall", "//compute.googleapis.com/projects/demo/global/firewalls/ssh-all",
               {"direction": "INGRESS", "sourceRanges": ["0.0.0.0/0"], "network": "x/networks/default",
                "allowed": [{"IPProtocol": "tcp", "ports": ["22", "8000-8100"]}]}),
        _asset("compute.googleapis.com/Firewall", "//compute.googleapis.com/projects/demo/global/firewalls/internal",
               {"direction": "INGRESS", "sourceRanges": ["10.0.0.0/8"], "allowed": [{"IPProtocol": "all"}]}),
        _asset("sqladmin.googleapis.com/Instance", "//cloudsql.googleapis.com/projects/demo/instances/appointments",
               {"settings": {"ipConfiguration": {"authorizedNetworks": [{"value": "0.0.0.0/0"}]},
                             "backupConfiguration": {"enabled": False}}}),
        _asset("storage.googleapis.com/Bucket", "//storage.googleapis.com/fine",
               {"iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}}}),
    ]
    policies = [_asset("storage.googleapis.com/Bucket", "//storage.googleapis.com/reports", policy={
        "bindings": [{"role": "roles/storage.objectViewer", "members": ["allUsers"]}]})]
    found = {(f["check"], f["severity"]) for f in evaluate(resources, policies)}
    assert found == {("firewall-open-22", "high"), ("sql-open", "critical"), ("sql-no-backups", "high"),
                     ("public", "critical")}


def test_a_clean_project_has_no_findings():
    assert evaluate([_asset("storage.googleapis.com/Bucket", "//storage.googleapis.com/b",
                            {"iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}}})], []) == []


def _entry(method, request=None, status=None):
    payload = {"methodName": method, "resourceName": "projects/_/buckets/reports",
               "authenticationInfo": {"principalEmail": "dev@example.com"}, "request": request or {}}
    if status:
        payload["status"] = {"code": status}
    return {"insertId": method, "timestamp": "2026-11-20T10:00:00Z", "protoPayload": payload}


def test_opening_a_bucket_to_everyone_is_a_critical_change_with_who_did_it():
    lines = [line_of(_entry("storage.setIamPermissions",
                            {"policy": {"bindings": [{"members": ["allUsers"]}]}}))]
    [change] = changes(lines)
    assert change["severity"] == "critical"
    assert change["title"].startswith("dev@example.com made it open to anyone")


def test_refused_and_harmless_calls_are_not_changes():
    assert changes([line_of(_entry("storage.setIamPermissions", status=7)), line_of(_entry("storage.objects.list"))]) == []

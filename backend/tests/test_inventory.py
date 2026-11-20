"""SCAN.2: every part of a service, direct and hidden, per version; a stored inventory is reused for an unchanged
version; one base image is one finding naming every service that runs it."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.decisions_api.scanners.trivy import ImageWeakness
from app.models import ImageScan, Inventory, Signal
from app.scanning import inventory
from app.services import image_scans

RUNTIME = "public.ecr.aws/lambda/python:3.12-arm64"
WORKER = f"509594260364.dkr.ecr.ap-southeast-2.amazonaws.com/payments-worker@sha256:{'a' * 64}"
SHOP = {"id": "shop", "name": "shop-api", "type": "service.function@2",
        "properties": {"platform": "aws_lambda", "runtime": "python3.12", "package_type": "zip",
                       "packages": ["PyPI:pyyaml==6.0.2"], "direct_dependencies": ["pyyaml"]}}


def _cyclonedx(*packages: tuple[str, str, str]) -> dict:
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [
        {"type": "operating-system", "name": "amazon", "version": "2023"},
        *({"type": "library", "name": name, "version": version, "purl": f"pkg:{kind}/{name}@{version}"}
          for kind, name, version in packages)]}


class Maker:
    """Stands in for Trivy making an inventory: counts how often an image is pulled."""

    def __init__(self, document: dict):
        self.document, self.made = document, []

    def __call__(self, image, *, timeout, login=None):
        self.made.append(image)
        return self.document, "sha256:" + "f" * 64, type("Run", (), {"error": ""})()


@pytest.fixture
def db(client):
    with SessionLocal() as session:
        yield session
        session.execute(delete(Inventory))
        session.execute(delete(ImageScan))
        session.execute(delete(Signal).where(Signal.source == image_scans.SOURCE))
        session.commit()


def test_a_zip_function_runs_on_its_aws_runtime_and_the_parts_its_manifest_does_not_name_are_hidden(db):
    assert inventory.runtime_image("python3.12") == RUNTIME
    assert inventory.runtime_image("nodejs20.x") == "public.ecr.aws/lambda/nodejs:20-arm64"
    assert inventory.runtime_image("provided.al2023") == ""
    make = Maker(_cyclonedx(("pypi", "boto3", "1.42.97"), ("pypi", "urllib3", "2.8.0"), ("rpm", "openssl-libs", "3.2.2")))
    found = inventory.for_node(db, SHOP, make=make)
    assert (found["image"], found["image_use"]) == (RUNTIME, "runs on the AWS runtime")
    parts = {(part["name"], part["from"], part["hidden"]) for part in found["parts"]}
    assert ("pyyaml", "lock file", False) in parts  # the manifest names it
    assert {("boto3", f"image {RUNTIME}", True), ("urllib3", f"image {RUNTIME}", True),
            ("openssl-libs", f"image {RUNTIME}", True)} <= parts  # parts shop-api's manifest never names
    assert found["counts"] == {"parts": 4, "hidden": 3}


def test_a_second_scan_of_an_unchanged_version_reuses_the_stored_inventory(db):
    make = Maker(_cyclonedx(("pypi", "pip", "25.0.1")))
    clock = [datetime(2026, 10, 31, 9, 0)]
    for _ in range(3):
        assert inventory.stored(db, WORKER, make=make, now=lambda: clock[0]) is not None
        clock[0] += timedelta(days=2)
    assert make.made == [WORKER]  # by digest: made once, read ever after
    for _ in range(2):  # by tag, the image may have moved: made again once a day
        inventory.stored(db, RUNTIME, make=make, now=lambda: clock[0])
        clock[0] += timedelta(days=2)
    assert make.made == [WORKER, RUNTIME, RUNTIME]
    assert inventory.stored(db, "never.example/app@sha256:" + "0" * 64, make=None) is None  # read-only: nothing made


def test_one_base_image_is_one_finding_naming_every_service_that_runs_it(db, client, engineer):
    document = {"schema_version": 1, "clone": "scan2-shared-image", "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "t", "reason": "r"}, "access_paths": [], "credential_refs": [],
                "groups": [], "edges": [],
                "nodes": [{"id": node_id, "type": "service.function@2", "name": name, "environment": "prod",
                           "properties": {"platform": "aws_lambda", "runtime": "unknown", "package_type": "image",
                                          "image": WORKER}}
                          for node_id, name in (("pay", "payments-worker"), ("refund", "refunds-worker"))]}
    made = client.post("/api/clones", headers=engineer, json={"reason": "r", "document": document})
    assert made.status_code == 200, made.text
    weak = [ImageWeakness(WORKER, "sha256:" + "a" * 64, f"pkg{n}", "1.0", "1.1", f"CVE-2026-{n}", "high", "flaw", ())
            for n in range(40)]
    try:
        image_scans.check(db, scan=lambda image, login: (weak if image == WORKER else [], 0, type("R", (), {"error": ""})()),
                          login_for=lambda *_: None)
        [finding] = db.scalars(select(Signal).where(Signal.source == image_scans.SOURCE, Signal.fingerprint.like("image|%")))
        assert finding.title == "40 known weaknesses with a fix in the image payments-worker and refunds-worker runs (40 high)"
        assert finding.entity == "payments-worker and refunds-worker"
    finally:
        for node in document["nodes"]:
            node["properties"]["image"] = ""
        client.post(f"/api/clones/{made.json()['id']}/versions", headers=engineer, json={"reason": "tidy", "document": document})


def test_the_route_lists_a_services_parts_and_gives_its_cyclonedx_inventory(db, client, engineer):
    document = {"schema_version": 1, "clone": "scan2-route", "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "t", "reason": "r"}, "access_paths": [], "credential_refs": [],
                "groups": [], "edges": [], "nodes": [{**SHOP, "environment": "prod"}]}
    made = client.post("/api/clones", headers=engineer, json={"reason": "r", "document": document})
    assert made.status_code == 200, made.text
    url = f"/api/clones/{made.json()['id']}/nodes/shop/inventory"
    before = client.get(url, headers=engineer).json()
    assert (before["image"], before["digest"], before["counts"]) == (RUNTIME, "", {"parts": 1, "hidden": 0})  # not made yet
    assert client.get(url + "?format=cyclonedx", headers=engineer).status_code == 404
    inventory.stored(db, RUNTIME, make=Maker(_cyclonedx(("pypi", "boto3", "1.42.97"))))
    after = client.get(url, headers=engineer).json()
    assert after["counts"] == {"parts": 2, "hidden": 1} and after["made_by"].startswith("trivy")
    assert client.get(url + "?format=cyclonedx", headers=engineer).json()["bomFormat"] == "CycloneDX"

"""SCAN.5: "not affected" is an OpenVEX statement, never a suppression. A marked finding stays listed and the next
scan shows it as not affected with the reason; a supplier's VEX is read with the supplier named; a withdrawn or expired
statement opens the finding again."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest
from matrix_account import matrix_fixture  # noqa: F401 - the fixture, imported so this module can ask for `matrix`
from sqlalchemy import delete, select
from test_scan_plans import FILES, Repository, osv_only

from app.db import SessionLocal
from app.models import Connector, ImageScan, Inventory, ScanPlanRecord, Signal, VexStatement
from app.routers.vex import problems
from app.scanning import floor, inventory, vex
from app.services import image_scans

RUNTIME = "public.ecr.aws/lambda/python:3.12-arm64"


@pytest.fixture
def finding(row_id):
    """S04's PyYAML finding on the default branch, as the weekly floor opens it, in the main project."""
    clock = [datetime(2026, 10, 31, 9, 0)]
    with SessionLocal() as db:
        connector = db.get(Connector, row_id)
        floor.run(db, now=lambda: clock[0], decider=osv_only, sources=[(connector, Repository(FILES))])
        rows = db.scalars(select(Signal).where(Signal.source == floor.SOURCE).order_by(Signal.project_id)).all()
        yield db, connector, clock, rows[0]
        db.rollback()
        db.execute(delete(VexStatement))
        db.execute(delete(Signal).where(Signal.source.in_([floor.SOURCE, image_scans.SOURCE])))
        db.execute(delete(ScanPlanRecord))
        db.execute(delete(ImageScan))
        db.execute(delete(Inventory))
        db.commit()


def _project_slug(db, signal) -> str:
    from app.models import Project

    return db.get(Project, signal.project_id).slug


def test_marking_a_finding_not_affected_is_a_valid_openvex_statement_and_the_next_scan_shows_it_with_the_reason(
        finding, client, engineer):
    db, connector, clock, signal = finding
    slug = _project_slug(db, signal)
    marked = client.post(f"/api/signals/{signal.id}/vex?project={slug}", headers=engineer, json={
        "justification": "vulnerable_code_not_in_execute_path", "reason": "promo files are only read at build time"})
    assert marked.status_code == 200, marked.text
    assert marked.json()["words"] == ("Not affected: the weak code is never run. promo files are only read at build "
                                      "time (decided by Kavya Iyer)")
    document = client.get(f"/api/vex.json?project={slug}", headers=engineer).json()
    assert problems(document) == ""  # the official OpenVEX schema
    [statement] = document["statements"]
    assert (statement["vulnerability"]["name"], statement["status"], statement["justification"]) == (
        "GHSA-8q59-q68h-6hv4", "not_affected", "vulnerable_code_not_in_execute_path")
    assert statement["products"][0]["@id"] == "https://github.com/deva-ghb/whitehats-proving-ground"
    assert statement["status_notes"].startswith("Decided by Kavya Iyer on ")
    clock[0] += timedelta(days=7)  # the next weekly scan still finds it: it stays, marked, with the reason
    floor.run(db, now=lambda: clock[0], decider=osv_only, sources=[(connector, Repository(FILES))])
    db.refresh(signal)
    assert (signal.closed_at, signal.vex_status) == (None, "not_affected")
    report = client.get(f"/api/vex?project={slug}", headers=engineer).json()
    assert report["counts"]["not_affected"] == 1 and report["statements"][0]["state"] == "in force"


@pytest.mark.skipif(shutil.which("docker") is None, reason="vexctl runs in its own container")
def test_vexctl_reads_the_projects_vex(finding, client, engineer, tmp_path):
    db, _connector, _clock, signal = finding
    slug = _project_slug(db, signal)
    client.post(f"/api/signals/{signal.id}/vex?project={slug}", headers=engineer, json={
        "justification": "inline_mitigations_already_exist", "reason": "the loader runs with SafeLoader"})
    (tmp_path / "project.vex.json").write_text(json.dumps(client.get(f"/api/vex.json?project={slug}", headers=engineer).json()))
    done = subprocess.run(["docker", "run", "--rm", "-v", f"{tmp_path}:/in:ro", "ghcr.io/openvex/vexctl:v0.3.0",
                           "merge", "/in/project.vex.json"], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["statements"][0]["vulnerability"]["name"] == "GHSA-8q59-q68h-6hv4"


def test_a_withdrawn_or_expired_statement_opens_the_finding_again(finding, client, engineer):
    db, _connector, _clock, signal = finding
    slug = _project_slug(db, signal)
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    marked = client.post(f"/api/signals/{signal.id}/vex?project={slug}", headers=engineer, json={
        "justification": "vulnerable_code_not_in_execute_path", "reason": "not used", "until": tomorrow}).json()
    db.refresh(signal)
    assert signal.vex_status == "not_affected"
    vex.apply(db, signal.project_id, now=lambda: datetime.now() + timedelta(days=3))  # past its end date
    db.refresh(signal)
    assert (signal.vex_status, signal.closed_at) == ("", None)  # open again, never closed by it
    vex.apply(db, signal.project_id)  # today it is still in force
    db.refresh(signal)
    assert signal.vex_status == "not_affected"
    assert client.post(f"/api/vex/{marked['id']}/withdraw?project={slug}", headers=engineer).status_code == 200
    db.refresh(signal)
    assert (signal.vex_status, signal.closed_at) == ("", None)
    db.add(VexStatement(org_id=signal.org_id, project_id=signal.project_id, fingerprint=signal.fingerprint,
                        vulnerability="GHSA-8q59-q68h-6hv4", product="https://github.com/x", status="not_affected",
                        justification="component_not_present", impact_statement="old", author="Someone",
                        expires_at=datetime.now() - timedelta(days=1)))
    db.commit()
    vex.apply(db, signal.project_id)
    db.refresh(signal)
    assert signal.vex_status == ""
    states = [row["state"] for row in client.get(f"/api/vex?project={slug}", headers=engineer).json()["statements"]]
    assert states == ["expired", "withdrawn"]  # history is kept, newest first


def test_marking_needs_triage_rights_a_justification_and_a_reason(finding, client, engineer, matrix):
    db, _connector, _clock, signal = finding
    viewer, signal_id = matrix.headers["viewer"], matrix.ids["signal_main"]
    assert client.post(f"/api/signals/{signal_id}/vex", headers=viewer,
                       json={"justification": "component_not_present", "reason": "x"}).status_code == 403
    url = f"/api/signals/{signal.id}/vex?project={_project_slug(db, signal)}"
    refused = client.post(url, headers=engineer, json={"justification": "because", "reason": "x"})
    assert refused.status_code == 422 and "five OpenVEX justifications" in refused.json()["detail"]
    assert client.post(url, headers=engineer, json={"justification": "component_not_present", "reason": ""}).status_code == 422


def test_a_supplier_document_that_is_not_openvex_is_refused(client, engineer):
    bad = {"@context": "https://openvex.dev/ns/v0.2.0", "@id": "x", "author": "a", "timestamp": "2026-10-31T00:00:00Z",
           "version": 1, "statements": [{"vulnerability": {"name": "CVE-1"}, "products": [{"@id": "pkg:pypi/pip@1"}],
                                         "status": "kind_of_fine"}]}
    refused = client.post("/api/vex/supplier", headers=engineer, json={"supplier": "Amazon", "document": bad})
    assert refused.status_code == 422 and "kind_of_fine" in refused.json()["detail"]


def _inventory() -> dict:
    """A one-part CycloneDX inventory: pip 25.0.1, which Trivy's database says CVE-2026-8643 affects."""
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "metadata": {"component": {"bom-ref": "image", "type": "container", "name": RUNTIME}},
            "components": [{"bom-ref": "pkg:pypi/pip@25.0.1", "type": "library", "name": "pip", "version": "25.0.1",
                            "purl": "pkg:pypi/pip@25.0.1"}],
            "dependencies": [{"ref": "image", "dependsOn": ["pkg:pypi/pip@25.0.1"]}]}


@pytest.mark.skipif(shutil.which("docker") is None, reason="Trivy runs in its own container")
def test_a_supplier_vex_for_a_back_ported_fix_turns_that_weakness_not_affected_with_the_supplier_named(
        finding, client, engineer, monkeypatch):
    db, _connector, _clock, signal = finding
    shop = {"id": "shop", "name": "shop-api", "type": "service.function@2", "environment": "prod",
            "properties": {"platform": "aws_lambda", "runtime": "python3.12", "package_type": "zip"}}
    document = {"schema_version": 1, "clone": "scan5-supplier", "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "t", "reason": "r"}, "access_paths": [], "credential_refs": [],
                "groups": [], "edges": [], "nodes": [shop]}
    slug = _project_slug(db, signal)
    made = client.post(f"/api/clones?project={slug}", headers=engineer, json={"reason": "r", "document": document})
    assert made.status_code == 200, made.text
    stored = Inventory(image=RUNTIME, digest="", made_at=datetime.now(), made_by="test", document=_inventory())
    monkeypatch.setattr(inventory, "stored", lambda db, image, **_: stored if image == RUNTIME else None)
    try:
        supplier = {"@context": "https://openvex.dev/ns/v0.2.0", "@id": "https://supplier.example/vex/1",
                    "author": "Amazon Linux security team", "timestamp": "2026-10-31T00:00:00Z", "version": 1,
                    "statements": [{"vulnerability": {"name": "CVE-2026-8643"}, "products": [{"@id": "pkg:pypi/pip@25.0.1"}],
                                    "status": "not_affected", "justification": "vulnerable_code_not_present",
                                    "impact_statement": "The fix is back-ported into this build of pip 25.0.1."}]}
        read = client.post(f"/api/vex/supplier?project={slug}", headers=engineer,
                           json={"supplier": "Amazon", "document": supplier, "image": RUNTIME})
        assert read.status_code == 200, read.text
        image_scans.check(db, login_for=lambda *_: None)
        row = db.get(ImageScan, RUNTIME)
        [pip] = [item for item in row.weaknesses if item["advisory"] == "CVE-2026-8643"]
        assert (pip["vex_status"], pip["vex_source"]) == ("not_affected", "Amazon")
        assert pip["vex_reason"] == ("Not affected: the weak code is not in this version. The fix is back-ported into "
                                     "this build of pip 25.0.1. (published by Amazon)")
        weak = client.get(f"/api/clones/{made.json()['id']}/weaknesses", headers=engineer).json()
        listed = weak["nodes"].get("shop", {})
        assert all(item["advisory"] != "CVE-2026-8643" for item in listed.get("weaknesses", []))  # not counted ...
        if listed:
            assert any(item["advisory"] == "CVE-2026-8643" for item in listed["not_affected"])  # ... but listed
    finally:
        shop["properties"]["runtime"] = "provided.al2023"
        client.post(f"/api/clones/{made.json()['id']}/versions", headers=engineer, json={"reason": "tidy", "document": document})

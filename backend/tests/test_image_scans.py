"""VS.5c: the image a function runs is scanned; its weaknesses become findings on the function, a later advisory raises
one of its own with no code change, and a new deployment closes the old image's findings."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.decisions_api.scanners.trivy import ImageWeakness
from app.models import ImageScan, Signal
from app.services import image_scans

REPO = "111122223333.dkr.ecr.ap-southeast-2.amazonaws.com/payments-worker"
OLD = f"{REPO}@sha256:{'a' * 64}"
NEW = f"{REPO}@sha256:{'b' * 64}"


def _weakness(advisory: str, package: str, severity: str, image: str = OLD) -> ImageWeakness:
    return ImageWeakness(image=image, digest=image.rpartition("@")[2], package=package, installed="1.0", fixed="1.1",
                         advisory=advisory, severity=severity, title=f"{package}: a flaw", cwe=())


class Scanner:
    """Stands in for Trivy: answers per image, and remembers what it was asked to scan and with which login."""

    def __init__(self, answers: dict[str, list[ImageWeakness]], error: str = ""):
        self.answers, self.error, self.asked = answers, error, []

    def __call__(self, image, login):
        self.asked.append((image, login))
        return self.answers.get(image, []), 2, type("Run", (), {"error": self.error})()


def _login(_db, _project, image):
    return (image.split("/")[0], "AWS", "token")


@pytest.fixture
def worker_clone(client, engineer, request):
    """A clone whose function runs OLD; yields a way to move it to another image."""
    document = {"schema_version": 1, "clone": f"vs5c-{request.node.name[5:40].replace('_', '-')}", "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "t", "reason": "r"}, "access_paths": [], "credential_refs": [],
                "groups": [], "edges": [],
                "nodes": [{"id": "worker", "type": "service.function@2", "name": "payments-worker", "environment": "prod",
                           "properties": {"platform": "aws_lambda", "runtime": "unknown", "package_type": "image",
                                          "image": OLD}}]}
    made = client.post("/api/clones", headers=engineer, json={"reason": "r", "document": document})
    assert made.status_code == 200, made.text
    clone_id = made.json()["id"]

    def run_image(image: str) -> None:
        document["nodes"][0]["properties"]["image"] = image
        saved = client.post(f"/api/clones/{clone_id}/versions", headers=engineer, json={"reason": "deploy", "document": document})
        assert saved.status_code == 200, saved.text

    yield run_image
    run_image("")  # other tests read every clone: its latest version runs no image any more
    with SessionLocal() as db:
        db.execute(delete(Signal).where(Signal.source == image_scans.SOURCE))
        db.execute(delete(ImageScan))
        db.commit()


def _findings(db, *, open_only: bool = True) -> list[Signal]:
    query = select(Signal).where(Signal.source == image_scans.SOURCE).order_by(Signal.id)
    if open_only:
        query = query.where(Signal.closed_at.is_(None))
    return list(db.scalars(query))


def test_the_first_scan_is_one_finding_naming_digest_packages_and_fixes_and_the_function(worker_clone):
    scanner = Scanner({OLD: [_weakness("CVE-1", "openssl", "critical"), _weakness("CVE-2", "curl", "medium")]})
    with SessionLocal() as db:
        assert image_scans.check(db, scan=scanner, login_for=_login)["scanned"] == 1
        [finding] = _findings(db)
    assert scanner.asked == [(OLD, (REPO.split("/")[0], "AWS", "token"))]
    assert (finding.severity, finding.entity, finding.node_key, finding.resource) == ("critical", "payments-worker", "worker", OLD)
    assert finding.title == "2 known weaknesses with a fix in the image payments-worker runs (1 critical, 1 medium)"
    assert f"payments-worker runs {OLD}" in finding.detail
    assert "CVE-1 (critical): openssl 1.0, fixed in 1.1; CVE-2 (medium): curl 1.0, fixed in 1.1." in finding.detail
    assert "2 more have no fixed version yet." in finding.detail


def test_an_advisory_published_later_raises_its_own_finding_with_no_code_change(worker_clone):
    clock = [datetime(2026, 10, 31, 9, 0)]
    first = Scanner({OLD: [_weakness("CVE-1", "openssl", "high")]})
    with SessionLocal() as db:
        image_scans.check(db, scan=first, login_for=_login, now=lambda: clock[0])
        clock[0] += timedelta(hours=2)
        image_scans.check(db, scan=first, login_for=_login, now=lambda: clock[0])
        assert len(first.asked) == 1  # not due again yet
        clock[0] += timedelta(days=1)
        later = Scanner({OLD: [_weakness("CVE-1", "openssl", "high"), _weakness("CVE-9", "glibc", "critical")]})
        image_scans.check(db, scan=later, login_for=_login, now=lambda: clock[0])
        titles = [finding.title for finding in _findings(db)]
    assert titles == ["1 known weaknesses with a fix in the image payments-worker runs (1 high)",
                      "CVE-9 in glibc 1.0, in the image payments-worker runs"]


def test_a_new_deployment_closes_the_old_images_findings_and_scans_the_new_one(worker_clone):
    scanner = Scanner({OLD: [_weakness("CVE-1", "openssl", "high")], NEW: []})
    with SessionLocal() as db:
        image_scans.check(db, scan=scanner, login_for=_login)
        worker_clone(NEW)
        assert image_scans.check(db, scan=scanner, login_for=_login)["closed"] == 1
        assert _findings(db) == []
        [closed] = _findings(db, open_only=False)
    assert closed.closed_reason == "image replaced"
    assert [image for image, _login in scanner.asked] == [OLD, NEW]


def test_a_failed_scan_raises_nothing_and_is_tried_again(worker_clone):
    with SessionLocal() as db:
        assert image_scans.check(db, scan=Scanner({}, error="docker pull failed: denied"), login_for=_login)["errors"] == 1
        assert _findings(db) == [] and db.get(ImageScan, OLD).last_error == "docker pull failed: denied"
        again = Scanner({OLD: [_weakness("CVE-1", "openssl", "high")]})
        assert image_scans.check(db, scan=again, login_for=_login)["scanned"] == 1
        assert len(_findings(db)) == 1


def test_a_public_image_needs_no_registry_sign_in():
    with SessionLocal() as db:
        assert image_scans.aws_login(db, 1, "public.ecr.aws/lambda/python:3.12@sha256:" + "c" * 64) is None


def test_an_image_is_pulled_through_any_project_that_can_sign_in_to_its_registry():
    from app.connectors.base import SourceError

    def login_for(_db, project, image):
        if project == 1:
            raise SourceError("AWS needs a read-only role. Long-lived access keys are not accepted.")
        return (image.split("/")[0], "AWS", f"token-{project}")

    assert image_scans._login(None, login_for, [1, 2], OLD) == ((REPO.split("/")[0], "AWS", "token-2"), 2)
    with pytest.raises(SourceError, match="read-only role"):
        image_scans._login(None, login_for, [1], OLD)



def test_every_project_that_runs_an_image_is_tried_for_its_registry_sign_in(monkeypatch):
    from types import SimpleNamespace

    from app.connectors.base import SourceError

    image = f"{REPO}@sha256:{'e' * 64}"
    running = [(SimpleNamespace(project_id=3), {}, image, "k3"), (SimpleNamespace(project_id=8), {}, image, "k8")]
    monkeypatch.setattr(image_scans, "_images", lambda db: running)
    monkeypatch.setattr(image_scans, "running_images", lambda db: [])
    tried = []

    def login_for(_db, project, _image):
        tried.append(project)
        if project != 8:
            raise SourceError("AWS needs a read-only role.")
        return ("registry", "AWS", "token")

    scanner = Scanner({image: []})
    with SessionLocal() as db:
        try:
            assert image_scans.check(db, scan=scanner, login_for=login_for)["errors"] == 0
        finally:
            db.query(ImageScan).filter(ImageScan.image == image).delete()
            db.commit()
    assert tried == [3, 8] and scanner.asked == [(image, ("registry", "AWS", "token"))]

"""SCAN.1: a scan plan is checked against the scanner catalogue and the project's scope before it runs; a push plan
targets the files it changed and the images of their services; the weekly floor finds what a narrow plan missed."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from app.connectors.github import RepositoryFiles
from app.db import SessionLocal
from app.decisions_api.suspect import Decision, EngineRun, Evidence, Suspect
from app.models import Connector, LLMCall, ScanPlanRecord, Signal
from app.scanning import PlanRefused, Scope, checked, floor, planner, store

REPO = "deva-ghb/whitehats-proving-ground"
WORKER_IMAGE = f"509594260364.dkr.ecr.ap-southeast-2.amazonaws.com/payments-worker@sha256:{'a' * 64}"
FILES = {"shop/orders.py": "def order():\n    return 1\n", "shop/pyproject.toml": '[project]\ndependencies = ["pyyaml==5.3.1"]\n',
         "worker/jobs.py": "def charge():\n    return 2\n", "worker/Dockerfile": "FROM python:3.12\n",
         "terraform/main.tf": 'resource "aws_s3_bucket" "b" {}\n'}
SCOPE = Scope(files={REPO: frozenset(FILES)}, images=frozenset({WORKER_IMAGE}))
NODES = [
    {"id": "shop", "name": "shop-api", "type": "service.function@2", "properties": {"code_repo": REPO, "code_folder": "shop"}},
    {"id": "worker", "name": "payments-worker", "type": "service.function@2",
     "properties": {"code_repo": REPO, "code_folder": "worker", "image": WORKER_IMAGE}},
]


def _plan(**target) -> dict:
    return {"trigger": "push", "made_by": "agent", "targets": [
        {"repo": REPO, "path": "shop/orders.py", "scanners": ["semgrep"], "reason": "changed"} | target]}


@pytest.mark.parametrize("plan, refusal", [
    (_plan(scanners=["nuclei"]), "nuclei is not in the scanner catalogue"),
    (_plan(path="shop/../../etc/passwd"), "is not a file of deva-ghb/whitehats-proving-ground this run can read"),
    (_plan(repo="someone/else"), "someone/else is not attached to this project"),
    (_plan(path="", image="docker.io/library/nginx@sha256:" + "b" * 64), "is not an image on this project's twin"),
    (_plan(options="--config=/tmp/rules.yml"), "A target may not carry options; scanner options come from the catalogue"),
    (_plan(flags=["--exclude-rule", "x"]), "A target may not carry flags"),
    ({**_plan(), "scanner_args": {"semgrep": "--disable-nosem=false"}}, "A scan plan may not carry scanner_args"),
    (_plan(reason=""), "says in one line why"),
])
def test_a_plan_asking_for_anything_outside_the_catalogue_or_the_scope_is_refused(plan, refusal):
    with pytest.raises(PlanRefused, match=refusal.replace("(", r"\(").replace(".", r"\.")):
        checked(plan, SCOPE)


def test_a_push_that_only_touches_the_worker_plans_its_files_and_its_image_and_nothing_else():
    plan = checked(planner.for_push(REPO, "main", "f" * 40, ["worker/jobs.py", "worker/Dockerfile"], NODES), SCOPE)
    assert [(target.path or target.image, target.scanners) for target in plan.targets] == [
        ("worker/Dockerfile", ("gitleaks", "checkov", "trivy")),
        ("worker/jobs.py", ("gitleaks", "semgrep")),
        (WORKER_IMAGE, ("trivy",)),
    ]
    assert plan.targets[1].reason == "changed in this push, part of payments-worker"
    assert plan.targets[2].reason == "the image payments-worker runs, since its code changed"
    assert not [target for target in plan.targets if target.path.startswith(("shop/", "terraform/"))]


def test_a_refused_plan_is_kept_with_its_refusal_and_every_file_in_scope_is_scanned_instead(client):
    with SessionLocal() as db:
        record, plan = store(db, org_id=1, project_ids=[1], kind="push", repo=REPO, data=_plan(scanners=["nuclei"]),
                             scope=SCOPE, fallback="every changed file")
        assert record.refused == "nuclei is not in the scanner catalogue."
        assert sorted(target.path for target in plan.targets) == sorted(FILES)
        assert plan.made_by == "every changed file"
        db.delete(record)
        db.commit()


class Repository:
    """A stand-in for the GitHub App's view of one repository's default branch."""

    def __init__(self, files: dict[str, str]):
        self.files = files

    def repositories(self):
        return [{"full_name": REPO, "default_branch": "main", "html_url": f"https://github.com/{REPO}"}]

    def repository_files(self, _repo):
        return RepositoryFiles(REPO, frozenset(self.files), self.files.get, {}, None)


def osv_only(root: Path, diff, *, planned=None):
    """Stands in for the engines: OSV-Scanner reports PyYAML 5.3.1 in shop/pyproject.toml when it is planned there."""
    found = []
    if "shop/pyproject.toml" in (planned or {}).get("osv-scanner", []) and "5.3.1" in (root / "shop/pyproject.toml").read_text():
        found.append(Suspect("osv-scanner", "GHSA-8q59-q68h-6hv4", "pyyaml 5.3.1: arbitrary code on yaml.load",
                             Evidence("shop/pyproject.toml", 2, 2), "critical"))
    return Decision("suspect" if found else "clean", found, [EngineRun("osv-scanner", "x", 0.1)])


@pytest.fixture
def floor_runs(row_id):
    with SessionLocal() as db:
        yield db, db.get(Connector, row_id)
        db.execute(delete(Signal).where(Signal.source == floor.SOURCE))
        db.execute(delete(ScanPlanRecord))
        db.commit()


def test_a_plan_that_skips_the_lock_file_still_finds_S04_through_the_floor_within_a_week(floor_runs):
    db, connector = floor_runs
    calls_before = db.scalar(select(func.count(LLMCall.id)))
    # The push's plan named the code it changed but skipped the lock file: the push finds nothing.
    narrow = checked({"trigger": "push", "made_by": "agent", "targets": [
        {"repo": REPO, "path": "shop/orders.py", "scanners": ["semgrep"], "reason": "changed"}]}, SCOPE)
    assert osv_only(Path("."), None, planned=narrow.paths_by_scanner(REPO)).suspects == []
    clock = [datetime(2026, 10, 31, 9, 0)]
    sources = [(connector, Repository(FILES))]
    [first] = floor.run(db, now=lambda: clock[0], decider=osv_only, sources=sources)
    plan = db.get(ScanPlanRecord, first["plan"])
    found = db.scalars(select(Signal).where(Signal.source == floor.SOURCE)).all()
    assert sorted(row.project_id for row in found) == sorted(plan.project_ids)  # once in each project that holds the repo
    assert {(row.title, row.severity, row.entity, row.branch, row.scan_plan_id) for row in found} == {
        ("pyyaml 5.3.1: arbitrary code on yaml.load", "critical", "shop/pyproject.toml:2", "main", plan.id)}
    assert plan.kind == "floor" and {"repo": REPO, "path": "shop/pyproject.toml", "scanners": ["gitleaks", "osv-scanner"],
                                     "reason": "the weekly floor scans every file"} in plan.plan["targets"]
    assert {"image": WORKER_IMAGE} not in plan.plan["targets"]  # no twin in this project names that image
    clock[0] += timedelta(days=3, hours=12)
    assert floor.run(db, now=lambda: clock[0], decider=osv_only, sources=sources) == []  # once a week
    clock[0] += timedelta(days=3, hours=12)
    [again] = floor.run(db, now=lambda: clock[0], decider=osv_only, sources=sources)
    assert (again["opened"], again["kept"]) == (0, len(plan.project_ids))  # the same weakness is the same finding
    assert db.scalar(select(func.count(LLMCall.id))) == calls_before  # no model call made a finding


def test_a_complete_floor_run_closes_what_is_no_longer_there_and_an_incomplete_one_closes_nothing(floor_runs):
    db, connector = floor_runs
    clock = [datetime(2026, 10, 31, 9, 0)]
    floor.run(db, now=lambda: clock[0], decider=osv_only, sources=[(connector, Repository(FILES))])
    fixed = {**FILES, "shop/pyproject.toml": '[project]\ndependencies = ["pyyaml==5.4"]\n'}

    def failed(root, diff, *, planned=None):
        return Decision("clean", [], [EngineRun("osv-scanner", "x", 0.1, error="timed out")])

    clock[0] += timedelta(days=7)
    floor.run(db, now=lambda: clock[0], decider=failed, sources=[(connector, Repository(fixed))])
    rows = db.scalars(select(Signal).where(Signal.source == floor.SOURCE)).all()
    assert rows and all(row.closed_at is None for row in rows)
    # An audit's finding on the same repository and branch is not this scan's: it stays open whatever the scan finds.
    first = rows[0]
    audited = Signal(org_id=first.org_id, project_id=first.project_id, severity="high", title="Audit finding",
                     resource=first.resource, branch=first.branch, fingerprint="audit:shop-orders-price", source="audit",
                     entity="shop/orders.py:12", origin="live")
    db.add(audited)
    db.commit()
    clock[0] += timedelta(days=7)
    floor.run(db, now=lambda: clock[0], decider=osv_only, sources=[(connector, Repository(fixed))])
    rows = db.scalars(select(Signal).where(Signal.source == floor.SOURCE)).all()
    assert {row.closed_reason for row in rows} == {floor.GONE}
    db.refresh(audited)
    assert audited.closed_at is None
    db.delete(audited)
    db.commit()


def test_the_full_scan_runs_once_a_week_not_every_day(floor_runs):
    db, connector = floor_runs
    clock = [datetime(2026, 10, 31, 9, 0)]
    assert floor.run(db, now=lambda: clock[0], decider=osv_only, sources=[(connector, Repository(FILES))])
    clock[0] += timedelta(days=6, hours=23)
    assert floor.run(db, now=lambda: clock[0], decider=osv_only, sources=[(connector, Repository(FILES))]) == []
    clock[0] += timedelta(hours=1)
    assert floor.run(db, now=lambda: clock[0], decider=osv_only, sources=[(connector, Repository(FILES))])

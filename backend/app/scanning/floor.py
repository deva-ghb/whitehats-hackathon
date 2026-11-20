"""The weekly floor (spec 5.3, SCAN.1): whatever the push plans said, every file of every attached repository is
scanned once a week by its default catalogue scanners, and every image on the twin with Trivy. A weak plan can delay a
finding by a week at most; it can never leave a blind spot.

Floor findings are the same findings as a push's: the same weakness in the same file of the same branch has one
fingerprint, so one found by a push is kept (with the plan that found it) and one found only here is opened here. A
complete floor run also closes what is no longer there. No model call makes a floor finding: scanners find, and the
confirmation step only ever judges what they found.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Iterable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..changes.findings import SOURCE as CODE_SOURCE
from ..changes.findings import fingerprint, repo_url
from ..connectors import routing
from ..connectors.github import GitHub
from ..decisions_api import decide
from ..models import Connector, ScanPlanRecord, Signal, utcnow
from ..services import finding_decisions
from . import request_images, store, twin_images, twin_nodes, vex
from .plan import Scope, every_file

log = logging.getLogger("whitehats.floor")

SOURCE = "whitehats:floor"
EVERY = timedelta(days=7)
CHECK_EVERY_S = 3600
MAX_FILES = 5_000
MAX_BYTES = 50_000_000
GONE = "no longer found by the weekly scan"
REMOVED = "the file was removed"
MADE_BY = "weekly floor: every file with its default scanners, every image on the twin"


def run(db: Session, *, now=utcnow, decider: Callable = decide,
        sources: Iterable[tuple[Connector, GitHub]] | None = None) -> list[dict]:
    """One pass: each attached repository whose last floor run is a week old or more is scanned. Returns a summary per run."""
    done = []
    for connector, source in sources if sources is not None else _github_sources(db):
        for repo in source.repositories():
            full = str(repo["full_name"])
            projects = routing.projects_for(db, connector, routing.Resource(
                identity=str(repo.get("html_url") or full), repo=full))
            if not projects or not _due(db, connector.org_id, full, now):
                continue
            done.append(_scan(db, connector.org_id, projects, source, repo, now, decider))
    return done


def _github_sources(db: Session) -> list[tuple[Connector, GitHub]]:
    rows = db.scalars(select(Connector).where(Connector.key == GitHub.key).order_by(Connector.id))
    return [(row, source) for row in rows if isinstance(source := connectors.for_row(db, row), GitHub)
            and row.status != "available"]


def _due(db: Session, org_id: int, repo: str, now) -> bool:
    last = db.scalar(select(ScanPlanRecord.created_at).where(
        ScanPlanRecord.org_id == org_id, ScanPlanRecord.kind == "floor", ScanPlanRecord.repo == repo,
        ScanPlanRecord.finished_at.is_not(None)).order_by(ScanPlanRecord.created_at.desc()).limit(1))
    return last is None or now() - last >= EVERY


def _scan(db: Session, org_id: int, projects: list[int], source: GitHub, repo: dict, now, decider: Callable) -> dict:
    full, branch = str(repo["full_name"]), str(repo.get("default_branch") or "")
    files = source.repository_files(repo)
    nodes = twin_nodes(db, projects)
    with tempfile.TemporaryDirectory(prefix="whitehats-floor-") as folder:
        root = Path(folder)
        written, skipped = _write(files, root)
        scope = Scope(files={full: frozenset(written)}, images=twin_images(nodes))
        images = sorted(scope.images)
        data = {"trigger": f"weekly floor of {full} ({branch or 'default branch'})", "made_by": MADE_BY,
                "targets": every_file(full, written, "the weekly floor scans every file")
                + [{"image": image, "scanners": ["trivy"], "reason": "the weekly floor scans every image on the twin"}
                   for image in images]}
        record, plan = store(db, org_id=org_id, project_ids=projects, kind="floor", repo=full, data=data, scope=scope,
                             fallback=MADE_BY)
        record.created_at = now()
        request_images(db, plan.images(), now)
        decision = decider(root, None, planned=plan.paths_by_scanner(full))
    complete = not any(engine.error for engine in decision.engines)
    found = {fingerprint(suspect.to_dict()): suspect for suspect in decision.suspects
             if suspect.status == "open" and suspect.is_above_low}
    counts = {"opened": 0, "kept": 0, "closed": 0}
    for project_id in projects:
        _apply(db, org_id, project_id, full, branch, record.id, found, set(written), set(files.paths), complete, counts)
    for project_id in projects:  # what a VEX statement covers stays listed, marked (SCAN.5)
        vex.apply(db, project_id, now)
    record.finished_at = now()
    record.summary = {"files": len(written), "skipped": skipped, "images": len(images), "complete": complete,
                      "engines": [{"engine": engine.engine, "seconds": round(engine.seconds, 1), "error": engine.error}
                                  for engine in decision.engines], **counts}
    db.commit()
    log.info("floor repo=%s files=%d %s", full, len(written), counts)
    return {"repo": full, "plan": record.id, **record.summary}


def _write(files, root: Path) -> tuple[list[str], int]:
    """Every readable text file, up to the limits; the scan sees the default branch as it is."""
    written, total, skipped = [], 0, 0
    for path in sorted(files.paths):
        if len(written) >= MAX_FILES or total >= MAX_BYTES or path.startswith("/") or ".." in path.split("/"):
            skipped += 1
            continue
        text = files.read(path)
        if text is None:  # binary or unreadable: nothing a catalogue scanner reads
            continue
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(path)
        total += len(text.encode())
    return written, skipped


def _apply(db: Session, org_id: int, project_id: int, repo: str, branch: str, plan_id: int, found: dict,
           scanned: set[str], present: set[str], complete: bool, counts: dict) -> None:
    # Only what the same scanners raise (this scan, or a push's): an audit's findings or another engine's are not this
    # scan's to keep or close; it never looked for them.
    rows = {row.fingerprint: row for row in db.scalars(select(Signal).where(
        Signal.org_id == org_id, Signal.project_id == project_id, Signal.resource == repo_url(repo),
        Signal.branch == branch, Signal.fingerprint != "", Signal.closed_at.is_(None),
        Signal.source.in_((SOURCE, CODE_SOURCE))))}
    decided = finding_decisions.active(db, project_id)
    for key, suspect in found.items():
        if key in rows:  # a push found it first: it keeps the plan that found it
            counts["kept"] += 1
            continue
        if key in decided:  # an approver called it a false positive or accepted its risk (FD.1)
            continue
        where = f"{suspect.evidence.path}:{suspect.evidence.start_line}"
        db.add(Signal(
            org_id=org_id, project_id=project_id, severity=suspect.severity, title=suspect.title[:240],
            detail=(f"{where}. Found by the weekly scan of {repo} ({branch or 'default branch'}), which scans every file "
                    f"whatever a push planned.{' ' + suspect.note if suspect.note else ''}"),
            entity=where, resource=repo_url(repo), source=SOURCE, origin="live", fingerprint=key, branch=branch,
            scan_plan_id=plan_id))
        counts["opened"] += 1
    if not complete:  # an engine that did not finish cannot say a weakness is gone
        return
    for key, row in rows.items():
        path = row.entity.rsplit(":", 1)[0]
        if key in found:
            continue
        if path not in present:
            row.closed_at, row.closed_reason = utcnow(), REMOVED
        elif path in scanned:
            row.closed_at, row.closed_reason = utcnow(), GONE
        else:
            continue
        counts["closed"] += 1


"""VS.5c: the container images the functions run, scanned with Trivy; their known weaknesses become findings.

Discovery records on each function node the image it runs, with the digest Lambda resolved (`properties.image`). A new
deployment is a new digest, so a pushed image is scanned once it is running. Every image is scanned again each day,
so an advisory published later raises a finding with no code change.

Findings are signals on the function's node:
- the first scan of a digest raises one finding for it: how many weaknesses have a fix, the worst severity, and the
  worst by name (advisory, package, installed and fixed version), since moving the image fixes them together;
- a later scan of the same digest raises one finding per advisory that was not there before;
- when the node runs another digest, the old digest's findings close ("image replaced").
Only weaknesses with a fixed version count; how many have none is said on the finding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors import SourceError, for_row
from ..connectors.aws import Aws
from ..decisions_api.scanners import trivy
from ..models import Clone, Connector, ImageScan, Inventory, ProjectConnection, Signal, utcnow
from ..scanning import inventory, vex
from .clones import read_clone

log = logging.getLogger("whitehats.images")

SOURCE = "whitehats:image"
CHECK_EVERY_S = 600  # new digests are looked for this often
RESCAN_AFTER = timedelta(days=1)
SCAN_TIMEOUT = 300
NAMED = 10  # weaknesses named on a first-scan finding
_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

Scan = Callable[[str, trivy.Login | None], tuple[list[trivy.ImageWeakness], int, object]]
LoginFor = Callable[[Session, int, str], trivy.Login | None]


def _through_inventory(db: Session, now, projects_of: dict[str, list[int]]) -> Scan:
    """Scan an image's stored inventory (SCAN.2): the image is pulled only when its digest has none yet. The VEX
    statements in force for it go to Trivy as input (SCAN.5); what they cover comes back marked, in words."""
    def scan(image: str, login: trivy.Login | None):
        row = inventory.stored(db, image, login=login, now=now)
        if row is None:
            failed = db.get(Inventory, image)
            return [], 0, type("Run", (), {"error": failed.last_error if failed else "no inventory"})()
        projects = projects_of.get(image, [])
        found, unfixed, run = trivy.scan_inventory(row.document, image, row.digest, timeout=SCAN_TIMEOUT,
                                                   vex=vex.for_image(db, projects, image, now()))
        statements = {item.vulnerability: item for project_id in projects for item in vex.in_force(db, project_id, now())
                      if item.image in (image, "") and item.product.startswith("pkg:")}
        return [replace(item, vex_reason=vex.words(statements[item.advisory]),
                        vex_source=statements[item.advisory].supplier or "WhiteHats")
                if item.vex_status and item.advisory in statements else item for item in found], unfixed, run
    return scan


def aws_login(db: Session, project_id: int, image: str) -> trivy.Login | None:
    """A pull token from one of the project's AWS connections, for an image in ECR; None for a public image."""
    if ".dkr.ecr." not in image.split("/", 1)[0]:
        return None
    rows = db.scalars(select(Connector).join(ProjectConnection, ProjectConnection.connector_id == Connector.id)
                      .where(ProjectConnection.project_id == project_id, Connector.key == Aws.key))
    problems = []
    for row in rows:
        source = for_row(db, row)
        if not isinstance(source, Aws):
            continue
        try:
            return source.registry_login(image)
        except SourceError as exc:
            problems.append(str(exc))
    raise SourceError("; ".join(problems) or "No AWS connection on this project can pull images from its registry.")


def running_images(db: Session) -> list[tuple[Clone, dict, str]]:
    """(clone, function node, image) for every function node that names its image, in the latest version of every clone."""
    return [(clone, node, image) for clone, node, image, key in _images(db) if key == "image" and "@sha256:" in image]


def _images(db: Session) -> list[tuple[Clone, dict, str, str]]:
    """(clone, node, image, which): the image a node runs ("image"), is built from ("base_image") or, for a zip function,
    the AWS runtime it runs on ("runtime"), in the latest version of every clone."""
    found = []
    for clone in db.scalars(select(Clone).order_by(Clone.id)):
        document = read_clone(db, clone.org_id, clone.id) or {}
        for node in document.get("nodes", []):
            for key in ("image", "base_image"):
                image = str((node.get("properties") or {}).get(key) or "")
                if image:
                    found.append((clone, node, image, key))
            image, how = inventory.image_of(node)
            if how == "runs on the AWS runtime":  # a zip function's runtime is scanned too (SCAN.2)
                found.append((clone, node, image, "runtime"))
    return found


def check(db: Session, *, scan: Scan | None = None, login_for: LoginFor = aws_login, now=utcnow) -> dict:
    """Scan what is due (an image never scanned, or scanned over a day ago), then raise and close findings.

    A base image is scanned too, so the node's weaknesses can show it (SC.1); only an image that runs raises findings
    here, since a base image is judged on the change that names it (the Trivy engine)."""
    running = running_images(db)
    every = _images(db)
    projects_of: dict[str, list[int]] = {}
    for clone, _node, image, _key in every:
        projects_of[image] = sorted({*projects_of.get(image, []), clone.project_id})  # every project, not the first
    scan = scan or _through_inventory(db, now, projects_of)
    scanned = errors = 0
    for image in sorted({item[2] for item in every}):
        row = db.get(ImageScan, image)
        asked = row is not None and row.requested_at is not None and (
            row.scanned_at is None or row.requested_at > row.scanned_at)  # a scan plan asked for it (SCAN.1)
        if row is not None and row.scanned_at is not None and now() - row.scanned_at < RESCAN_AFTER and not asked:
            continue
        before = {item["advisory"] for item in (row.weaknesses if row and row.scanned_at else [])}
        first = row is None or row.scanned_at is None
        row = row or ImageScan(image=image)
        db.add(row)
        try:
            login, signed_in_by = _login(db, login_for, projects_of[image], image)
            found, unfixed, run = scan(image, login)
            error = getattr(run, "error", "")
        except SourceError as exc:
            found, unfixed, error = [], 0, str(exc)
        if error:
            row.last_error = error[:2000]
            errors += 1
            log.warning("image scan failed image=%s error=%s", image, error[:200])
            db.commit()
            continue
        row.weaknesses = [asdict(item) for item in found]
        row.unfixed, row.scanned_at, row.last_error = unfixed, now(), ""
        scanned += 1
        for project_id, users in _users(running, image).items():
            if login is not None and _org(db, project_id) != _org(db, signed_in_by):
                continue  # a private image is read for the company whose connection could pull it, and no other
            _raise(db, project_id, users, row, None if first else before)
        db.commit()
    closed = _close_replaced(db, running, now)
    db.commit()
    return {"images": len({item[2] for item in running}), "scanned": scanned, "errors": errors, "closed": closed}


def _org(db: Session, project_id: int) -> int | None:
    from ..models import Project

    project = db.get(Project, project_id)
    return project.org_id if project else None


def _login(db: Session, login_for: LoginFor, projects: list[int], image: str) -> tuple[trivy.Login | None, int]:
    """Sign in to the image's registry through any project that runs it: one project's connection may have no role
    while another's can pull. Returns the login and the project that made it; fails only when none can."""
    problems: list[str] = []
    for project_id in projects:
        try:
            return login_for(db, project_id, image), project_id
        except SourceError as exc:
            if str(exc) not in problems:
                problems.append(str(exc))
    raise SourceError("; ".join(problems) or "No project that runs this image can pull it.")


def _users(running: list[tuple[Clone, dict, str]], image: str) -> dict[int, list[tuple[Clone, dict]]]:
    """{project: the (clone, node) pairs that run this image}: one finding per image per project, naming them all."""
    found: dict[int, list[tuple[Clone, dict]]] = {}
    for clone, node, item in running:
        if item == image:
            found.setdefault(clone.project_id, []).append((clone, node))
    return found


def _fingerprint(image: str, advisory: str = "") -> str:
    return "|".join(part for part in ("image", image.rpartition("@")[2], advisory) if part)[:240]


def _open(db: Session, project_id: int, key: str) -> bool:
    return db.scalar(select(Signal.id).where(
        Signal.project_id == project_id, Signal.source == SOURCE, Signal.fingerprint == key,
        Signal.closed_at.is_(None))) is not None


def _decided(db: Session, project_id: int, key: str) -> bool:
    """An approver called it a false positive or accepted its risk (FD.1): not raised again."""
    from . import finding_decisions

    return key in finding_decisions.active(db, project_id)


def _names(users: list[tuple[Clone, dict]]) -> str:
    names = sorted({str(node.get("name", node["id"])) for _clone, node in users})
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _raise(db: Session, project_id: int, users: list[tuple[Clone, dict]], row: ImageScan, before: set[str] | None) -> None:
    """A project not yet told about this digest gets one finding for it, naming every service that runs it (SCAN.2:
    one base image, one finding); after that, a finding per advisory published since."""
    if not _open_weaknesses(row):
        return
    names = _names(users)
    summary_key = _fingerprint(row.image)
    told = db.scalar(select(Signal.id).where(
        Signal.project_id == project_id, Signal.source == SOURCE, Signal.fingerprint == summary_key))
    if told is None:
        _add(db, users, summary_key, _open_weaknesses(row)[0]["severity"], _summary_title(names, row),
             _summary_detail(names, row))
        return
    for item in _open_weaknesses(row):
        key = _fingerprint(row.image, item["advisory"])
        if before is None or item["advisory"] in before or _open(db, project_id, key) or _decided(db, project_id, key):
            continue
        _add(db, users, key, item["severity"],
             f"{item['advisory']} in {item['package']} {item['installed']}, in the image {names} runs",
             f"Published since this image was last scanned. {names} runs {row.image}. {_line(item)}. {item['title']}")


def _open_weaknesses(row: ImageScan) -> list[dict]:
    """The weaknesses no VEX statement covers; the covered ones stay in the scan, listed as not affected."""
    return [item for item in row.weaknesses if not item.get("vex_status")]


def _summary_title(names: str, row: ImageScan) -> str:
    open_ones = _open_weaknesses(row)
    counts = {level: sum(item["severity"] == level for item in open_ones) for level in _ORDER}
    split = ", ".join(f"{count} {level}" for level, count in counts.items() if count)
    return f"{len(open_ones)} known weaknesses with a fix in the image {names} runs ({split})"


def _summary_detail(names: str, row: ImageScan) -> str:
    open_ones = _open_weaknesses(row)
    worst = "; ".join(_line(item) for item in open_ones[:NAMED])
    more = f" And {len(open_ones) - NAMED} more." if len(open_ones) > NAMED else ""
    unfixed = f" {row.unfixed} more have no fixed version yet." if row.unfixed else ""
    covered = len(row.weaknesses) - len(open_ones)
    vexed = f" {covered} more are not affected, by a VEX statement." if covered else ""
    return (f"{names} runs {row.image}. Worst first: {worst}.{more}{unfixed}{vexed} "
            "Moving to a newer base image, or upgrading these packages in the build, fixes them.")


def _line(item: dict) -> str:
    return f"{item['advisory']} ({item['severity']}): {item['package']} {item['installed']}, fixed in {item['fixed']}"


def _add(db: Session, users: list[tuple[Clone, dict]], key: str, severity: str, title: str, detail: str) -> None:
    clone, node = users[0]
    db.add(Signal(
        org_id=clone.org_id, project_id=clone.project_id, severity=severity, title=title[:240], detail=detail,
        entity=_names(users)[:160], resource=str((node.get("properties") or {}).get("image", ""))[:300],
        source=SOURCE, origin="live", clone_id=clone.id, node_key=str(node["id"])[:80], fingerprint=key))


def _close_replaced(db: Session, running: list[tuple[Clone, dict, str]], now) -> int:
    """Close the findings of a digest no service in the project runs any more: the deployment replaced the image."""
    current = {(clone.project_id, image.rpartition("@")[2]) for clone, _node, image in running}
    closed = 0
    for signal in db.scalars(select(Signal).where(Signal.source == SOURCE, Signal.closed_at.is_(None))):
        parts = signal.fingerprint.split("|")
        if len(parts) < 2 or (signal.project_id, parts[1]) in current:
            continue
        signal.closed_at, signal.closed_reason = now(), "image replaced"
        closed += 1
    return closed

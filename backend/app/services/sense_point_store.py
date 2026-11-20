"""Sense points kept per project and clone version: the analysis suggests, a person accepts, rejects or places by hand.

The analysis itself (attack paths, choke points, the greedy cover) is services/sense_points.py and stays pure.
Here its answer is stored as rows, one per node per saved drawing, with the person's decision beside it, and the
coverage figure is worked out from the paths and the decisions each time it is read, so it can never disagree with them.
Everything is keyed by the company and the clone, so another company's clone reads as missing.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Clone, CloneVersion, SensePoint
from . import sense_points as analysis
from .clones import library, read_clone

STATUSES = ("suggested", "accepted", "rejected")
KEPT = ("accepted", "rejected")   # decisions that a new analysis keeps (and so are the points placed by hand)


class Refused(Exception):
    """Something the person asked for that cannot be done; the message says why, in plain words."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _clone(db: Session, org_id: int, clone_id: int) -> Clone | None:
    return db.query(Clone).filter_by(id=clone_id, org_id=org_id).one_or_none()


def _versions(clone: Clone) -> dict[int, CloneVersion]:
    return {version.number: version for version in clone.versions}


def _rows(db: Session, version_id: int) -> list[SensePoint]:
    return db.query(SensePoint).filter_by(clone_version_id=version_id).order_by(SensePoint.rank == 0, SensePoint.rank, SensePoint.id).all()


def pinned_number(db: Session, clone: Clone) -> int:
    """The version the stored sense points belong to: the newest one that has any, else the latest drawing."""
    numbers = sorted(_versions(clone))
    for number in reversed(numbers):
        if db.query(SensePoint.id).filter_by(clone_version_id=_versions(clone)[number].id).first():
            return number
    return numbers[-1]


def _type_words(document: dict) -> dict[str, tuple[str, str]]:
    """Per node: its mode and the library's own reason for watching a node of its kind."""
    found = {}
    for node in document.get("nodes", []):
        defaults = (library().get(str(node.get("type", "")).split("@", 1)[0]) or {}).get("default_sense_points") or []
        first = defaults[0] if defaults else {}
        found[node["id"]] = (first.get("mode") or "telemetry", first.get("reason") or "")
    return found


def _reason(node: dict, point: dict, words: tuple[str, str], total: int, extra: bool) -> str:
    on = f"{node['name']} is on {point['paths']} of {total} attack paths and has no logging or control."
    why = f" {words[1].rstrip('.')}." if words[1] else ""
    tail = " Other suggestions already cover most of those paths." if extra else " Watching it covers them."
    return f"{on}{why}{tail}"


def _suggestions(document: dict) -> tuple[list[dict], dict]:
    result = analysis.analyse(document)
    ranked = result["sense_points"] + analysis.also_on_paths(document, result)
    return ranked, result


def suggest(document: dict) -> tuple[list[dict], dict]:
    """The list the Where to watch panel shows, in its order: node, mode and paths for each, and the analysis behind it.

    The stored rows are made from exactly this, so a measurement of it measures what a person is shown.
    """
    ranked, result = _suggestions(document)
    nodes = {node["id"]: node for node in document["nodes"]}
    covered = len(result["sense_points"])
    return [{"rank": place, "target": point["target"], "name": nodes[point["target"]]["name"], "mode": point["mode"], "modes": point["modes"],
             "paths": point["paths"], "is_cover": place <= covered} for place, point in enumerate(ranked, start=1)], result


def run_analysis(db: Session, org_id: int, clone_id: int, author: str) -> dict | None:
    """Analyse the latest drawing and store the suggestions. Decisions already made carry over to it; old suggestions go."""
    clone = _clone(db, org_id, clone_id)
    if clone is None:
        return None
    versions = _versions(clone)
    latest = versions[max(versions)]
    before = pinned_number(db, clone)
    document = read_clone(db, org_id, clone_id)
    nodes = {node["id"]: node for node in document["nodes"]}
    ranked, result = _suggestions(document)
    words = _type_words(document)
    kept = {row.node_key: row for row in _rows(db, versions[before].id) if row.status in KEPT}  # a hand placed point is always accepted
    covered = len(result["sense_points"])
    for row in _rows(db, latest.id):
        if row.node_key not in kept or kept[row.node_key] is not row:
            db.delete(row)
    db.flush()
    taken = {row.node_key for row in _rows(db, latest.id)}
    for key, old in kept.items():
        if key in nodes and key not in taken:
            db.add(SensePoint(org_id=org_id, project_id=clone.project_id, clone_version_id=latest.id, node_key=key, mode=old.mode,
                              reason=old.reason, status=old.status, origin=old.origin, paths=old.paths, rank=0, decided_by=old.decided_by))
    db.flush()
    held = {row.node_key: row for row in _rows(db, latest.id)}
    for place, point in enumerate(ranked, start=1):
        node = nodes[point["target"]]
        mode, reason = point["mode"], _reason(node, point, words[node["id"]], len(result["paths"]), place > covered)
        row = held.get(node["id"])
        if row is None:
            db.add(SensePoint(org_id=org_id, project_id=clone.project_id, clone_version_id=latest.id, node_key=node["id"], mode=mode,
                              reason=reason, status="suggested", origin="analysis", paths=point["paths"], rank=place))
        elif row.origin == "analysis":
            row.mode, row.reason, row.paths, row.rank = mode, reason, point["paths"], place
    db.commit()
    return overview(db, org_id, clone_id)


def _coverage(document: dict, rows: list[SensePoint]) -> dict:
    """The share of attack paths with an accepted sense point on them. A suggestion or a rejection covers nothing."""
    result = analysis.analyse(document)
    paths = result["paths"]
    accepted = {row.node_key for row in rows if row.status == "accepted"}
    watched = sum(any(analysis.passes(path, node) for node in accepted) for path in paths)
    return {
        "paths_total": len(paths), "paths_watched": watched, "share": (watched / len(paths)) if paths else 0.0,
        "is_complete": result["is_complete"], "entry_points": result["entry_points"], "crown_jewels": result["crown_jewels"],
    }


def overview(db: Session, org_id: int, clone_id: int) -> dict | None:
    """What the screens need: the stored points, the version they belong to, and the coverage they give."""
    clone = _clone(db, org_id, clone_id)
    if clone is None:
        return None
    versions = _versions(clone)
    number = pinned_number(db, clone)
    rows = _rows(db, versions[number].id)
    document = read_clone(db, org_id, clone_id, number)
    named = {node["id"]: node for node in document["nodes"]}
    counts = {status: sum(row.status == status for row in rows) for status in STATUSES}
    return {
        "clone": clone_id, "version": number, "latest_version": max(versions), "is_current": number == max(versions),
        "has_run": bool(rows), "counts": counts, "coverage": _coverage(document, rows),
        "points": [_view(row, named[row.node_key]) for row in rows if row.node_key in named],
    }


def _view(row: SensePoint, node: dict) -> dict:
    """One stored point as the screens read it. `mode` is the one stored; `modes` are all the WhiteHats that can work from this node."""
    return {"id": row.id, "node": row.node_key, "node_name": node["name"], "mode": row.mode, "modes": analysis.modes(node), "reason": row.reason,
            "status": row.status, "origin": row.origin, "paths": row.paths, "rank": row.rank, "decided_by": row.decided_by}


def decide(db: Session, org_id: int, point_id: int, status: str, author: str) -> tuple[SensePoint, dict] | None:
    """Accept, reject, or put back a suggestion. A point placed by hand can only be accepted or removed."""
    row = db.query(SensePoint).filter_by(id=point_id, org_id=org_id).one_or_none()
    if row is None:
        return None
    if status not in STATUSES:
        raise Refused("A sense point is accepted, rejected or suggested.", 422)
    if row.origin == "manual" and status != "accepted":
        raise Refused("A sense point you placed by hand is accepted. Remove it instead.")
    row.status = status
    row.decided_by = author if status in KEPT else ""
    db.commit()
    version = db.get(CloneVersion, row.clone_version_id)
    return row, overview(db, org_id, version.clone_id)


def place(db: Session, org_id: int, clone_id: int, node: str, mode: str, reason: str, author: str) -> dict | None:
    """Put a sense point on a node by hand. It is accepted at once, and survives every later analysis."""
    clone = _clone(db, org_id, clone_id)
    if clone is None:
        return None
    versions = _versions(clone)
    number = pinned_number(db, clone)
    if number != max(versions) and db.query(SensePoint.id).filter_by(clone_version_id=versions[number].id).first():
        raise Refused(f"These sense points were worked out on version {number}, and the clone is now at {max(versions)}. "
                      "Run the analysis again first; it keeps what you have decided.")
    document = read_clone(db, org_id, clone_id, number)
    found = next((item for item in document["nodes"] if item["id"] == node), None)
    if found is None:
        raise Refused("That node is not in this clone.", 404)
    words = _type_words(document)[node]
    row = db.query(SensePoint).filter_by(clone_version_id=versions[number].id, node_key=node).one_or_none()
    if row is None:
        row = SensePoint(org_id=org_id, project_id=clone.project_id, clone_version_id=versions[number].id, node_key=node)
        db.add(row)
    row.mode = mode or words[0]
    row.reason = reason or f"Placed by hand by {author}. {words[1]}".rstrip()
    row.status, row.origin, row.rank, row.decided_by = "accepted", "manual", 0, author
    db.commit()
    return overview(db, org_id, clone_id)


def remove(db: Session, org_id: int, point_id: int) -> dict | None:
    """Take away a point placed by hand. A suggestion from the analysis is put back with a decision, not deleted."""
    row = db.query(SensePoint).filter_by(id=point_id, org_id=org_id).one_or_none()
    if row is None:
        return None
    if row.origin != "manual":
        raise Refused("Only a sense point you placed by hand can be removed. Set a suggestion back to suggested instead.")
    clone_id = db.get(CloneVersion, row.clone_version_id).clone_id
    db.delete(row)
    db.commit()
    return overview(db, org_id, clone_id)

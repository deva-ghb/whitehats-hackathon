"""Open cloud alerts on the map: which buildings an open alert concerns, so the Studio can show it on that building.

A Cloud Posture alert names the resource the change touched (a bucket, a trail, a security group, a user), as AWS's
own record names it. A map node discovered from AWS carries its name and its ARN. An alert belongs to a node when its
resource is the node's name, its ARN, or the last part of its ARN (a bucket's ARN ends in the bucket's name).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Signal

MOST_PER_NODE = 5


def _names(node: dict) -> set[str]:
    arn = str((node.get("properties") or {}).get("arn") or "")
    names = {str(node.get("name") or ""), arn}
    if arn:
        names.add(arn.rsplit(":", 1)[-1].rsplit("/", 1)[-1])
    return {n for n in names if n}


def for_clone(db: Session, project_id: int, document: dict) -> dict:
    """{"nodes": {node id: [alert, newest first]}, "unplaced": [open alerts with a resource no node matches, the ones
    that just happened], "unplaced_backlog": how many unplaced ones were there before WhiteHats first looked}."""
    rows = list(db.scalars(select(Signal).where(Signal.project_id == project_id, Signal.closed_at.is_(None),
                                                Signal.learning.is_(False), Signal.resource != "",
                                                Signal.source.like("whitehat:%"))
                           .order_by(Signal.created_at.desc()).limit(200)))
    by_name: dict[str, list[str]] = {}
    for node in document.get("nodes") or []:
        for name in _names(node):
            by_name.setdefault(name, []).append(str(node["id"]))
    nodes: dict[str, list[dict]] = {}
    unplaced = []
    for row in rows:
        alert = {"id": row.id, "title": row.title, "severity": row.severity, "detail": row.detail,
                 "resource": row.resource, "at": row.created_at.isoformat() if row.created_at else None,
                 "incident_id": row.incident_id, "baseline": row.baseline}
        targets = by_name.get(row.resource) or by_name.get(row.resource.rsplit("/", 1)[-1]) or []
        if not targets:
            unplaced.append(alert)
        for node_id in targets:
            if len(nodes.setdefault(node_id, [])) < MOST_PER_NODE:
                nodes[node_id].append(alert)
    # Not drawn on the map (users, roles, the account itself): live ones first, most serious first; the backlog counted.
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    live = sorted((a for a in unplaced if not a["baseline"]), key=lambda a: rank.get(a["severity"], 9))
    return {"nodes": nodes, "unplaced": live[:20], "unplaced_backlog": sum(1 for a in unplaced if a["baseline"])}

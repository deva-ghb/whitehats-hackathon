"""The knowledge sphere's versions (KS.1): the record of truth, in the platform's own tables.

A version is a list of operations on the one before it: put a node, drop a node, put a relationship, drop one. Replaying
them in order gives any version. The graph database (app/sphere/graph.py) holds the newest version for queries; when
it is missing or behind, it is rebuilt from these.

A node is a dict:
    key        stable across versions ("rule:order-price-from-catalogue")
    kind       Concept | Rule | Exception
    text       the rule or exception in one sentence, or the concept's name
    detail     a longer explanation (optional)
    status     proposed | known
    protects   for a rule: money | data | access | availability | other (what breaking it costs)
    cites      [{"kind": "file", "path", "lines": [first, last]} | {"kind": "node", "arn", "name"} |
                {"kind": "finding", "id", "title"} | {"kind": "decision", "ref", "by", "at", "label"}]
    lost       the file and node citations that are gone (kept so the screen can say so)
    taught_by  the decision refs that made it known ("fix:12", "vex:4", "confirm:31")
A relationship is {"kind", "source", "target", "taught_by"}.
"""

from __future__ import annotations

import copy
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SphereGraphState, SphereVersion
from . import graph

log = logging.getLogger("whitehats.sphere")

EVIDENCE = ("file", "node")  # citations that can disappear; decisions and findings are history


def edge_id(edge: dict) -> str:
    return f"{edge['kind']}|{edge['source']}|{edge['target']}"


class State:
    """One version of the sphere, in memory."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}

    def apply(self, ops: list[dict]) -> None:
        for op in ops:
            if op["op"] == "put_node":
                self.nodes[op["node"]["key"]] = copy.deepcopy(op["node"])
            elif op["op"] == "drop_node":
                self.nodes.pop(op["key"], None)
                for eid in [eid for eid, e in self.edges.items() if op["key"] in (e["source"], e["target"])]:
                    self.edges.pop(eid)
            elif op["op"] == "put_edge":
                edge = copy.deepcopy(op["edge"])
                if edge["source"] in self.nodes and edge["target"] in self.nodes:
                    self.edges[edge_id(edge)] = edge
            elif op["op"] == "drop_edge":
                self.edges.pop(op["id"], None)

    def as_dict(self) -> dict:
        return {"nodes": sorted(self.nodes.values(), key=lambda n: (n["kind"], n["key"])),
                "edges": sorted(self.edges.values(), key=edge_id)}


def latest_number(db: Session, project_id: int) -> int:
    return int(db.scalar(select(func.max(SphereVersion.number)).where(SphereVersion.project_id == project_id)) or 0)


def versions(db: Session, project_id: int, upto: int | None = None) -> list[SphereVersion]:
    q = select(SphereVersion).where(SphereVersion.project_id == project_id)
    if upto is not None:
        q = q.where(SphereVersion.number <= upto)
    return list(db.scalars(q.order_by(SphereVersion.number)))


def state(db: Session, project_id: int, upto: int | None = None) -> State:
    s = State()
    for v in versions(db, project_id, upto):
        s.apply(v.ops or [])
    return s


def diff(before: State, after: State) -> dict:
    """What a later version learned, forgot and changed, by key."""
    def changed(a: dict, b: dict) -> bool:
        return {k: v for k, v in a.items() if k != "lost"} != {k: v for k, v in b.items() if k != "lost"}

    return {
        "learned": sorted(k for k in after.nodes if k not in before.nodes),
        "forgot": sorted(k for k in before.nodes if k not in after.nodes),
        "changed": sorted(k for k in after.nodes if k in before.nodes and changed(before.nodes[k], after.nodes[k])),
        "edges_learned": sorted(e for e in after.edges if e not in before.edges),
        "edges_forgot": sorted(e for e in before.edges if e not in after.edges),
    }


def record(db: Session, org_id: int, project_id: int, ops: list[dict], *, actor: str, reason: str,
           decision_id: int | None = None) -> SphereVersion | None:
    """Save a new version made of these operations (none: no version). The caller commits; the graph follows after."""
    if not ops:
        return None
    v = SphereVersion(org_id=org_id, project_id=project_id, number=latest_number(db, project_id) + 1, actor=actor,
                      reason=reason[:2000], decision_id=decision_id, ops=ops)
    db.add(v)
    db.flush()
    return v


def sync_graph(db: Session, project_id: int) -> bool:
    """Bring the graph database up to the newest version. False when it is not configured or cannot be reached; the
    versions stay the record, and the next sync catches up."""
    if not graph.configured():
        return False
    from ..models import Project

    project = db.get(Project, project_id)
    row = db.get(SphereGraphState, project_id) or SphereGraphState(
        project_id=project_id, org_id=project.org_id if project else 0, applied=0)
    newest = latest_number(db, project_id)
    if row.applied == newest and row.applied:
        return True
    try:
        with graph.Graph(project_id) as g:
            # Rebuilt whole from the versions: a few hundred nodes at most, and never half-applied.
            g.clear()
            current = state(db, project_id)
            for node in current.nodes.values():
                g.put_node(node["kind"], node["key"], _graph_props(node))
            for edge in current.edges.values():
                g.put_edge(edge["kind"], edge["source"], edge["target"], {"taught_by": edge.get("taught_by") or []})
    except graph.Unavailable as exc:
        log.warning("knowledge sphere graph for project %s not synced: %s", project_id, exc)
        return False
    row.applied = newest
    db.merge(row)
    db.commit()
    return True


def _graph_props(node: dict) -> dict:
    return {k: v for k, v in node.items() if k not in ("key", "kind") and v is not None}


def around(db: Session, project_id: int, paths: list[str]) -> list[dict]:
    """The known and proposed nodes a review of these files should keep in mind: those citing the files, and their
    neighbourhood, from the graph database when it is there (else from the versions)."""
    current = state(db, project_id)
    wanted = set(paths)
    seeds = [n["key"] for n in current.nodes.values()
             if any(c.get("kind") == "file" and c.get("path") in wanted for c in n.get("cites") or [])]
    if not seeds:
        return []
    if sync_graph(db, project_id):
        try:
            with graph.Graph(project_id) as g:
                keys = {n["key"] for n in g.around(seeds)}
            return [current.nodes[k] for k in sorted(keys) if k in current.nodes]
        except graph.Unavailable:
            pass
    keys = set(seeds)
    for e in current.edges.values():
        if e["source"] in seeds or e["target"] in seeds:
            keys |= {e["source"], e["target"]}
    return [current.nodes[k] for k in sorted(keys)]

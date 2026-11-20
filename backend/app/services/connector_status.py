"""What the Connectors screen and the canvas show about each connection (CN.5).

A check stores one probe per permission; a discovery stores what the connection found. The
contract status (permissions, last read, items, lag, last error) and each project's view of
the discoveries are built from those, so the screen and the canvas always agree.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..connectors import routing
from ..connectors.aws_resources import ACCOUNT_TYPE, REGION_TYPE
from ..connectors.base import ConnectionTest, Discovery, SourceError, health
from ..connectors.scope import split
from ..db import utcnow
from ..models import Connector

# Discovered boundaries drawn as containers on the canvas, by clone group kind.
GROUP_KINDS = {ACCOUNT_TYPE: "account", REGION_TYPE: "region", "boundary.vpc@1": "vpc"}
NESTING = "contained_in"
# Kept in Connector.state by this module; the screen shows them through contract(), not as raw state.
OWN_STATE = ("probes", "identity", "checked_at", "sign_in_error", "discovery")


def record_check(c: Connector, result: ConnectionTest | None, error: str = "") -> None:
    """Keep each permission's probe from a check. A check that could not sign in leaves every permission unknown."""
    probes = [{"permission": p.permission, "ok": p.ok, "detail": p.detail} for p in result.probes] if result else []
    c.state = {**(c.state or {}), "probes": probes, "identity": result.identity if result else "",
               "checked_at": utcnow().isoformat(), "sign_in_error": error}


def missing_permissions(c: Connector) -> list[str]:
    return [p["permission"] for p in (c.state or {}).get("probes") or [] if not p.get("ok")]


def contract(c: Connector, now: datetime) -> dict | None:
    """The connector contract on screen: each permission ok, missing or not checked, and the health of reading."""
    kind = connectors.REGISTRY.get(c.key)
    if kind is None:
        return None
    state = c.state or {}
    probes = {p["permission"]: p for p in state.get("probes") or []}
    permissions = []
    for permission in kind.permissions:
        probe = probes.get(permission.name)
        permissions.append({"name": permission.name, "why": permission.why,
                            "ok": probe["ok"] if probe else None, "detail": probe["detail"] if probe else ""})
    shape = health(c.last_sync_at, state, now)
    found = state.get("discovery") or {}
    return {
        "identity": state.get("identity", ""), "checked_at": state.get("checked_at"),
        "sign_in_error": state.get("sign_in_error", ""), "permissions": permissions,
        "last_read_at": c.last_sync_at.isoformat() if c.last_sync_at else None, "items_read": shape.items_read,
        "lag_seconds": shape.lag_seconds, "last_error": shape.last_error,
        "discovered_at": found.get("at"), "discovered": len(found.get("nodes") or []),
    }


def discover(db: Session, c: Connector) -> int:
    """Check the connection, then list what it can see, and keep both. Returns how many nodes were found.

    The check comes first so the canvas marks nodes whose permission has gone, even when the listing still works.
    """
    source = connectors.for_row(db, c)
    if source is None:
        raise SourceError(f"{c.name} cannot list what it sees yet.")
    try:
        result = source.test()
    except SourceError as exc:
        record_check(c, None, str(exc))
        db.commit()
        raise
    record_check(c, result)
    try:
        found = source.discover()
    except SourceError:
        db.commit()  # the check is still worth keeping
        raise
    c.state = {**c.state, "discovery": {"at": utcnow().isoformat(), "nodes": found.nodes, "edges": found.edges}}
    db.commit()
    return len(found.nodes)


def project_view(db: Session, org_id: int, project_id: int, only: str | None = None) -> dict:
    """This project's share of every connection's discovery, as a read-only clone document plus marks.

    Marks say, in words, why a node may be out of date: a permission it needs is missing, or the
    connection's last read failed. Generic nodes are shown here but are never saved into a clone
    (the clone save refuses Generic nodes; PG.3 decides how imported Generic nodes are stored).
    `only` names one connector's key: the view of what that connection alone found (STUDIO.4).
    """
    rows = db.scalars(select(Connector).where(Connector.org_id == org_id,
                                              Connector.key.in_(connectors.PROTOTYPE_CONNECTORS))).all()
    if only is not None:
        rows = [c for c in rows if c.key == only]
    nodes, edges, marks, sources = [], [], {}, []
    for c in rows:
        found = (c.state or {}).get("discovery")
        if not found:
            continue
        mine = split(Discovery(found["nodes"], found["edges"]), routing.attachments(db, c)).get(project_id)
        if mine is None or not mine.nodes:
            continue
        problems = _problems(c)
        at = _when(found["at"])
        for node in mine.nodes:
            nodes.append({**node, "last_confirmed_at": at})
            texts = _node_marks(node["type"], problems)
            if texts:
                marks[node["id"]] = texts
        edges.extend(mine.edges)
        sources.append({"id": c.id, "name": c.name, "discovered_at": at, "nodes": len(mine.nodes),
                        "missing": [name for name, _covers in problems["missing"]],
                        "last_error": problems["last_error"]})
    return {"sources": sources, "marks": marks, "document": as_canvas(nodes, edges)}


def _when(stored: str) -> str:
    """A stored time as the clone document writes it (whole seconds, Z), so the node can be saved into a clone."""
    return datetime.fromisoformat(stored).strftime("%Y-%m-%dT%H:%M:%SZ")


def _problems(c: Connector) -> dict:
    kind = connectors.REGISTRY[c.key]
    covers = {p.name: p.covers for p in kind.permissions}
    missing = [(name, covers.get(name, ("*",))) for name in missing_permissions(c)]
    state = c.state or {}
    return {"name": c.name, "missing": missing,
            "last_error": str(state.get("last_error") or state.get("sign_in_error") or "")}


def _node_marks(type_ref: str, problems: dict) -> list[str]:
    type_id = type_ref.split("@")[0]
    texts = [f"{problems['name']} cannot read this any more: {name} is missing."
             for name, covers in problems["missing"] if "*" in covers or type_id in covers]
    if problems["last_error"]:
        texts.append(f"{problems['name']} last read failed: {problems['last_error']}")
    return texts


def as_canvas(nodes: list[dict], edges: list[dict]) -> dict:
    """Accounts, regions and VPCs become containers; everything else sits in the container it was found in."""
    groups = [{"id": n["id"], "type": n["type"], "name": n["name"], "kind": GROUP_KINDS[n["type"]],
               "environment": n["environment"], "provenance": n["provenance"], "properties": n["properties"]}
              for n in nodes if n["type"] in GROUP_KINDS]
    group_ids = {g["id"] for g in groups}
    parent = {e["from"]: e["to"] for e in edges if e["type"] == NESTING and e["to"] in group_ids}
    for group in groups:
        if group["id"] in parent:
            group["parent"] = parent[group["id"]]
    placed = []
    for node in nodes:
        if node["type"] in GROUP_KINDS:
            continue
        item = dict(node)
        if node["id"] in parent:
            item["location"] = parent[node["id"]]
        placed.append(item)
    drawn = [e for e in edges if not (e["type"] == NESTING and e["to"] in group_ids)]
    return {"schema_version": 1, "clone": "found-by-connectors", "environment": "shared",
            "version": {"number": 0, "author": "Connectors", "reason": "What the connections found",
                        "created_at": utcnow().isoformat()},
            "nodes": placed, "edges": drawn, "groups": groups, "access_paths": [], "credential_refs": []}

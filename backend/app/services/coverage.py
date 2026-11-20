"""How much of the map each WhiteHat actually sees (COV.1).

Coverage is a measurement, never a stamp: of the things on the project's map that a WhiteHat is meant to watch, the
share its data really reaches now. A WhiteHat with nothing on the map to watch has no coverage (None), not 0% or 100%.

- Cloud Posture: everything on the map that came from AWS (it carries an ARN) is seen while an AWS connection reads the
  account's activity: the activity log and the hourly settings check cover the whole account.
- Network: each server and each network (VPC). A server is seen when a network collector runs on it; a network when its
  traffic log is being read.
- Endpoint: each server and each staff or customer device, seen when a process or log collector runs on it.
- Identity: each AWS account and each sign-in provider on the map. An AWS account is seen while an AWS connection reads
  its activity (console sign-ins, root use); a sign-in provider when its connection syncs.

A collector is matched to a server by the host name it reports: the server's name, its instance id, or its private name
or address.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Clone, CloneVersion, Collector, Connector, Node
from . import deploy
from .cloudlogs import EVERY_S

SERVERS = {"compute.vm"}
NETWORKS = {"boundary.vpc"}
DEVICES = {"endpoint.laptop", "endpoint.staff", "endpoint.customer"}
SIGN_IN = {"identity.okta": "okta", "identity.entra": "entra", "identity.entra-external": "entra",
           "connection.okta": "okta", "connection.entra": "entra", "connection.google": "google"}
DOMAIN = {"cloud": None, "network": SERVERS | NETWORKS, "endpoint": SERVERS | DEVICES,
          "identity": {"boundary.aws-account", *SIGN_IN}}
SHOWN_DARK = 8


def _kind(node: Node) -> str:
    return str(node.type).split("@", 1)[0]


def _arn(node: Node) -> str:
    return str((node.properties or {}).get("arn") or "")


def _names(node: Node) -> set[str]:
    props = node.properties or {}
    names = {node.name, _arn(node).rsplit("/", 1)[-1], props.get("instance_id"), props.get("private_dns_name"),
             props.get("private_ip"), props.get("host"), props.get("vpc_id")}
    return {str(n).lower() for n in names if n}


def map_nodes(db: Session, project_id: int) -> list[Node]:
    """The things on the project's map: the newest version of each of its clones."""
    out = []
    for clone in db.scalars(select(Clone).where(Clone.project_id == project_id)):
        newest = db.scalar(select(CloneVersion.id).where(CloneVersion.clone_id == clone.id)
                           .order_by(CloneVersion.number.desc()).limit(1))
        if newest is not None:
            out += list(db.scalars(select(Node).where(Node.clone_version_id == newest)))
    return out


def _recent(c: Connector, now) -> bool:
    every = 3600 if c.key in ("okta", "entra", "google") else EVERY_S.get(c.key, 300)  # sign-in providers: hourly
    return c.last_sync_at is not None and (now - c.last_sync_at).total_seconds() <= every * 3 + 60


def _sources(db: Session, org_id: int) -> dict:
    from ..db import utcnow

    now = utcnow()
    online = [c for c in db.scalars(select(Collector).where(Collector.org_id == org_id))
              if deploy.health(c) == "online"]
    connectors = [c for c in db.scalars(select(Connector).where(Connector.org_id == org_id)) if _recent(c, now)]
    flow = set()
    for c in connectors:
        if c.key == "aws":
            flow |= {str(f.get("vpc")).lower() for f in (c.state or {}).get("flow_logs") or []
                     if f.get("destination") == "logs"}
    return {"aws": any(c.key == "aws" for c in connectors),
            "network_hosts": {c.host.lower() for c in online if c.kind == "network"},
            "device_hosts": {c.host.lower() for c in online if c.kind in ("process", "log") and c.mode != "cloud"},
            "sign_in": {c.key for c in connectors if c.key in ("okta", "entra", "google")},
            "flow_vpcs": flow}


def _seen(key: str, node: Node, src: dict) -> bool:
    kind = _kind(node)
    if key == "cloud":
        return src["aws"]
    if key == "network":
        if kind in NETWORKS:
            return bool(_names(node) & src["flow_vpcs"])
        return bool(_names(node) & src["network_hosts"])
    if key == "endpoint":
        return bool(_names(node) & src["device_hosts"])
    if key == "identity":
        return src["aws"] if kind == "boundary.aws-account" else SIGN_IN.get(kind) in src["sign_in"]
    return False


def _in_domain(key: str, node: Node) -> bool:
    if key == "cloud":
        return _arn(node).startswith("arn:aws:")
    return _kind(node) in (DOMAIN.get(key) or ())


def measure(db: Session, org_id: int, project_id: int, keys: list[str]) -> dict[str, dict]:
    """{WhiteHat key: {"percent": 0-100 or None, "seen": n, "total": n, "dark": [names of a few unseen things]}}."""
    nodes = map_nodes(db, project_id)
    src = _sources(db, org_id)
    out = {}
    for key in keys:
        if key not in DOMAIN:
            out[key] = {"percent": None, "seen": 0, "total": 0, "dark": []}
            continue
        mine = [n for n in nodes if _in_domain(key, n)]
        seen = [n for n in mine if _seen(key, n, src)]
        dark = [n.name for n in mine if n not in seen]
        out[key] = {"percent": round(100 * len(seen) / len(mine)) if mine else None, "seen": len(seen),
                    "total": len(mine), "dark": dark[:SHOWN_DARK]}
    return out

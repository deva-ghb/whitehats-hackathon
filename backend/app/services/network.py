"""Network analyzer: findings from real connection snapshots sent by network collectors."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Collector, NetworkFinding, NetworkFlow
from ..projects import main_project_id
from ..whitehats.signals import NETWORK_RISKY_PORTS as RISKY_PORTS

COMMON_OUTBOUND = {53, 80, 123, 443, 993, 587, 465, 22, 5222, 5223, 8443, 3478, 19302}
ALL_INTERFACES = ("0.0.0.0", "::", "*", "")
EPHEMERAL = 32768  # the usual start of the short-lived ports a system picks for its side of a connection


def _is_private(addr: str) -> bool:
    if addr.startswith(("10.", "192.168.", "127.", "169.254.", "::1", "fe80", "fc", "fd")):
        return True
    if addr.startswith("172."):
        parts = addr.split(".")
        return len(parts) > 1 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31
    return False


def ingest_snapshot(db: Session, collector: Collector, conns: list[dict]) -> int:
    """Replace this collector's latest snapshot and re-run the findings."""
    now = utcnow()
    db.execute(delete(NetworkFlow).where(NetworkFlow.collector_id == collector.id))
    rows = []
    for c in conns[:5000]:
        rows.append(NetworkFlow(
            org_id=collector.org_id, collector_id=collector.id, observed_at=now,
            proto=str(c.get("proto", "tcp"))[:8], local_addr=str(c.get("laddr", ""))[:64],
            local_port=int(c.get("lport") or 0), remote_addr=str(c.get("raddr", ""))[:64],
            remote_port=int(c.get("rport") or 0), state=str(c.get("state", ""))[:20],
            process=str(c.get("process", ""))[:120], pid=int(c.get("pid") or 0),
        ))
    db.add_all(rows)
    collector.events_total += len(rows)
    new = analyze(db, collector, rows)
    db.flush()
    from ..whitehats import dispatch

    dispatch(db, collector.org_id, "on_network_findings", host=collector.host, new_findings=new)
    return len(rows)


def _upsert(db: Session, collector: Collector, key: str, kind: str, severity: str, title: str, detail: str,
            new: list) -> NetworkFinding:
    f = db.scalar(select(NetworkFinding).where(NetworkFinding.org_id == collector.org_id, NetworkFinding.key == key))
    now = utcnow()
    if f is None:
        f = NetworkFinding(org_id=collector.org_id, project_id=main_project_id(db, collector.org_id), key=key,
                           kind=kind, severity=severity, title=title, detail=detail,
                           host=collector.host, first_seen=now, last_seen=now)
        db.add(f)
        new.append(f)
    else:
        f.last_seen, f.detail = now, detail
        if f.status == "resolved":
            f.status = "open"
            new.append(f)
    return f


def analyze(db: Session, collector: Collector, flows: list[NetworkFlow]) -> list[NetworkFinding]:
    seen_keys, new = set(), []
    listeners = [f for f in flows if f.state.upper() == "LISTEN"]
    # One finding a port: the same service listening for both kinds of address (IPv4 and IPv6) is one exposure.
    by_port: dict[int, NetworkFlow] = {}
    for f in listeners:
        if f.local_addr in ALL_INTERFACES or f.local_port not in by_port:
            by_port[f.local_port] = f
    for f in by_port.values():
        exposed = f.local_addr in ALL_INTERFACES
        if f.local_port in RISKY_PORTS and exposed:
            svc = RISKY_PORTS[f.local_port]
            key = f"{collector.host}:listen:{f.local_port}"
            seen_keys.add(key)
            _upsert(db, collector, key, "exposed_service", "high",
                    f"{svc} on port {f.local_port} accepts connections from any network",
                    f"{f.process or 'A process'} (pid {f.pid}) listens on {f.local_addr or '*'}:{f.local_port} on "
                    f"{collector.host}, on every network interface. Unless a firewall in front of the server blocks "
                    "it, anyone who can reach the server can try it. Bind it to localhost or a private interface, or "
                    "allow only known addresses in the firewall.",
                    new)
        elif exposed and f.local_port < 49152:
            key = f"{collector.host}:listen:{f.local_port}"
            seen_keys.add(key)
            _upsert(db, collector, key, "open_listener", "low",
                    f"Port {f.local_port} open on all interfaces ({f.process or 'unknown process'})",
                    f"{f.process or 'A process'} listens on every interface of {collector.host}. Confirm it is meant "
                    "to be reachable from other machines.", new)

    # A connection to one of this server's own listening ports was made to it, not by it: its far end is a visitor's
    # short-lived port, not a service it chose to call. And a far port in the short-lived range is a reply, not a call.
    serving = {f.local_port for f in listeners}
    outbound = [f for f in flows if f.remote_addr and f.state.upper() == "ESTABLISHED" and f.local_port not in serving]
    odd = Counter()
    for f in outbound:
        if f.remote_port and f.remote_port not in COMMON_OUTBOUND and f.remote_port < EPHEMERAL \
                and not _is_private(f.remote_addr):
            odd[(f.process or "unknown", f.remote_port)] += 1
    for (proc, port), n in odd.most_common(10):
        key = f"{collector.host}:egress:{proc}:{port}"
        seen_keys.add(key)
        _upsert(db, collector, key, "unusual_egress", "medium" if port in RISKY_PORTS else "low",
                f"{proc} talks to the internet on unusual port {port}",
                f"{n} live connection(s) from {proc} on {collector.host} to public addresses on port {port}. "
                "Check that this traffic is expected.", new)

    # Findings for this host that no longer appear are resolved automatically.
    # A second open copy of one finding (an earlier snapshot could write the same one twice) is resolved as well.
    db.flush()
    kept: set[str] = set()
    for f in db.scalars(select(NetworkFinding).where(NetworkFinding.org_id == collector.org_id,
                                                     NetworkFinding.host == collector.host,
                                                     NetworkFinding.status == "open").order_by(NetworkFinding.id)):
        if f.key not in seen_keys or f.key in kept:
            f.status = "resolved"
        kept.add(f.key)
    return new


def summary(db: Session, org_id: int) -> dict:
    flows = db.scalars(select(NetworkFlow).where(NetworkFlow.org_id == org_id)).all()
    listen = [f for f in flows if f.state.upper() == "LISTEN"]
    est = [f for f in flows if f.state.upper() == "ESTABLISHED"]
    talkers = Counter(f.process or "unknown" for f in est).most_common(8)
    dests = Counter(f.remote_addr for f in est if f.remote_addr and not _is_private(f.remote_addr)).most_common(8)
    return {
        "connections": len(flows), "listening": len(listen), "established": len(est),
        "exposed": sum(1 for f in listen if f.local_addr in ALL_INTERFACES),
        "public_destinations": len({f.remote_addr for f in est if f.remote_addr and not _is_private(f.remote_addr)}),
        "top_processes": [{"process": p, "connections": n} for p, n in talkers],
        "top_destinations": [{"address": a, "connections": n} for a, n in dests],
    }

"""The network analyzer judges a server's snapshot: an exposed service once per port, and outgoing calls on odd ports,
never the replies to its own visitors."""

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Collector, Incident, IncidentEvent, NetworkFinding, NetworkFlow, Signal


def _conn(laddr, lport, raddr="", rport=0, state="ESTABLISHED", process="nginx"):
    return {"proto": "tcp", "laddr": laddr, "lport": lport, "raddr": raddr, "rport": rport, "state": state,
            "pid": 1, "process": process}


def test_a_server_snapshot_raises_each_exposure_once_and_only_real_outgoing_calls(client, ceo):
    try:
        _snapshot_and_judge(client, ceo)
    finally:  # the sample company keeps only sample rows (test_sample_origin)
        with SessionLocal() as db:
            for model in (NetworkFinding, NetworkFlow):
                db.query(model).filter(model.org_id == db.scalar(select(Collector.org_id).where(
                    Collector.name == "web snapshot"))).filter(
                    (model.host == "web-snapshot-1") if model is NetworkFinding else
                    model.collector_id.in_(select(Collector.id).where(Collector.name == "web snapshot"))).delete()
            for signal in db.scalars(select(Signal).where(Signal.entity == "web-snapshot-1")):
                if signal.incident_id:
                    db.execute(delete(IncidentEvent).where(IncidentEvent.incident_id == signal.incident_id))
                    incident = db.get(Incident, signal.incident_id)
                    signal.incident_id = None
                    db.flush()
                    db.delete(incident)
                db.delete(signal)
            db.query(Collector).filter(Collector.name == "web snapshot").delete()
            db.commit()


def _snapshot_and_judge(client, ceo):
    made = client.post("/api/collectors", headers=ceo, json={"name": "web snapshot", "kind": "network",
                                                              "mode": "docker"}).json()
    token = {"Authorization": f"Bearer {made['token']}"}
    snapshot = [
        _conn("0.0.0.0", 22, state="LISTEN", process="sshd"), _conn("::", 22, state="LISTEN", process="sshd"),
        _conn("0.0.0.0", 80, state="LISTEN"), _conn("127.0.0.1", 8000, state="LISTEN", process="uvicorn"),
        # visitors of the website: their side is a short-lived port, not something this server called
        _conn("172.31.25.177", 80, "13.54.10.20", 14426), _conn("172.31.25.177", 80, "13.54.10.21", 48318),
        # this server calling out: HTTPS is normal, port 4444 is not
        _conn("172.31.25.177", 51000, "52.95.1.1", 443, process="python3"),
        _conn("172.31.25.177", 51001, "198.51.100.7", 4444, process="python3"),
    ]
    assert client.post("/api/ingest/network", headers=token, json={"host": "web-snapshot-1",
                                                                   "connections": snapshot}).status_code == 200
    with SessionLocal() as db:
        found = sorted((f.severity, f.title) for f in db.scalars(select(NetworkFinding).where(
            NetworkFinding.host == "web-snapshot-1", NetworkFinding.status == "open")))
    assert found == [("high", "SSH on port 22 accepts connections from any network"),
                     ("low", "Port 80 open on all interfaces (nginx)"),
                     ("low", "python3 talks to the internet on unusual port 4444")]
    # A copy of one finding left open by an earlier snapshot is resolved by the next one.
    with SessionLocal() as db:
        first = db.scalar(select(NetworkFinding).where(NetworkFinding.host == "web-snapshot-1",
                                                       NetworkFinding.title.like("SSH%")))
        db.add(NetworkFinding(org_id=first.org_id, project_id=first.project_id, key=first.key, kind=first.kind,
                              severity=first.severity, title=first.title, detail=first.detail, host=first.host,
                              status="open"))
        db.commit()
    client.post("/api/ingest/network", headers=token, json={"host": "web-snapshot-1", "connections": snapshot})
    with SessionLocal() as db:
        ssh = list(db.scalars(select(NetworkFinding).where(NetworkFinding.host == "web-snapshot-1",
                                                           NetworkFinding.title.like("SSH%"),
                                                           NetworkFinding.status == "open")))
    assert len(ssh) == 1

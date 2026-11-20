"""Collectors (deploy, run, stop), the ingest endpoints they call, and the log and network analyzers."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Collector, LogDigest, LogEvent, LogTemplate, NetworkFinding, NetworkFlow, ProcessSample
from ..permissions import FromObject, allow, can
from ..security import Principal, audit, new_collector_token, token_digest
from ..services import deploy, loganalyzer, network
from ..services.tenancy import mark_milestone
from ..sphere import learn as sphere_learn
from ..whitehats import dispatch

router = APIRouter(prefix="/api")
_FINDING = FromObject(NetworkFinding, "fid")
# Collectors belong to the account, and what they send is not tied to one project yet (AP.3 routes it).
# Raw lines, flows and samples are therefore read with read_account_data, which only the roles that see
# every project hold. The collector list itself is shared setup, like the connections list.
RAW = "read_account_data"
RAW_HIDDEN = ("Raw network connections come from the account's collectors and are not tied to one project yet. "
              "Account owners, Account admins, Security leads and Auditors can read them.")


def col_out(c: Collector) -> dict:
    return {"id": c.id, "name": c.name, "kind": c.kind, "kind_label": deploy.KIND_LABEL[c.kind], "mode": c.mode,
            "host": c.host, "health": deploy.health(c), "status": c.status, "config": c.config,
            "interval_s": c.interval_s, "context_budget": c.context_budget, "version": c.version, "pid": c.pid,
            "events_total": c.events_total, "bytes_total": c.bytes_total,
            "last_heartbeat": c.last_heartbeat.isoformat() if c.last_heartbeat else None,
            "created_at": c.created_at.isoformat(),
            "log_file": f"backend/.data/logs/collector-{c.id}.log" if c.mode == "local" else None}


def _col(db: Session, user: Principal, cid: int) -> Collector:
    c = db.get(Collector, cid)
    if c is None or c.org_id != user.org_id:
        raise HTTPException(404, "Collector not found.")
    return c


@router.get("/collectors")
def collectors(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Collector).where(Collector.org_id == user.org_id).order_by(Collector.id)).all()
    for c in rows:  # a local agent whose process died is shown honestly
        if c.mode == "local" and c.status == "running" and not deploy.is_alive(c.pid):
            c.status, c.pid = "crashed", None
    db.commit()
    return [col_out(c) for c in rows]


class NewCollector(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["log", "network", "process"]
    mode: Literal["local", "docker", "kubernetes", "systemd"]
    interval_s: int = Field(default=15, ge=5, le=3600)
    context_budget: int = Field(default=600, ge=100, le=8000)
    files: list[str] | None = None


@router.get("/agent/collector.py", include_in_schema=False)
def collector_program():
    """The collector program, for a server installed without the image (the Linux service file says how). Public: it
    holds no secret, and its fingerprint is in the X-SHA256 header and in the setup file, to check what was fetched."""
    return Response(deploy.AGENT_SCRIPT.read_bytes(), media_type="text/x-python",
                    headers={"X-SHA256": deploy.agent_sha256(), "Cache-Control": "no-cache"})


@router.post("/collectors")
def create_collector(body: NewCollector, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    token, digest = new_collector_token()
    cfg = deploy.default_config(body.kind)
    if body.kind == "log" and body.files:
        cfg["files"] = body.files
    c = Collector(org_id=user.org_id, name=body.name, kind=body.kind, mode=body.mode, token_hash=digest, config=cfg,
                  interval_s=body.interval_s, context_budget=body.context_budget,
                  host="this server" if body.mode == "local" else "")
    db.add(c)
    db.flush()
    if body.mode == "local":
        deploy.start_local(c, token)
    audit(db, user, "deployed collector", body.name, f"{body.kind} via {body.mode}")
    db.commit()
    # The token is shown once, then only its hash is kept.
    return {"collector": col_out(c), "token": token, "manifests": deploy.manifests(c, token)}


@router.get("/collectors/{cid}")
def collector(cid: int, user: Principal = Depends(allow(RAW)), db: Session = Depends(get_db)):
    c = _col(db, user, cid)
    out = col_out(c)
    if c.kind == "log":
        out["recent"] = [{"ts": e.ts.isoformat(), "source": e.source, "message": e.message[:300]}
                         for e in db.scalars(select(LogEvent).where(LogEvent.collector_id == c.id)
                                             .order_by(LogEvent.id.desc()).limit(40))]
    elif c.kind == "network":
        out["recent"] = [{"proto": f.proto, "local": f"{f.local_addr}:{f.local_port}",
                          "remote": f"{f.remote_addr}:{f.remote_port}" if f.remote_addr else "", "state": f.state,
                          "process": f.process} for f in db.scalars(select(NetworkFlow)
                                                                   .where(NetworkFlow.collector_id == c.id).limit(60))]
    else:
        out["recent"] = [{"name": p.name, "pid": p.pid, "user": p.user, "cpu": p.cpu, "memory_mb": p.memory_mb}
                         for p in db.scalars(select(ProcessSample).where(ProcessSample.collector_id == c.id)
                                             .order_by(ProcessSample.cpu.desc()).limit(25))]
    out["manifests"] = deploy.manifests(c)
    return out


@router.post("/collectors/{cid}/{action}")
def collector_action(cid: int, action: str, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    c = _col(db, user, cid)
    if action == "stop":
        if c.mode == "local":
            deploy.stop_local(c)
        else:
            c.status = "stopped"
        audit(db, user, "stopped collector", c.name)
        db.commit()
        return {"collector": col_out(c)}
    if action in ("start", "rotate"):
        token, digest = new_collector_token()
        c.token_hash = digest
        if c.mode == "local":
            deploy.stop_local(c)
            deploy.start_local(c, token)
        else:
            c.status = "pending"
        audit(db, user, "rotated collector token" if action == "rotate" else "started collector", c.name)
        db.commit()
        return {"collector": col_out(c), "token": token, "manifests": deploy.manifests(c, token)}
    raise HTTPException(404, "Unknown action.")


@router.delete("/collectors/{cid}")
def delete_collector(cid: int, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    c = _col(db, user, cid)
    if c.mode == "local":
        deploy.stop_local(c)
    for model in (LogEvent, NetworkFlow, ProcessSample):
        db.query(model).filter(model.collector_id == c.id).delete()
    db.delete(c)
    audit(db, user, "removed collector", c.name)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ ingest (called by collectors)


def collector_from_token(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Collector:
    token = authorization.removeprefix("Bearer ").strip()
    c = db.scalar(select(Collector).where(Collector.token_hash == token_digest(token))) if token else None
    if c is None:
        raise HTTPException(401, "Unknown collector token.")
    if c.status == "stopped":
        raise HTTPException(403, "This collector was stopped from the console.")
    return c


def _beat(c: Collector, host: str, version: str, size: int) -> None:
    c.last_heartbeat, c.version, c.bytes_total = utcnow(), version or c.version, c.bytes_total + size
    if host and (not c.host or c.host == "this server"):
        c.host = host
    if c.status == "pending":
        c.status = "running"


class LogBatch(BaseModel):
    host: str = ""
    version: str = ""
    lines: list[dict] = Field(default_factory=list, max_length=5000)


@router.post("/ingest/logs")
def ingest_logs(body: LogBatch, c: Collector = Depends(collector_from_token), db: Session = Depends(get_db)):
    if c.kind != "log":
        raise HTTPException(400, "This collector is not a log collector.")
    n = loganalyzer.ingest_lines(db, c, body.lines)
    _beat(c, body.host, body.version, sum(len(str(x.get("message", ""))) for x in body.lines))
    if n:
        dispatch(db, c.org_id, "on_log_batch", source_host=c.host or body.host, lines=body.lines)
        mark_milestone(db, c.org_id, "first_data")
    db.commit()
    return {"accepted": n}


class NetBatch(BaseModel):
    host: str = ""
    version: str = ""
    connections: list[dict] = Field(default_factory=list, max_length=20000)


@router.post("/ingest/network")
def ingest_network(body: NetBatch, c: Collector = Depends(collector_from_token), db: Session = Depends(get_db)):
    if c.kind != "network":
        raise HTTPException(400, "This collector is not a network analyzer.")
    _beat(c, body.host, body.version, len(body.connections) * 80)
    n = network.ingest_snapshot(db, c, body.connections)
    mark_milestone(db, c.org_id, "first_data")
    mark_milestone(db, c.org_id, "first_network")
    db.commit()
    return {"accepted": n}


class ProcBatch(BaseModel):
    host: str = ""
    version: str = ""
    processes: list[dict] = Field(default_factory=list, max_length=500)


@router.post("/ingest/processes")
def ingest_processes(body: ProcBatch, c: Collector = Depends(collector_from_token), db: Session = Depends(get_db)):
    if c.kind != "process":
        raise HTTPException(400, "This collector is not a process monitor.")
    _beat(c, body.host, body.version, len(body.processes) * 60)
    db.query(ProcessSample).filter(ProcessSample.collector_id == c.id).delete()
    db.add_all([ProcessSample(org_id=c.org_id, collector_id=c.id, name=str(p.get("name", ""))[:120],
                              pid=int(p.get("pid", 0)), user=str(p.get("user", ""))[:80],
                              cpu=float(p.get("cpu", 0)), memory_mb=float(p.get("memory_mb", 0)))
                for p in body.processes])
    c.events_total += len(body.processes)
    dispatch(db, c.org_id, "on_processes", host=c.host or body.host, processes=body.processes)
    mark_milestone(db, c.org_id, "first_data")
    db.commit()
    return {"accepted": len(body.processes)}


# ------------------------------------------------------------------ log analyzer


@router.get("/logs/overview")
def logs_overview(minutes: int = 60, user: Principal = Depends(allow(RAW)), db: Session = Depends(get_db)):
    items = loganalyzer.score_window(db, user.org_id, minutes)
    total_templates = db.scalar(select(func.count(LogTemplate.id)).where(LogTemplate.org_id == user.org_id)) or 0
    total_lines = db.scalar(select(func.sum(LogTemplate.count)).where(LogTemplate.org_id == user.org_id)) or 0
    latest = db.scalar(select(LogDigest).where(LogDigest.org_id == user.org_id).order_by(LogDigest.created_at.desc()))
    return {
        "window_minutes": minutes, "lines_in_window": sum(i["count"] for i in items), "shapes_in_window": len(items),
        "notable": [i for i in items if i["notable"]][:40], "routine": [i for i in items if not i["notable"]][:20],
        "all_time": {"lines": int(total_lines), "templates": total_templates},
        "latest_digest": digest_out(latest) if latest else None,
    }


def digest_out(d: LogDigest) -> dict:
    return {"id": d.id, "created_at": d.created_at.isoformat(), "window_start": d.window_start.isoformat(),
            "window_end": d.window_end.isoformat(), "lines": d.lines, "raw_tokens": d.raw_tokens,
            "templates": d.templates, "notable": d.notable, "digest_tokens": d.digest_tokens,
            "budget_tokens": d.budget_tokens, "text": d.text, "ai_summary": d.ai_summary, "model_used": d.model_used,
            "items": d.items}


class DigestRequest(BaseModel):
    minutes: int = Field(default=60, ge=5, le=1440)
    budget: int = Field(default=1500, ge=300, le=12000)


@router.post("/logs/digest", dependencies=[Depends(allow(RAW))])
def make_digest(body: DigestRequest, user: Principal = Depends(allow("triage")), db: Session = Depends(get_db)):
    """Building a digest is triage, and it reads the account's raw logs, so it needs both."""
    d = loganalyzer.build_digest(db, user.org_id, body.minutes, body.budget)
    return digest_out(d)


@router.get("/logs/digests")
def digests(user: Principal = Depends(allow(RAW)), db: Session = Depends(get_db)):
    rows = db.scalars(select(LogDigest).where(LogDigest.org_id == user.org_id)
                      .order_by(LogDigest.created_at.desc()).limit(20)).all()
    return [digest_out(d) | {"text": None, "items": None} for d in rows]


@router.get("/logs/templates/{tid}")
def template_lines(tid: int, user: Principal = Depends(allow(RAW)), db: Session = Depends(get_db)):
    t = db.get(LogTemplate, tid)
    if t is None or t.org_id != user.org_id:
        raise HTTPException(404, "Shape not found.")
    lines = db.scalars(select(LogEvent).where(LogEvent.template_id == tid).order_by(LogEvent.id.desc()).limit(50)).all()
    return {"pattern": t.pattern, "count": t.count, "level": t.level, "security": t.security,
            "first_seen": t.first_seen.isoformat(), "lines": [{"ts": e.ts.isoformat(), "source": e.source,
                                                              "message": e.message} for e in lines]}


@router.get("/logs/search")
def search(q: str = Query(min_length=2), user: Principal = Depends(allow(RAW)), db: Session = Depends(get_db)):
    rows = db.scalars(select(LogEvent).where(LogEvent.org_id == user.org_id, LogEvent.message.contains(q))
                      .order_by(LogEvent.id.desc()).limit(100)).all()
    return [{"ts": e.ts.isoformat(), "source": e.source, "message": e.message[:500]} for e in rows]


# ------------------------------------------------------------------ network analyzer


@router.get("/network/overview")
def network_overview(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    findings = db.scalars(select(NetworkFinding).where(
        NetworkFinding.org_id == user.org_id, NetworkFinding.project_id == user.project.id,
    ).order_by(NetworkFinding.status, NetworkFinding.last_seen.desc())).all()
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings = sorted(findings, key=lambda f: (f.status != "open", order.get(f.severity, 9)))
    raw = can(db, user, RAW)
    flows = db.scalars(select(NetworkFlow).where(NetworkFlow.org_id == user.org_id)
                       .order_by(NetworkFlow.state, NetworkFlow.local_port).limit(400)).all() if raw else []
    return {
        "summary": network.summary(db, user.org_id) if raw else None,
        "raw_hidden": None if raw else RAW_HIDDEN,
        "findings": [{"id": f.id, "kind": f.kind, "severity": f.severity, "title": f.title, "detail": f.detail,
                      "host": f.host, "status": f.status, "first_seen": f.first_seen.isoformat(),
                      "last_seen": f.last_seen.isoformat()} for f in findings],
        "flows": [{"proto": f.proto, "local_addr": f.local_addr, "local_port": f.local_port,
                   "remote_addr": f.remote_addr, "remote_port": f.remote_port, "state": f.state,
                   "process": f.process, "pid": f.pid} for f in flows],
        "observed_at": max((f.observed_at for f in flows), default=None),
    }


class FindingPatch(BaseModel):
    status: Literal["open", "accepted", "resolved"]


@router.patch("/network/findings/{fid}")
def update_finding(fid: int, body: FindingPatch, user: Principal = Depends(allow("triage", _FINDING)),
                   db: Session = Depends(get_db)):
    f = db.get(NetworkFinding, fid)
    if f is None or f.org_id != user.org_id:
        raise HTTPException(404, "Finding not found.")
    before, f.status = f.status, body.status
    audit(db, user, f"marked network finding {body.status}", f.title)
    if body.status == "accepted" and before != "accepted" and sphere_learn.is_approver(db, user):  # KS.1
        sphere_learn.note(db, org_id=f.org_id, project_id=f.project_id, kind="network_accepted",
                          ref=f"network:{f.id}", actor=user.name, payload={"finding_id": f.id})
    elif before == "accepted" and body.status != "accepted":
        sphere_learn.note_reversal(db, org_id=f.org_id, project_id=f.project_id, taught=f"network:{f.id}",
                                   actor=user.name)
    db.commit()
    return {"ok": True}


@router.get("/telemetry/health")
def telemetry_health(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    hour = utcnow() - timedelta(hours=1)
    return {"log_lines_last_hour": db.scalar(select(func.count(LogEvent.id)).where(
        LogEvent.org_id == user.org_id, LogEvent.ts >= hour)) or 0}

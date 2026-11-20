"""FX.1: what a person reads before deciding a fix. Three parts, each said plainly when it is not known:
- the evidence: the finding behind the fix (where, which scanner, what the model said, the code lines as checked),
  or the incident's signals for a fix that answers an incident;
- the change: what Fix Factory proved and opened (its diff and pull requests), what it will do when it has not run
  yet, or the steps a person carries out;
- what it touches: the twin's services that hold the finding's code or carry the fix's target, and their neighbours.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..changes.findings import fingerprint
from ..connectors.base import SourceError
from ..connectors.github import GitHub
from ..models import ChangeEvent, Clone, Connector, Incident, Organization, Remediation, ScanPlanRecord, Signal
from ..scanning import planner
from . import factory, setup_checks
from .clones import read_clone

log = logging.getLogger("whitehats.fix_details")

CONTEXT_LINES = 4  # lines shown above and below the finding
MAX_LINES = 40
DIFF_LIMIT = 60_000  # characters of a run's diff the page shows


def details(db: Session, org: Organization, r: Remediation) -> dict:
    finding = db.get(Signal, r.signal_id) if r.signal_id else None
    if finding is not None and finding.org_id != r.org_id:
        finding = None
    return {"evidence": _evidence(db, r, finding), "change": _change(org, r), "touches": _touches(db, r, finding)}


# ------------------------------------------------------------------ evidence


def _evidence(db: Session, r: Remediation, finding: Signal | None) -> dict:
    if finding is not None and finding.source == setup_checks.SOURCE:
        return {"kind": "setting", "finding": {
            "id": finding.id, "title": finding.title, "severity": finding.severity, "detail": finding.detail,
            "status": "open" if finding.closed_at is None else "closed", "closed_reason": finding.closed_reason,
            "resource": finding.resource, "where": finding.entity, "rule": finding.fingerprint.split("|", 1)[0],
            "found_at": finding.created_at.isoformat()}}
    if finding is not None:
        return {"kind": "finding", "finding": _finding(db, finding)}
    if r.incident_id:
        incident = db.get(Incident, r.incident_id)
        signals = db.scalars(select(Signal).where(Signal.incident_id == r.incident_id)
                             .order_by(Signal.created_at)).all()
        return {"kind": "incident", "incident": {"id": incident.id, "ref": incident.ref, "title": incident.title}
                if incident else None,
                "signals": [{"title": s.title, "detail": s.detail, "where": s.entity, "severity": s.severity,
                             "source": s.agent.name if s.agent else s.source, "at": s.created_at.isoformat()}
                            for s in signals]}
    return {"kind": "none"}


def _finding(db: Session, s: Signal) -> dict:
    change = db.get(ChangeEvent, s.change_id) if s.change_id else None
    plan = db.get(ScanPlanRecord, s.scan_plan_id) if s.scan_plan_id else None
    path, _, line = s.entity.rpartition(":")
    start = int(line) if line.isdigit() else 0
    confirmation = _confirmation(change, s.fingerprint)
    suspect = (confirmation or {}).get("suspect") or {}
    evidence = suspect.get("evidence") or {}
    end = int(evidence.get("end_line") or start)
    repo = s.resource.removeprefix("https://github.com/")
    ref = change.head_sha if change else s.branch
    return {
        "id": s.id, "title": s.title, "severity": s.severity, "status": "open" if s.closed_at is None else "closed",
        "repo": repo, "branch": s.branch, "path": path or s.entity, "line": start, "end_line": end,
        "commit": change.head_sha[:7] if change else "", "check_url": change.check_url if change else "",
        "found_by": "a push" if plan is None or plan.kind == "push" else "the weekly scan",
        "engine": suspect.get("engine", ""), "rule": suspect.get("rule_id", s.fingerprint.split("|", 1)[0]),
        "cwe": suspect.get("cwe", []),
        "model": _model_words(confirmation),
        "explanation": _without_place(s.detail.split("\n", 1)[0], s.entity),
        "code": _code(db, s.org_id, repo, path, ref, start, end) if path and start else None,
    }


def _without_place(text: str, where: str) -> str:
    """The finding's words without the "path:line (CWE-..)." it starts with, since the page shows the place apart."""
    if not where or not text.startswith(where):
        return text
    rest = text[len(where):]
    if rest.startswith(" ("):
        rest = rest[rest.find(")") + 1:]
    return rest.removeprefix(".").strip() or text


def _confirmation(change: ChangeEvent | None, key: str) -> dict | None:
    for item in (change.confirmations or []) if change else []:
        if isinstance(item, dict) and item.get("suspect") and fingerprint(item["suspect"]) == key:
            return item
    return None


def _model_words(item: dict | None) -> dict:
    if item is None:
        return {"status": "not asked", "text": "The AI model was not asked about this finding."}
    status = str(item.get("status") or "")
    if item.get("error") or status not in ("confirmed", "rejected", "uncertain"):
        return {"status": "no answer", "text": "No AI model answered, so the scanner's word stands."}
    return {"status": status, "text": str(item.get("explanation") or ""), "model": str(item.get("model") or "")}


def _github(db: Session, org_id: int) -> GitHub | None:
    for row in db.scalars(select(Connector).where(Connector.org_id == org_id, Connector.key == GitHub.key,
                                                  Connector.status != "available")):
        source = connectors.for_row(db, row)
        if isinstance(source, GitHub):
            return source
    return None


def _code(db: Session, org_id: int, repo: str, path: str, ref: str, start: int, end: int) -> dict:
    """The lines around the finding as they were where it was found, or why they could not be read."""
    source = _github(db, org_id)
    if source is None:
        return {"lines": [], "problem": "No GitHub connection can read this repository."}
    try:
        data, reason = source.file_at(repo, path, ref)
    except SourceError as exc:
        log.info("fix details: %s %s@%s not read: %s", repo, path, ref, exc)
        return {"lines": [], "problem": "GitHub did not return the file just now."}
    if data is None:
        return {"lines": [], "problem": f"The file was not read: {reason}."}
    text = data.decode("utf-8", errors="replace").splitlines()
    first = max(1, start - CONTEXT_LINES)
    last = min(len(text), max(end, start) + CONTEXT_LINES, first + MAX_LINES - 1)
    return {"lines": [{"number": n, "text": text[n - 1], "flagged": start <= n <= max(end, start)}
                      for n in range(first, last + 1)], "problem": "", "ref": ref[:12]}


# ------------------------------------------------------------------ the change


def _change(org: Organization, r: Remediation) -> dict:
    if r.destination != "Fix Factory":
        steps = [line.strip() for line in (r.action or "").splitlines() if line.strip()]
        return {"kind": "manual", "destination": r.destination, "steps": steps}
    run = _run(org, r.factory_run_id) if r.factory_run_id else None
    if run is None:
        return {"kind": "planned", "summary": r.action,
                "ran": bool(r.factory_run_id), "run_id": r.factory_run_id}
    diff = str(run.get("diff") or "")
    return {
        "kind": "proven", "run_id": r.factory_run_id, "status": str(run.get("status") or ""),
        "summary": r.action,
        "verification": [item for item in run.get("verification") or [] if isinstance(item, dict)],
        "pull_requests": [{"number": (p.get("pull_request") or {}).get("number"),
                           "url": (p.get("pull_request") or {}).get("url"), "part": p.get("part") or p.get("name", "")}
                          for p in run.get("pieces") or [] if isinstance(p, dict) and p.get("pull_request")],
        "diff": diff[:DIFF_LIMIT], "diff_cut": len(diff) > DIFF_LIMIT,
    }


def _run(org: Organization, run_id: str) -> dict | None:
    if not factory.serves(org):
        return None
    try:
        detail = factory.read_for_screen(org, factory.run_path(run_id))
    except Exception:  # noqa: BLE001 - the engine being away leaves the page saying so, not failing
        return None
    return detail if isinstance(detail, dict) else None


# ------------------------------------------------------------------ what it touches


def _touches(db: Session, r: Remediation, finding: Signal | None) -> dict:
    """The twin's nodes the fix lands on, and every node one connection away from them."""
    graphs = _graphs(db, r.project_id)
    nodes = {str(node["id"]): node for graph in graphs for node in graph.get("nodes", [])}
    if not nodes:
        return {"on_twin": False, "where": [], "near": [], "why": "This project has no digital twin yet."}
    hit: dict[str, str] = {}
    if finding is not None and finding.node_key in nodes:
        hit[finding.node_key] = "the setting is on it"
    elif finding is not None:
        repo = finding.resource.removeprefix("https://github.com/")
        path = finding.entity.rpartition(":")[0]
        service = planner.owner(path, planner.services(nodes.values(), repo))
        if service is not None:
            hit[str(service["id"])] = f"its code holds {path}"
    for key, node in nodes.items():
        if r.target and r.target in (key, str(node.get("name") or "")):
            hit.setdefault(key, "the fix's target")
    if not hit:
        return {"on_twin": False, "where": [], "near": [],
                "why": f"Nothing on the twin is named {r.target} or holds this code."}
    near: dict[str, str] = {}
    for graph in graphs:
        for edge in graph.get("edges", []):
            ends = (str(edge.get("from")), str(edge.get("to")))
            for this, other in (ends, ends[::-1]):
                if this in hit and other not in hit and other in nodes:
                    near.setdefault(other, str(edge.get("type") or "connects to"))
    return {"on_twin": True, "where": [_node(nodes[key], why) for key, why in hit.items()],
            "near": [_node(nodes[key], why) for key, why in sorted(near.items())], "why": ""}


def _graphs(db: Session, project_id: int) -> list[dict]:
    """The latest version of each clone in the project, as nodes and edges."""
    return [doc for clone in db.scalars(select(Clone).where(Clone.project_id == project_id).order_by(Clone.id))
            if (doc := read_clone(db, clone.org_id, clone.id))]


def _node(node: dict, why: str) -> dict:
    return {"id": str(node["id"]), "name": str(node.get("name") or node["id"]), "type": str(node.get("type") or ""),
            "criticality": str(node.get("criticality") or ""), "data": node.get("data_classification") or "",
            "why": why}

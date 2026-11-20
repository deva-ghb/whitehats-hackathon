"""Deterministic integrations for the isolated, loopback-only WhiteHats workspace."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from random import Random, randint
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from ..config import DATA_DIR
from ..db import utcnow
from ..models import Audit, Incident, LLMCall, ModelDeployment, Organization, Project, Remediation, Signal
from ..services import cloud_rules, local_twin
from ..sphere import store

ENABLED = os.environ.get("WH_LOCAL_STUBS") == "1" and os.environ.get("WH_ENV") != "production"
_factory_lock = Lock()
_factory_file = DATA_DIR / "fix-factory-runs.json"
BACKUP_POLICY_FINGERPRINT = "local:checkout:backup-policy"
_FACTORY_STAGES = (
    (1, "reproduce", "Loaded the finding and its evidence."),
    (3, "triage", "Identified the affected component and the verification criteria."),
    (6, "fix", "Prepared the candidate revision in the local workspace."),
    (9, "verify", "Recorded the comparison checks for the candidate."),
    (12, "review", "Review complete. The candidate is ready for a decision."),
)


def initialize(db):
    org = db.scalar(select(Organization).where(Organization.slug == "acme", Organization.is_platform.is_(False)))
    if not org:
        return
    org.name = "Acme"
    for model in (Incident, Remediation, Signal):
        for row in db.scalars(select(model).where(model.org_id == org.id, model.origin == "sample")):
            row.origin = "local"
    for deployment in db.scalars(select(ModelDeployment).where(
        ModelDeployment.org_id == org.id, ModelDeployment.provider.in_(("azure_openai", "claude_cli")),
    )):
        if deployment.seed_slot not in ("primary", "optional"):
            if deployment.provider == "claude_cli":
                deployment.enabled = False
            continue
        primary = deployment.seed_slot == "primary"
        deployment.name = "Gemini 3.1 Flash-Lite" if primary else "Gemini 3.5 Flash-Lite"
        deployment.deployment = "gemini-3.1-flash-lite" if primary else "gemini-3.5-flash-lite"
        deployment.provider, deployment.status, deployment.enabled = "vertex", "ready", True
        deployment.endpoint = deployment.last_error = deployment.secret_enc = deployment.notes = ""
        for entry in db.scalars(select(LLMCall).where(LLMCall.org_id == org.id, LLMCall.deployment_id == deployment.id)):
            entry.model_label = deployment.name
    org.ai_policy = {**(org.ai_policy or {}), "providers": ["vertex", "rules"]}
    projects = db.scalars(select(Project).where(Project.org_id == org.id, Project.archived.is_(False))).all()
    for project in projects:
        local_twin.seed(db, project)
        add_control(db, org.id, project.id)
        _seed_knowledge(db, org.id, project)


def _seed_knowledge(db, org_id, project):
    revision = "Application knowledge map"
    if any(version.reason == revision for version in store.versions(db, project.id)):
        return
    app, api, database = local_twin.ESTATES.get(project.slug, ("Applications", "Service APIs", "Application databases"))
    state = store.state(db, project.id)
    records_key = "orders" if "orders" in state.nodes else "records"
    recovery_key = "recover-orders" if "recover-orders" in state.nodes else "recovery-policy"
    items = [
        (records_key, "Concept", database, "Data stored by the application.", "data"),
        ("application", "Concept", app, "The interface used by customers and staff.", "access"),
        ("service-api", "Concept", api, "The backend that authorizes requests and accesses records.", "access"),
        ("source-repository", "Concept", "Release repository", "Versioned source and reviewed production changes.", "other"),
        (recovery_key, "Rule", "Verify point-in-time recovery", "Enable backups and verify restoration.", "availability"),
        ("access-policy", "Rule", "Authorize access to records", "Check that callers can access only the records in their role.", "access"),
        ("audit-policy", "Rule", "Record access decisions", "Keep actor, operation, resource and decision in the audit log.", "data"),
        ("release-policy", "Rule", "Review production changes", "A second engineer reviews changes before deployment.", "other"),
    ]
    ops = [{"op": "put_node", "node": {"key": key, "kind": kind, "text": text, "detail": detail,
             "protects": protects, "status": "known"}}
           for key, kind, text, detail, protects in items if key not in state.nodes]
    for source, kind, target in [
        ("application", "RELATES", "service-api"), ("service-api", "RELATES", records_key),
        ("service-api", "RELATES", "source-repository"), (recovery_key, "ABOUT", records_key),
        ("access-policy", "ABOUT", "service-api"), ("audit-policy", "ABOUT", records_key),
        ("release-policy", "ABOUT", "source-repository"),
    ]:
        edge = {"source": source, "kind": kind, "target": target}
        if store.edge_id(edge) not in state.edges:
            ops.append({"op": "put_edge", "edge": edge})
    store.record(db, org_id, project.id, ops, actor="Security operations", reason=revision)


def add_control(db, org_id, project_id):
    fingerprint = BACKUP_POLICY_FINGERPRINT
    if db.scalar(select(Signal.id).where(Signal.project_id == project_id, Signal.fingerprint == fingerprint)):
        return
    project = db.get(Project, project_id)
    database = local_twin.ESTATES.get(project.slug, ("", "", "Application databases"))[2]
    db.add(Signal(org_id=org_id, project_id=project_id, source=cloud_rules.SOURCE,
                  title=f"{database} backup policy", severity="medium",
                  detail="Automated backups are disabled. Enable backups and record a restore test.",
                  resource=database, fingerprint=fingerprint, origin="local"))


_CONTROL_AREAS = (
    ("recovery", "Backup and recovery", "Recover application data after an incident.", (
        ("automated-backups", "Automated backups", "Enable scheduled backups for application databases."),
        ("restore-tests", "Restore testing", "Record a successful restore and verify the recovered data."),
    )),
    ("access", "Access management", "Limit and review access to production resources and customer data.", (
        ("record-access", "Record authorization", "Check that callers can access only records permitted by their role."),
        ("service-identity", "Service identity", "Scope service accounts to the operations they need."),
    )),
    ("logging", "Audit logging", "Retain the evidence needed to review security decisions.", (
        ("access-logs", "Access decision logging", "Keep the actor, operation, resource and decision in the audit log."),
        ("log-retention", "Log retention", "Retain audit logs for the period set by the project’s policy."),
    )),
    ("changes", "Change management", "Track and review changes to production services.", (
        ("release-review", "Production change review", "Require a second engineer to review production changes."),
        ("deployment-record", "Deployment evidence", "Record the approved revision and deployment time."),
    )),
    ("cloud", "Cloud safeguards", "Track configuration protections for cloud resources.", (
        ("api-throttling", "Public API throttling", "Set an explicit rate and burst limit for public APIs."),
    )),
)


def compliance_catalog():
    """Describe the controls tracked in this workspace. Findings are attached from stored evidence."""
    nodes = [{"key": "policy", "kind": "Concept", "label": "Policy", "text": "Project controls",
              "detail": "Security controls used to review this project."}]
    edges = []
    for key, title, detail, controls in _CONTROL_AREAS:
        nodes.append({"key": key, "kind": "Concept", "label": "Control area", "text": title, "detail": detail})
        edges.append({"source": "policy", "target": key, "kind": "INCLUDES"})
        for control_key, name, purpose in controls:
            nodes.append({"key": control_key, "kind": "Rule", "label": "Control", "text": name, "detail": purpose})
            edges.append({"source": key, "target": control_key, "kind": "INCLUDES"})
    return nodes, edges


_AUDIT_STAGES = (
    ("map", "Mapping project services and data flows."),
    ("hunt", "Reviewing security controls and the requested focus areas."),
    ("critic", "Checking which areas the review has covered."),
    ("verify", "Comparing observations with the available evidence."),
    ("prove", "Validating the findings and preparing the report."),
)


def start_audit(db, row):
    project = db.get(Project, row.project_id)
    row.repo = "acme/" + ("storefront" if project.slug == "main" else project.slug)
    row.status, row.step, row.started_at = "running", "map", utcnow()
    row.local_duration_seconds = randint(60, 120)
    row.counts = {"confirmed": 0, "needs_validation": 0, "rejected": 0}


def _settle_audit(db, row, status, finished_at):
    values = {"status": status, "step": "done" if status == "complete" else status, "finished_at": finished_at}
    if status == "complete":
        values["coverage_percent"] = 100
    result = db.execute(update(Audit).where(Audit.id == row.id, Audit.status == "running").values(**values))
    db.commit()
    db.refresh(row)
    return bool(result.rowcount)


def audit_progress(db, row):
    if not row.local_duration_seconds or not row.started_at:
        return {}
    duration = row.local_duration_seconds
    deadline = row.started_at + timedelta(seconds=duration)
    if row.status == "running" and utcnow() >= deadline:
        _settle_audit(db, row, "complete", deadline)
    elapsed = max(0, ((row.finished_at or utcnow()) - row.started_at).total_seconds())
    rng = Random(row.id)
    weights = [rng.randint(15, 25) for _ in _AUDIT_STAGES]
    events, offset = [], 0
    for weight, (step, message) in zip(weights, _AUDIT_STAGES):
        at = round(duration * offset / sum(weights))
        if at <= elapsed:
            events.append({"at": at, "step": step, "message": message})
        offset += weight
    if row.status == "complete":
        events.append({"at": duration, "step": "done", "message": "Audit complete. Results are ready to review."})
    if row.status == "cancelled":
        events.append({"at": elapsed, "step": "cancelled", "message": "Audit cancelled."})
    progress = {"events": events}
    if row.status == "running":
        progress["step"] = events[-1]["step"]
    return progress


def cancel_audit(db, row):
    audit_progress(db, row)
    if row.status != "running" or not _settle_audit(db, row, "cancelled", utcnow()):
        raise ValueError("This audit has already finished.")


def _factory_runs():
    if not _factory_file.exists():
        return {}
    return json.loads(_factory_file.read_text())


def _save_factory_runs(runs):
    draft = _factory_file.with_suffix(".tmp")
    draft.write_text(json.dumps(runs))
    draft.replace(_factory_file)


def _factory_progress(run):
    start = datetime.fromisoformat(run["started_at"])
    elapsed = (utcnow() - start).total_seconds()
    stages = [{"stage": stage, "message": message, "level": "ok"}
              for at, stage, message in _FACTORY_STAGES if at <= elapsed]
    seconds = _FACTORY_STAGES[-1][0]
    ready = elapsed >= seconds
    row = {**run, "status": "verified" if ready else "running", "approval": run.get("approval") if ready else None,
           "finished_at": (start + timedelta(seconds=seconds)).isoformat() if ready else None,
           "seconds": seconds if ready else None, "cost_usd": 0}
    return row, stages


def factory_request(method, path, body):
    if path == "/api/inbox":
        return []
    if path == "/api/intents" and method == "POST":
        return {"ok": True}
    with _factory_lock:
        runs = _factory_runs()
        if path == "/api/runs" and method == "POST":
            run_id = "local-" + uuid4().hex
            args = body or {}
            runs[run_id] = {"id": run_id, "item_id": args.get("intent"),
                            "title": args.get("title") or "Revision review",
                            "approval": "pending", "started_at": utcnow().isoformat(), "mode": "live"}
            _save_factory_runs(runs)
            return {"run": run_id}
        if path == "/api/runs" and method == "GET":
            rows = [_factory_progress(entry)[0] for entry in reversed(list(runs.values()))]
            done = sum(row["status"] == "verified" for row in rows)
            return {"summary": {"runs": len(rows), "verified": done,
                                "approved": sum(row["approval"] == "approved" for row in rows),
                                "cost_usd": 0}, "runs": rows}
        parts = path.strip("/").split("/")
        if len(parts) in (3, 4) and parts[:2] == ["api", "runs"] and parts[2] in runs:
            entry = runs[parts[2]]
            row, stages = _factory_progress(entry)
            if method == "POST" and len(parts) == 4:
                decision = {"approve": "approved", "reject": "rejected"}.get(parts[3])
                if not decision:
                    raise HTTPException(404, "Unknown decision.")
                if not row["finished_at"] or row["approval"] != "pending":
                    raise HTTPException(409, "This run is not awaiting a decision.")
                entry["approval"] = row["approval"] = decision
                _save_factory_runs(runs)
            return {"row": row, "events": stages, "pieces": []}
    raise HTTPException(404, "Run not found.")

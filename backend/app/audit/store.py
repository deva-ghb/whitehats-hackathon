"""A finished audit run becomes WhiteHats records: the audit, a finding per confirmed record, and a fix to approve.

A confirmed record with a proof check is offered as a fix for Fix Factory straight away, because WhiteHats can prove
that fix itself: Fix Factory sends back the changed files, and `recheck` runs the finding's proof check on the
audited snapshot with those files laid over it. The fix passes only when the check that failed on the original code
passes on the changed code: the attack that worked before no longer works.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Audit, Remediation, Signal
from .conductor import proof_rerun

INTENT_PREFIX = "WH-A-"  # the Fix Factory intent of a fix made from an audit finding

SOURCE = "audit"
SCANNER = "whitehats-audit"
DESTINATION = "Fix Factory"
FINGERPRINT_PREFIX = "audit:"


def _read(run_dir: Path, name: str, default):
    path = run_dir / name
    return json.loads(path.read_text()) if path.exists() else default


def import_run(db: Session, org_id: int, project_id: int, run_dir: Path, trigger: str, repo: str, branch: str = "",
               trigger_detail: str = "", audit: Audit | None = None) -> Audit:
    """Record a finished run. Safe to call again for the same run: nothing is added twice."""
    meta = _read(run_dir, "run-metadata.json", {})
    summary = _read(run_dir, "summary.json", {})
    snapshot = run_dir / "snapshot"
    if not snapshot.exists() and meta.get("target") and Path(meta["target"]).is_dir():
        shutil.copytree(meta["target"], snapshot, symlinks=False, ignore=shutil.ignore_patterns(".git"))
    if audit is None:
        audit = db.scalar(select(Audit).where(Audit.org_id == org_id, Audit.run_dir == str(run_dir)))
    if audit is None:
        audit = Audit(org_id=org_id, project_id=project_id, trigger=trigger, trigger_detail=trigger_detail,
                      profile=meta.get("profile", "quick"), repo=repo, branch=branch, run_dir=str(run_dir),
                      source_ref=str(meta.get("source_ref", ""))[:200], started_at=utcnow())
        db.add(audit)
        db.flush()
    audit.status = summary.get("status") or meta.get("run_status", "running")
    audit.step = "done" if summary else audit.step
    audit.counts = summary.get("counts", {})
    audit.coverage_percent = int(summary.get("coverage_percent", 0))
    audit.cost_usd = float(summary.get("cost", 0.0))
    audit.finished_at = audit.finished_at or (utcnow() if summary else None)

    proofs = _read(run_dir, "proofs.json", {})
    for record in _read(run_dir, "findings.json", []):
        if record.get("verdict") != "confirmed":
            continue
        signal = _finding(db, audit, record)
        if signal is not None and record["fingerprint"] in proofs:
            suggest(db, signal, record)
    db.commit()
    return audit


def _finding(db: Session, audit: Audit, record: dict) -> Signal | None:
    key = FINGERPRINT_PREFIX + record["fingerprint"]
    held = db.scalar(select(Signal).where(Signal.org_id == audit.org_id, Signal.project_id == audit.project_id,
                                          Signal.source == SOURCE, Signal.fingerprint == key,
                                          Signal.closed_at.is_(None)))
    if held is not None:
        return held
    sink = record["trace"][-1]
    signal = Signal(
        org_id=audit.org_id, project_id=audit.project_id, severity=record["severity"]["overall_severity"][:10],
        title=record["title"][:240], detail=_detail(record), entity=f"{sink['file']}:{sink['line']}"[:160],
        resource=f"https://github.com/{audit.repo}" if audit.repo else "", source=SOURCE, origin="live",
        fingerprint=key[:240], branch=audit.branch, audit_id=audit.id)
    db.add(signal)
    db.flush()
    return signal


def _detail(record: dict) -> str:
    trace = "\n".join(f"- {t['kind']}: {t['file']}:{t['line']}: {t['description']}" for t in record["trace"])
    return (f"{record['description']}\n\nRoot cause: {record['root_cause']}\n\nTrace:\n{trace}\n\n"
            f"Observed in a sealed local check: {record['execution']['observed_result']}")[:8000]


def suggest(db: Session, signal: Signal, record: dict) -> Remediation | None:
    if db.scalar(select(Remediation.id).where(Remediation.signal_id == signal.id, Remediation.org_id == signal.org_id)):
        return None
    sink = record["trace"][-1]
    severity = signal.severity
    row = Remediation(
        org_id=signal.org_id, project_id=signal.project_id, signal_id=signal.id, title=f"Fix: {record['title']}"[:200],
        target=f"{sink['file']}:{sink['line']}"[:160],
        action=("Fix Factory changes the code, then proves the fix with the audit's own proof check: the same test that "
                "showed the weakness on the audited code must pass on the changed code, in the same sealed container. "
                "The tests run, a second model reviews the change, and it opens a pull request for a person to merge."),
        why=f"{record['description']}\n\nFix: {record['remediation']['strategy']}"[:2000], destination=DESTINATION,
        factory_intent=f"{INTENT_PREFIX}{signal.id}", risk="high" if severity in ("critical", "high") else severity,
        status="pending", origin="live")
    db.add(row)
    return row


def record_for(db: Session, signal: Signal) -> tuple[Audit, dict, dict | None] | None:
    """The audit, its record and its proof check behind an audit finding."""
    audit = db.get(Audit, signal.audit_id) if signal.audit_id else None
    if audit is None or audit.org_id != signal.org_id:
        return None
    fingerprint = signal.fingerprint.removeprefix(FINGERPRINT_PREFIX)
    run_dir = Path(audit.run_dir)
    record = next((r for r in _read(run_dir, "findings.json", []) if r.get("fingerprint") == fingerprint), None)
    if record is None:
        return None
    return audit, record, _read(run_dir, "proofs.json", {}).get(fingerprint)


def intent(db: Session, signal: Signal, engine_repo: dict) -> dict:
    """The Fix Factory intent for an audit finding: the record in full, proved by its proof check."""
    found = record_for(db, signal)
    if found is None:
        raise LookupError("The audit behind this finding is gone, so there is nothing to prove a fix against.")
    audit, record, proof = found
    if proof is None:
        raise LookupError("This finding has no proof check, so Fix Factory cannot prove a fix for it.")
    sink = record["trace"][-1]
    return {
        "id": f"{INTENT_PREFIX}{signal.id}", "source": "whitehats", "repo": engine_repo["name"], "title": record["title"][:200],
        "severity": signal.severity,
        "summary": f"{record['description']}\n\nRecommended fix: {record['remediation']['strategy']}",
        "evidence": [f"{e['file']}:{e['line']}: {e['description']}"[:400] for e in record["evidence"]],
        "base": audit.branch or None,
        "proof": {"recheck": {"rule": record["fingerprint"], "scanner": SCANNER, "path": sink["file"]}},
        # Fix Factory starts from what the audit established: the record, the notes of the agents that found and
        # verified it, and the proof test its fix must pass.
        "handoff": {"from": "a WhiteHats security audit", "audit": audit.id, "record": record,
                    "notes": handoff_notes(Path(audit.run_dir), record["fingerprint"]),
                    "proof": {k: proof.get(k) for k in ("files", "command", "workdir", "explains")}},
    }


def handoff_notes(run_dir: Path, fingerprint: str) -> list[str]:
    """The handoff notes the hunter and the verifier of this finding wrote when their sessions were compacted."""
    lineage = _read(run_dir, "lineage.json", {}).get(fingerprint) or {}
    notes = []
    for role in ("hunter", "verifier"):
        agent = lineage.get(role)
        session = _read(run_dir / "agents" / agent, "session.json", {}) if agent else {}
        notes += [f"From the {role} ({agent}): {n}" for n in session.get("notes") or []]
    return notes[-4:]  # the newest notes hold what the earlier ones did


def recheck(db: Session, org_id: int, fingerprint: str, files: dict[str, str]) -> dict:
    """Fix Factory's proof for an audit finding: its proof check, run on the audited code with `files` laid over it."""
    signal = db.scalar(select(Signal).where(Signal.org_id == org_id, Signal.source == SOURCE,
                                            Signal.fingerprint == FINGERPRINT_PREFIX + fingerprint)
                       .order_by(Signal.id.desc()).limit(1))
    found = record_for(db, signal) if signal is not None else None
    if found is None or found[2] is None:
        raise ValueError(f"No audit finding with a proof check has the fingerprint {fingerprint}.")
    audit, _, proof = found
    run_dir = Path(audit.run_dir)
    work = run_dir / "rechecks" / utcnow().strftime("%Y%m%dT%H%M%S%f")
    work.mkdir(parents=True)
    deps = run_dir / "deps"
    result = proof_rerun(run_dir / "snapshot", deps if deps.is_dir() else None, proof, files, work)
    said = result["output"].split("--- stdout ---", 1)[-1].split("--- stderr ---", 1)[0].strip().splitlines()
    last = said[-1] if said else f"exit code {result['exit_code']}"
    if result["fixed"]:
        return {"fixed": True, "message": f"the proof check passes on the changed code: {last}", "still": []}
    return {"fixed": False, "message": "", "still": [{"line": 0, "message": f"the proof check still fails: {last}"}]}

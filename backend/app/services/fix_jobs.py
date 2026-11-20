"""A finding in a repository becomes a fix Fix Factory can make, proved by the rule that found it (FF.SRV).

A high or critical finding on a pushed change is suggested as a fix straight away: a pending Remediation for
Fix Factory, which a person applies on Approvals. Nothing is written for one flaw. The intent is made from the
finding (repository, branch, file, line, the engine and rule that found it, the weakness class), and its proof is
WhiteHats running that same rule again on the changed code (`recheck`): the fix passes only when the rule no
longer fires anywhere in the file. When a later commit closes the finding, its suggested fix is closed with it.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..audit import store as audit_store
from ..changes.findings import fingerprint, open_items
from ..db import utcnow
from ..decisions_api.decide import decide
from ..decisions_api.scanners import (
    scan_checkov,
    scan_gitleaks,
    scan_osv,
    scan_semgrep,
    scan_trivy,
)
from ..decisions_api.suspect import OPEN
from ..models import ChangeEvent, Connector, Remediation, Signal
from ..registry import switch

log = logging.getLogger("whitehats.fix_jobs")

SUGGEST = ("critical", "high")
INTENT_PREFIX = "WH-F-"
# Fixes made from a project's own findings (a scanner's, or an audit's): they run on that project's repository.
FINDING_INTENTS = (INTENT_PREFIX, audit_store.INTENT_PREFIX)
DESTINATION = "Fix Factory"
# The engine that raised a finding, by the name its suspects carry. The house rules ("whitehats") always run.
ENGINES = {"semgrep": scan_semgrep, "gitleaks": scan_gitleaks, "osv-scanner": scan_osv, "checkov": scan_checkov,
           "trivy": scan_trivy, "whitehats": None}
MAX_FILES = 50


class NotFixable(Exception):
    """Fix Factory cannot take this finding; the message says why in words, for the person it goes to instead."""


def intent_id(signal: Signal) -> str:
    return f"{INTENT_PREFIX}{signal.id}"


def item_for(db: Session, signal: Signal) -> tuple[ChangeEvent, dict] | None:
    """The change that raised this finding and its confirmed suspect, as the check run and comments showed it."""
    change = db.get(ChangeEvent, signal.change_id) if signal.change_id else None
    if change is None:
        return None
    for item in open_items(change.confirmations or []):
        if fingerprint(item["suspect"]) == signal.fingerprint:
            return change, item
    return None


def suggest(db: Session, signal: Signal, item: dict) -> Remediation | None:
    """Offer a fix for a new finding, once: any high or critical one, and a library with no fixed release that the
    WhiteHats Registry can fix (its build is made here when only its recipe exists). `signal` must have its id."""
    suspect = item["suspect"]
    library = switch.target(suspect)
    covered = library is not None and (switch.proved_build(db, signal.org_id, library) or switch.recipe_for(library))
    if signal.id is None or (signal.severity not in SUGGEST and not covered):
        return None
    if db.scalar(select(Remediation.id).where(Remediation.signal_id == signal.id, Remediation.org_id == signal.org_id)):
        return None
    if covered:
        return _suggest_registry_build(db, signal, item, library)
    where = f"{suspect['evidence']['path']}:{suspect['evidence']['start_line']}"
    row = Remediation(
        org_id=signal.org_id, project_id=signal.project_id, signal_id=signal.id, title=f"Fix: {suspect['title']}"[:200],
        target=where[:160],
        action=(f"Fix Factory changes the code on {signal.branch or 'the default branch'}, then proves the fix: "
                f"the {suspect['engine']} rule that found it no longer fires in {suspect['evidence']['path']}, the "
                "tests pass, nothing new is found, and a second model reviews it. It opens a pull request for a person "
                "to merge."),
        why=(item.get("explanation") or suspect["title"])[:2000], destination=DESTINATION,
        factory_intent=intent_id(signal), risk=signal.severity if signal.severity != "critical" else "high",
        status="pending", origin="live")
    db.add(row)
    return row


def _suggest_registry_build(db: Session, signal: Signal, item: dict, library: switch.Target) -> Remediation:
    """A library with no fixed release: make the registry's proved build if there is none, then offer the switch."""
    try:
        built = switch.ensure_build(db, signal.org_id, library)
        action = (f"Fix Factory points {library.manifest_dir or 'the project'} at {built.name} {built.version} from the "
                  "WhiteHats Registry, then proves it: the dependency scanner no longer finds the advisory, the tests "
                  "pass, and CI installs the build with the registry token. It opens a pull request for a person to merge.")
    except switch.NoSwitch as exc:
        action = str(exc)
    row = Remediation(
        org_id=signal.org_id, project_id=signal.project_id, signal_id=signal.id,
        title=f"Fix: use the WhiteHats Registry build of {library.name} {library.version}"[:200],
        target=f"{library.manifest_dir}/uv.lock"[:160], action=action,
        why=(item.get("explanation") or item["suspect"]["title"])[:2000], destination=DESTINATION,
        factory_intent=intent_id(signal), risk="medium" if signal.severity in ("low", "medium") else "high",
        status="pending", origin="live")
    db.add(row)
    return row


def _registry_intent(db: Session, signal: Signal, change: ChangeEvent, item: dict, library: switch.Target,
                     repo: dict) -> dict:
    built = switch.proved_build(db, signal.org_id, library)
    if built is None:
        raise NotFixable(f"The WhiteHats Registry has no proved build of {library.name} {library.version} yet.")
    connector = db.get(Connector, change.connector_id)
    source = connectors.for_row(db, connector) if connector is not None else None
    if source is None:
        raise NotFixable("The GitHub connection that saw this change is gone, so its files cannot be read.")
    base = "" if library.manifest_dir in ("", ".") else f"{library.manifest_dir}/"
    manifest, why = source.file_at(change.repo, f"{base}pyproject.toml", change.head_sha)
    if manifest is None:
        raise NotFixable(f"{base}pyproject.toml could not be read: {why}")
    lock, _ = source.file_at(change.repo, f"{base}uv.lock", change.head_sha)
    try:
        written = switch.files(db, signal.org_id, library, built, manifest.decode(), lock.decode() if lock else None)
    except switch.NoSwitch as exc:
        raise NotFixable(str(exc)) from exc
    contents = "\n\n".join(f"===== {path} =====\n{text}" for path, text in written.items())
    return {
        "id": intent_id(signal), "source": "whitehats", "repo": repo["name"],
        "title": f"Use the WhiteHats Registry build of {built.name} {built.version}"[:200], "severity": signal.severity,
        "summary": (f"{library.name} {library.version} has {library.advisory} and no fixed release. The WhiteHats Registry "
                    f"holds {built.name} {built.version}: the upstream fix applied to {library.version} and proved "
                    f"({built.proof.get('after', {}).get('said', '')}). Replace these two files with exactly the content "
                    f"below, character for character, and change nothing else. WhiteHats generated the lockfile with uv "
                    f"against the registry, so its URL, sha256 and size are the registry's own.\n\n{contents}"),
        "evidence": [f"{library.manifest_dir}/uv.lock pins {library.name} {library.version} ({library.advisory})"],
        "base": change.branch or None,
        "proof": {"recheck": {"rule": library.advisory, "scanner": "osv-scanner", "path": f"{base}uv.lock"}},
    }


def intent(db: Session, remediation: Remediation, repos: list[dict]) -> dict:
    """The Fix Factory intent for a suggested fix, made from its finding. Raises NotFixable with the reason."""
    signal = db.get(Signal, remediation.signal_id) if remediation.signal_id else None
    if signal is None or signal.org_id != remediation.org_id:
        raise NotFixable("The finding this fix was for is gone.")
    if signal.closed_at is not None:
        raise NotFixable(f"The finding is already closed: {signal.closed_reason}.")
    if signal.source == audit_store.SOURCE:
        return _audit_intent(db, signal, repos)
    found = item_for(db, signal)
    if found is None:
        raise NotFixable("The scan that raised this finding no longer lists it, so there is nothing to prove a fix against.")
    change, item = found
    repo = _engine_repo(change.repo, repos)
    if repo is None:
        raise NotFixable(f"Fix Factory is not set up for {change.repo}. A person has to make this change.")
    suspect, evidence = item["suspect"], item["suspect"]["evidence"]
    library = switch.target(suspect)
    if library is not None and (switch.proved_build(db, signal.org_id, library) or switch.recipe_for(library)):
        return _registry_intent(db, signal, change, item, library, repo)
    if suspect["engine"] not in ENGINES:
        raise NotFixable(f"WhiteHats cannot run the {suspect['engine']} rule again to prove a fix.")
    cwe = f" ({', '.join(suspect['cwe'])})" if suspect.get("cwe") else ""
    snippet = (evidence.get("snippet") or "").strip()
    return {
        "id": intent_id(signal), "source": "whitehats", "repo": repo["name"],
        "title": suspect["title"][:200], "severity": signal.severity,
        "summary": (f"{item.get('explanation') or suspect['title']} Found by {suspect['engine']} rule "
                    f"{suspect['rule_id']}{cwe} at {evidence['path']}:{evidence['start_line']} on commit "
                    f"{change.head_sha[:7]} of {change.branch or 'the default branch'}."),
        "evidence": [f"{evidence['path']}:{evidence['start_line']}: {snippet[:300]}" if snippet
                     else f"{evidence['path']}:{evidence['start_line']}"],
        "base": change.branch or None,
        "proof": {"recheck": {"rule": suspect["rule_id"], "scanner": suspect["engine"], "path": evidence["path"]}},
    }


def _audit_intent(db: Session, signal: Signal, repos: list[dict]) -> dict:
    """An audit finding's fix, proved by the audit's own proof check (app/audit/store.py)."""
    found = audit_store.record_for(db, signal)
    if found is None:
        raise NotFixable("The audit behind this finding is gone, so there is nothing to prove a fix against.")
    repo = _engine_repo(found[0].repo, repos)
    if repo is None:
        raise NotFixable(f"Fix Factory is not set up for {found[0].repo}. A person has to make this change.")
    try:
        return audit_store.intent(db, signal, repo)
    except LookupError as exc:
        raise NotFixable(str(exc)) from exc


def _engine_repo(repo: str, repos: list[dict]) -> dict | None:
    """The engine's repository that is this GitHub repository (`owner/name`), if the engine has one."""
    for candidate in repos:
        source = str(candidate.get("source") or "")
        if source.removeprefix("github:").lower() == repo.lower() and candidate.get("deliver") == "pull_request":
            return candidate
    return None


def recheck(rule: str, scanner: str, path: str, files: dict[str, str], acceptances: Sequence = ()) -> dict:
    """Run `scanner`'s rules (and the house rules) on these files; fixed when `rule` no longer fires in `path`."""
    if scanner not in ENGINES:
        raise ValueError(f"unknown scanner {scanner}")
    if not files or len(files) > MAX_FILES:
        raise ValueError(f"send between 1 and {MAX_FILES} files")
    with tempfile.TemporaryDirectory(prefix="wh-recheck-") as tmp:
        root = Path(tmp)
        for name, text in files.items():
            parts = PurePosixPath(name).parts
            if not parts or name.startswith("/") or ".." in parts:
                raise ValueError(f"{name} is not a path inside the repository")
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        engine = ENGINES[scanner]
        decision = decide(root, scanners=() if engine is None else (engine,), acceptances=acceptances)
    # A finding the registry's statements mark not affected is reported, never open: it does not hold the fix back.
    still = [s for s in decision.suspects if s.rule_id == rule and s.evidence.path == path and s.status == OPEN]
    ran = [run for run in decision.engines if run.engine == scanner]
    if engine is not None and not any(not run.error for run in ran):
        # An engine that did not run cannot say a weakness is gone (the same rule findings.apply follows).
        return {"fixed": False, "still": [], "message": f"{scanner} did not run, so the fix is not proved"}
    return {"fixed": not still, "message": "", "still": [
        {"line": s.evidence.start_line, "message": s.title} for s in still]}


def finding_closed(db: Session, signal: Signal) -> None:
    """A closed finding closes its suggested fix that is still waiting: no fix is needed from Fix Factory any more.

    A fix already sent keeps its status: fix_outcomes marks it verified only when its own run's pull requests are
    merged, and failed when its run ended without a fix, whatever else closed the finding. It gets the note.
    """
    note = f"The finding closed ({signal.closed_reason}): WhiteHats no longer finds it on {signal.branch or 'the default branch'}."
    for row in db.scalars(select(Remediation).where(
            Remediation.signal_id == signal.id, Remediation.org_id == signal.org_id,
            Remediation.status.in_(("pending", "dispatched")))).all():
        if row.status == "pending":
            row.status, row.done_by, row.done_at = "done", "WhiteHats", utcnow()
        row.result = ((row.result + "\n") if row.result else "") + note

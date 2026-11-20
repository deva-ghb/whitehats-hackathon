"""WhiteHats checks again (FD.2): when a person says an incident or a fix is done, each finding behind it is re-run the
way it was found, and only what the check shows counts.

- An audit finding: its proof check, run on the file as it is now on the default branch.
- A finding from a pushed change: the rule that found it, run again on the file as it is now.
- A cloud finding: Prowler checks that service of the account again; a finding from the activity history counts as gone
  when Prowler finds nothing unsafe on its resource.
- Anything else WhiteHats cannot check by itself: said so, never counted as checked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.base import SourceError
from ..db import utcnow
from ..models import Connector, Signal

log = logging.getLogger("whitehats.verify")

GONE, STILL, UNCHECKED = "gone", "still", "unchecked"
CLOUD = "whitehat:cloud"


@dataclass
class Check:
    signal: Signal
    outcome: str  # gone | still | unchecked
    said: str  # in words, for the timeline


def _repo_files(db: Session, signal: Signal):
    from . import audit_jobs

    found = audit_jobs._repository(db, SimpleNamespace(org_id=signal.org_id, project_id=signal.project_id))  # type: ignore[arg-type]
    if found is None:
        raise SourceError("No repository is connected to this project.")
    source, repo = found
    return source.repository_files(repo), str(repo.get("default_branch") or "the default branch")


def _close(db: Session, signal: Signal, why: str) -> None:
    from . import fix_jobs

    if signal.closed_at is None:
        signal.closed_at, signal.closed_reason = utcnow(), why[:60]
        fix_jobs.finding_closed(db, signal)  # a fix still waiting for it is not needed any more


def _audit(db: Session, signal: Signal) -> Check:
    from ..audit import store

    found = store.record_for(db, signal)
    if found is None or found[2] is None:
        return Check(signal, UNCHECKED, "its audit has no proof check to run again")
    record = found[1]
    paths = sorted({str(step.get("file")) for step in record.get("trace") or [] if step.get("file")})
    files, branch = _repo_files(db, signal)
    current = {p: text for p in paths if p in files.paths and (text := files.read(p)) is not None}
    if not current:
        return Check(signal, GONE, f"the files it was about are no longer on {branch}") if paths else Check(
            signal, UNCHECKED, "the audit did not say which files it was about")
    result = store.recheck(db, signal.org_id, signal.fingerprint.removeprefix(store.FINGERPRINT_PREFIX), current)
    if result["fixed"]:
        _close(db, signal, "checked: fixed")
        return Check(signal, GONE, f"its proof check now passes on {branch}")
    return Check(signal, STILL, f"its proof check still fails on {branch}")


def _code(db: Session, signal: Signal) -> Check:
    from . import fix_jobs

    found = fix_jobs.item_for(db, signal)
    if found is None:
        return Check(signal, UNCHECKED, "the scan that found it no longer lists it")
    suspect = found[1]["suspect"]
    path, rule, engine = suspect["evidence"]["path"], suspect["rule_id"], suspect["engine"]
    if engine not in fix_jobs.ENGINES:
        return Check(signal, UNCHECKED, f"WhiteHats cannot run the {engine} rule again")
    files, branch = _repo_files(db, signal)
    text = files.read(path) if path in files.paths else None
    if text is None:
        _close(db, signal, "checked: file gone")
        return Check(signal, GONE, f"{path} is no longer on {branch}")
    result = fix_jobs.recheck(rule, engine, path, {path: text})
    if result["fixed"]:
        _close(db, signal, "checked: fixed")
        return Check(signal, GONE, f"the rule no longer fires in {path} on {branch}")
    return Check(signal, STILL, result.get("message") or f"the rule still fires in {path} on {branch}")


def _cloud(db: Session, signal: Signal) -> Check:
    from ..connectors import routing
    from . import cloud_settings

    connector = next((c for c in db.scalars(select(Connector).where(Connector.org_id == signal.org_id,
                                                                    Connector.key == "aws"))
                      if signal.project_id in routing.projects_for(db, c, routing.connection_resource(c))), None)
    if connector is None:
        return Check(signal, UNCHECKED, "no AWS connection of this project can check it")
    prowler = signal.fingerprint.startswith(cloud_settings.FINGERPRINT_PREFIX)
    service = signal.fingerprint.removeprefix("prowler-aws-").split("_", 1)[0] if prowler else ""
    summary = cloud_settings.scan(db, connector, [service] if service else [])
    if "error" in summary or "limited" in summary:
        return Check(signal, UNCHECKED, f"Prowler could not finish: {summary.get('error') or summary.get('limited')}")
    db.refresh(signal)
    if prowler:
        return (Check(signal, GONE, "Prowler finds the setting safe now") if signal.closed_at
                else Check(signal, STILL, "Prowler still finds the setting unsafe"))
    unsafe = db.scalar(select(Signal.id).where(
        Signal.project_id == signal.project_id, Signal.source == CLOUD, Signal.closed_at.is_(None),
        Signal.fingerprint.like(f"{cloud_settings.FINGERPRINT_PREFIX}%"),
        Signal.resource.contains(signal.resource) if signal.resource else Signal.id < 0).limit(1))
    if signal.resource and unsafe is None:
        _close(db, signal, "checked: safe now")
        return Check(signal, GONE, f"Prowler finds nothing unsafe on {signal.resource}")
    return Check(signal, STILL if unsafe else UNCHECKED,
                 f"Prowler still finds {signal.resource} unsafe" if unsafe else "it names no resource Prowler can check")


CHECKERS = {"audit": _audit, "whitehats:code": _code, CLOUD: _cloud}


def check(db: Session, signal: Signal) -> Check:
    """Run the finding's own check again. A closed finding is gone already."""
    if signal.closed_at is not None:
        return Check(signal, GONE, f"it is closed ({signal.closed_reason})")
    checker = CHECKERS.get(signal.source)
    if checker is None:
        return Check(signal, UNCHECKED, "WhiteHats cannot check this kind of finding again by itself")
    try:
        return checker(db, signal)
    except (SourceError, ValueError, OSError) as exc:
        log.warning("could not check finding %s again: %s", signal.id, exc)
        return Check(signal, UNCHECKED, f"the check could not run: {exc}"[:300])


def _verdict(checks: list[Check]) -> tuple[bool, str]:
    """(all gone, the timeline's words). Unchecked findings never count as checked; they do not hold it back."""
    still = [c for c in checks if c.outcome == STILL]
    lines = [f"- {c.signal.title}: {c.said}" for c in checks]
    unchecked = [c for c in checks if c.outcome == UNCHECKED]
    head = ("Still there, so back to investigating:" if still else
            "Checked again: gone." if not unchecked else
            f"Checked what WhiteHats can; {len(unchecked)} could not be checked by WhiteHats and "
            "rest on the person's word:")
    return not still, "\n".join([head, *lines]) if lines else head


def incident(db: Session, inc) -> str:
    """Check every open finding of an incident a person says is fixed: resolved, or back to investigating."""
    from ..models import IncidentEvent

    checks = [check(db, s) for s in db.scalars(select(Signal).where(Signal.incident_id == inc.id))]
    gone, words = _verdict(checks)
    db.refresh(inc)
    if inc.status != "verifying":
        # A check closed the findings and WhiteHats already resolved the incident on that: record what was checked.
        inc.events.append(IncidentEvent(kind="note", actor="WhiteHats", text=words))
        db.commit()
        return inc.status
    inc.status = "resolved" if gone else "investigating"
    inc.resolved_at = utcnow() if gone else None
    inc.events.append(IncidentEvent(kind="status", actor="WhiteHats",
                                    text=f"Status changed from verifying to {inc.status}"))
    inc.events.append(IncidentEvent(kind="note", actor="WhiteHats",
                                    text=words if checks else "No findings to check: resolved on the person's word."))
    db.commit()
    return inc.status


def fix(db: Session, r) -> str:
    """Check the finding of a fix a person says is done: verified, or back to waiting with what is still there."""
    signal = db.get(Signal, r.signal_id) if r.signal_id else None
    if signal is None:
        r.status = "done"
    else:
        c = check(db, signal)
        r.status = {GONE: "verified", STILL: "dispatched", UNCHECKED: "done"}[c.outcome]
        said = {GONE: f"WhiteHats checked: gone ({c.said}).",
                STILL: f"WhiteHats checked: still there ({c.said}). Mark it done again once it is fixed.",
                UNCHECKED: f"WhiteHats could not check it ({c.said}); done on {r.done_by}'s word."}[c.outcome]
        r.result = ((r.result + "\n") if r.result else "") + said
    db.commit()
    return r.status


def due(db: Session) -> int:
    """One pass: every incident and fix waiting to be checked. Returns how many were checked."""
    from ..models import Incident, Remediation

    done = 0
    for inc in list(db.scalars(select(Incident).where(Incident.status == "verifying"))):
        incident(db, inc)
        done += 1
    for r in list(db.scalars(select(Remediation).where(Remediation.status == "verifying"))):
        fix(db, r)
        done += 1
    return done

"""Checkov in its own container, for Terraform and Dockerfiles. Severity comes from our policy table."""

from __future__ import annotations

import time
from pathlib import Path

from ..checkov_policies import policy
from ..container import parse_json, relative_path, run_container, skipped
from ..pins import CHECKOV_IMAGE
from ..suspect import EngineRun, Evidence, Suspect


def _address(resource: object) -> str:
    text = str(resource or "")
    if text and "/" not in text and "." in text:
        return text
    return ""


def _failed(payload: object) -> list[dict]:
    if isinstance(payload, list):
        rows: list[dict] = []
        for item in payload:
            rows.extend(_failed(item))
        return rows
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, dict):
        return []
    # A check skipped by a `checkov:skip` comment in the code still counts (R-052): comments are data, and accepted
    # findings come only from the owned accepted-findings file.
    rows = [*(results.get("failed_checks") or []), *(results.get("skipped_checks") or [])]
    return [row for row in rows if isinstance(row, dict)]


def _suspects(payload: object) -> list[Suspect]:
    suspects: list[Suspect] = []
    for row in _failed(payload):
        raw = str(row.get("file_abs_path") or row.get("file_path") or "")
        path = relative_path(raw)
        if not path or skipped(path):
            continue
        span = row.get("file_line_range") if isinstance(row.get("file_line_range"), list) else [1, 1]
        start = int(span[0]) if span else 1
        end = int(span[1]) if len(span) > 1 else start
        check_id = str(row.get("check_id") or "checkov")
        severity, cwe = policy(check_id)
        suspects.append(Suspect(
            engine="checkov",
            rule_id=check_id,
            title=str(row.get("check_name") or check_id)[:240],
            evidence=Evidence(path, start, end, resource=_address(row.get("resource"))),
            severity=severity,
            cwe=cwe,
        ))
    return suspects


def _relevant(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.endswith((".tf", ".tf.json")) or name == "Dockerfile" or name.lower().endswith(".dockerfile")


def scan(root: Path, paths: list[str] | None, *, timeout: int) -> tuple[list[Suspect], EngineRun]:
    # A diff with no infrastructure file still has the tree scanned only on a full run.
    if paths is not None and not any(_relevant(path) for path in paths):
        return [], EngineRun("checkov", CHECKOV_IMAGE, 0.0)
    started = time.perf_counter()
    args = [
        "-d", "/src",
        "--framework", "terraform",
        "--framework", "dockerfile",
        "--output", "json",  # not --quiet: it leaves out the checks a comment skipped (R-052)
        "--compact",
        "--download-external-modules", "false",
        "--soft-fail",
    ]
    result = run_container(CHECKOV_IMAGE, args, root, network=False, timeout=timeout)
    seconds = time.perf_counter() - started
    if result.timed_out:
        return [], EngineRun("checkov", CHECKOV_IMAGE, seconds, error="timed out")
    if result.exit_code == 127:
        return [], EngineRun("checkov", CHECKOV_IMAGE, seconds, error="docker is not available")
    payload = parse_json(result.stdout)
    if payload is None:
        return [], EngineRun("checkov", CHECKOV_IMAGE, seconds, error=f"no JSON (exit {result.exit_code})")
    return _suspects(payload), EngineRun("checkov", CHECKOV_IMAGE, seconds)


scan.name = "checkov"  # type: ignore[attr-defined]
scan.image = CHECKOV_IMAGE  # type: ignore[attr-defined]

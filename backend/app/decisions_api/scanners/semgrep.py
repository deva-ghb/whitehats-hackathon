"""Semgrep in its own container, with the public Python and secrets packs."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ..container import MOUNT, parse_json, relative_path, run_container, skipped
from ..pins import SEMGREP_CONFIGS, SEMGREP_IMAGE
from ..suspect import SEVERITIES, EngineRun, Evidence, Suspect

_CWE = re.compile(r"CWE-\d+")
_SEVERITY = {
    "CRITICAL": "critical",
    "ERROR": "high",
    "HIGH": "high",
    "WARNING": "medium",
    "MEDIUM": "medium",
    "INFO": "low",
    "LOW": "low",
    "INVENTORY": "low",
    "EXPERIMENT": "low",
}


def _cwe(value: object) -> list[str]:
    items = value if isinstance(value, list) else [value]
    found: list[str] = []
    for item in items:
        match = _CWE.search(str(item))
        if match and match.group(0) not in found:
            found.append(match.group(0))
    return found


def _suspects(payload: object) -> list[Suspect]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    suspects: list[Suspect] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        path = relative_path(str(row.get("path") or ""))
        if not path or skipped(path):
            continue
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        metadata = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
        start = row.get("start") if isinstance(row.get("start"), dict) else {}
        end = row.get("end") if isinstance(row.get("end"), dict) else {}
        severity = _SEVERITY.get(str(extra.get("severity") or "").upper(), "medium")
        if severity not in SEVERITIES:
            severity = "medium"
        line = int(start.get("line") or 1)
        suspects.append(Suspect(
            engine="semgrep",
            rule_id=str(row.get("check_id") or "semgrep"),
            title=str(extra.get("message") or row.get("check_id") or "Semgrep finding").split("\n", 1)[0][:240],
            evidence=Evidence(path, line, int(end.get("line") or line)),
            severity=severity,
            cwe=_cwe(metadata.get("cwe")),
        ))
    return suspects


def scan(root: Path, paths: list[str] | None, *, timeout: int) -> tuple[list[Suspect], EngineRun]:
    started = time.perf_counter()
    targets = [MOUNT] if paths is None else [f"{MOUNT}/{path}" for path in paths]
    if paths is not None and not paths:
        return [], EngineRun("semgrep", SEMGREP_IMAGE, 0.0)
    # `--disable-nosem`: a `# nosemgrep` comment in the change does not remove a finding (R-052).
    args = ["semgrep", "scan", "--json", "--quiet", "--metrics=off", "--disable-version-check", "--no-git-ignore", "--disable-nosem"]
    for config in SEMGREP_CONFIGS:
        args.extend(["--config", config])
    for name in (".git", ".pytest_cache", ".ruff_cache", "node_modules", ".terraform", "__pycache__"):
        args.extend(["--exclude", name])
    args.extend(targets)
    cache = Path(os.environ.get("WH_SEMGREP_CACHE", Path.home() / ".cache" / "whitehats" / "semgrep"))
    cache.mkdir(parents=True, exist_ok=True)
    result = run_container(
        SEMGREP_IMAGE, args, root, network=True, timeout=timeout,
        extra_mounts=[(cache, "/root/.semgrep", True)],
    )
    seconds = time.perf_counter() - started
    if result.timed_out:
        return [], EngineRun("semgrep", SEMGREP_IMAGE, seconds, error="timed out")
    if result.exit_code == 127:
        return [], EngineRun("semgrep", SEMGREP_IMAGE, seconds, error="docker is not available")
    payload = parse_json(result.stdout)
    if not isinstance(payload, dict) or "results" not in payload:
        return [], EngineRun("semgrep", SEMGREP_IMAGE, seconds, error=f"no JSON (exit {result.exit_code})")
    return _suspects(payload), EngineRun("semgrep", SEMGREP_IMAGE, seconds)


scan.name = "semgrep"  # type: ignore[attr-defined]
scan.image = SEMGREP_IMAGE  # type: ignore[attr-defined]

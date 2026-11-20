"""gitleaks in its own container. The secret value is never copied onto a suspect.

A change cannot switch it off (R-052): `gitleaks:allow` comments are ignored, the rules are always gitleaks' own
defaults from our config (a repository's `.gitleaks.toml` is not read), and a `.gitleaksignore` is hidden by the
container runner. Accepted findings come only from the owned accepted-findings file (R-047).
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ..container import MOUNT, parse_json, relative_path, run_container, skipped
from ..pins import GITLEAKS_IMAGE
from ..suspect import EngineRun, Evidence, Suspect


def _suspects(payload: object) -> list[Suspect]:
    rows = payload if isinstance(payload, list) else []
    suspects: list[Suspect] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = relative_path(str(row.get("File") or ""))
        if not path or skipped(path):
            continue
        line = int(row.get("StartLine") or 1)
        end = int(row.get("EndLine") or line) or line
        suspects.append(Suspect(
            engine="gitleaks",
            rule_id=str(row.get("RuleID") or "gitleaks"),
            title=str(row.get("Description") or "Secret in source"),
            evidence=Evidence(path, line, end),
            severity="high",
            cwe=["CWE-798"],
        ))
    return suspects


CONFIG = "# WhiteHats runs gitleaks' default rules; a repository cannot replace or loosen them.\n[extend]\nuseDefault = true\n"
_CONFIG_DIR: Path | None = None


def _config_dir() -> Path:
    """A folder holding only our config: also the place gitleaks looks for an ignore file, and there is none."""
    global _CONFIG_DIR
    if _CONFIG_DIR is None or not (_CONFIG_DIR / "gitleaks.toml").is_file():
        _CONFIG_DIR = Path(tempfile.mkdtemp(prefix="wh-gitleaks-"))
        (_CONFIG_DIR / "gitleaks.toml").write_text(CONFIG, encoding="utf-8")
    return _CONFIG_DIR


def scan(root: Path, paths: list[str] | None, *, timeout: int) -> tuple[list[Suspect], EngineRun]:
    """Scan the tree. Callers drop findings outside the diff."""
    del paths  # gitleaks walks a directory; the decision filters to the diff afterwards
    started = time.perf_counter()
    args = [
        "dir", MOUNT,
        "--report-format", "json",
        "--report-path", "-",
        "--exit-code", "0",
        "--no-banner",
        "--redact",
        "--log-level", "error",
        "--ignore-gitleaks-allow",
        "--config", "/wh/gitleaks.toml",
        "--gitleaks-ignore-path", "/wh",
    ]
    result = run_container(GITLEAKS_IMAGE, args, root, network=False, timeout=timeout,
                           extra_mounts=[(_config_dir(), "/wh", False)])
    seconds = time.perf_counter() - started
    if result.timed_out:
        return [], EngineRun("gitleaks", GITLEAKS_IMAGE, seconds, error="timed out")
    if result.exit_code == 127:
        return [], EngineRun("gitleaks", GITLEAKS_IMAGE, seconds, error="docker is not available")
    payload = parse_json(result.stdout)
    if not isinstance(payload, list):
        return [], EngineRun("gitleaks", GITLEAKS_IMAGE, seconds, error=f"no JSON (exit {result.exit_code})")
    return _suspects(payload), EngineRun("gitleaks", GITLEAKS_IMAGE, seconds)


scan.name = "gitleaks"  # type: ignore[attr-defined]
scan.image = GITLEAKS_IMAGE  # type: ignore[attr-defined]

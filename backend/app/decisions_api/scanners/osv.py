"""OSV-Scanner in its own container.

A pyproject.toml that lists dependencies and has no lockfile is written out as a
requirements file for the scanner, and the finding is reported on the manifest.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import tomllib
from pathlib import Path

from ..container import MOUNT, parse_json, relative_path, run_container, skipped
from ..cvss import base_score
from ..pins import OSV_IMAGE
from ..suspect import EngineRun, Evidence, Suspect, severity_from_cvss

_WORD = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MODERATE": "medium",
    "MEDIUM": "medium",
    "LOW": "low",
}


def _manifests(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in {
            ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", ".terraform", "dist", ".venv", "venv",
        }]
        for name in filenames:
            if name == "pyproject.toml":
                found.append(Path(dirpath) / name)
    return found


def _requirements(root: Path) -> tuple[dict[str, str], Path | None]:
    """Map a generated requirements path inside the container to the pyproject it came from."""
    mapping: dict[str, str] = {}
    directory = Path(tempfile.mkdtemp(prefix="wh-osv-"))
    index = 0
    for manifest in _manifests(root):
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        deps = (data.get("project") or {}).get("dependencies") or []
        lines = []
        for dep in deps:
            if isinstance(dep, str) and dep.strip():
                lines.append(dep.split(";", 1)[0].strip())
        if not lines:
            continue
        target = directory / f"req-{index}.txt"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        relative = manifest.relative_to(root).as_posix()
        mapping[f"/req/{target.name}"] = relative
        index += 1
    return mapping, directory if mapping else None


def _severity_and_cwe(record: dict) -> tuple[str, list[str]]:
    specific = record.get("database_specific") if isinstance(record.get("database_specific"), dict) else {}
    cwe = [item for item in (specific.get("cwe_ids") or []) if str(item).startswith("CWE-")]
    for entry in record.get("severity") or []:
        if not isinstance(entry, dict):
            continue
        score = base_score(str(entry.get("score") or ""))
        if score is not None:
            return severity_from_cvss(score), cwe or ["CWE-1104"]
    word = str(specific.get("severity") or "").upper()
    if word in _WORD:
        return _WORD[word], cwe or ["CWE-1104"]
    return "high", cwe or ["CWE-1104"]


def _canonical(record: dict) -> str:
    for alias in record.get("aliases") or []:
        if str(alias).startswith("CVE-"):
            return str(alias)
    return str(record.get("id") or "osv")


def _rank(record: dict) -> int:
    if any(isinstance(entry, dict) and base_score(str(entry.get("score") or "")) is not None
           for entry in (record.get("severity") or [])):
        return 2
    specific = record.get("database_specific") if isinstance(record.get("database_specific"), dict) else {}
    if str(specific.get("severity") or "").upper() in _WORD:
        return 1
    return 0


def _line(text: str, package: str) -> int:
    needle = package.lower()
    for number, line in enumerate(text.splitlines(), start=1):
        if needle and needle in line.lower():
            return number
    return 1


def _suspects(payload: object, root: Path, generated: dict[str, str]) -> list[Suspect]:
    blocks = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return []
    best: dict[tuple[str, str, str], dict] = {}
    where: dict[tuple[str, str, str], tuple[str, str]] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        raw_path = str(source.get("path") or "")
        path = generated.get(raw_path) or relative_path(raw_path)
        if not path or skipped(path):
            continue
        for package in block.get("packages") or []:
            if not isinstance(package, dict):
                continue
            info = package.get("package") if isinstance(package.get("package"), dict) else {}
            name = str(info.get("name") or "")
            # Named as package indexes compare names (PEP 503), so "Toy_PDF" in a release is "toy-pdf" in a lockfile.
            pinned = f"{name.lower().replace('_', '-').replace('.', '-')}@{info.get('version') or ''}"
            grouped: dict[str, dict] = {}
            for record in package.get("vulnerabilities") or []:
                if not isinstance(record, dict):
                    continue
                key = _canonical(record)
                current = grouped.get(key)
                if current is None or _rank(record) > _rank(current):
                    grouped[key] = record
            for rule_id, record in grouped.items():
                slot = (path, name, rule_id)
                current = best.get(slot)
                if current is None or _rank(record) > _rank(current):
                    best[slot] = record
                    where[slot] = (path, name, pinned)
    suspects: list[Suspect] = []
    for slot, record in best.items():
        path, name, pinned = where[slot]
        manifest = root / path
        line = _line(manifest.read_text(encoding="utf-8", errors="replace"), name) if manifest.is_file() else 1
        severity, cwe = _severity_and_cwe(record)
        title = str(record.get("summary") or record.get("id") or slot[2])
        suspects.append(Suspect(
            engine="osv-scanner",
            rule_id=slot[2],
            title=title[:240],
            evidence=Evidence(path, line, line, package=pinned),
            severity=severity,
            cwe=cwe,
        ))
    return suspects


def _payload_or_error(stdout: str, stderr: str, exit_code: int) -> tuple[dict | None, str]:
    """Empty lockfiles make OSV-Scanner exit 128 with no JSON. That tree is clean."""
    payload = parse_json(stdout)
    if isinstance(payload, dict) and "results" in payload:
        return payload, ""
    if "No package sources found" in stderr:
        return {"results": []}, ""
    if exit_code == 127:
        return None, "docker is not available"
    return None, f"no JSON (exit {exit_code})"


def scan(root: Path, paths: list[str] | None, *, timeout: int) -> tuple[list[Suspect], EngineRun]:
    started = time.perf_counter()
    if paths is not None and not any(path.endswith(("pyproject.toml", "requirements.txt", "poetry.lock", "uv.lock",
                                                     "package-lock.json", "go.mod", "Cargo.lock")) for path in paths):
        return [], EngineRun("osv-scanner", OSV_IMAGE, 0.0)
    generated, directory = _requirements(root)
    if paths is not None:
        generated = {container: manifest for container, manifest in generated.items() if manifest in paths}
    try:
        args = ["scan", "source", "--format", "json", "--no-resolve", "-r", MOUNT]
        mounts: list[tuple[Path, str, bool]] = []
        if directory is not None and generated:
            mounts.append((directory, "/req", False))
            for container in generated:
                args.extend(["-L", f"requirements.txt:{container}"])
        result = run_container(OSV_IMAGE, args, root, network=True, timeout=timeout, extra_mounts=mounts)
    finally:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
    seconds = time.perf_counter() - started
    if result.timed_out:
        return [], EngineRun("osv-scanner", OSV_IMAGE, seconds, error="timed out")
    if result.exit_code == 127:
        return [], EngineRun("osv-scanner", OSV_IMAGE, seconds, error="docker is not available")
    payload, error = _payload_or_error(result.stdout, result.stderr, result.exit_code)
    if error or payload is None:
        return [], EngineRun("osv-scanner", OSV_IMAGE, seconds, error=error or "no JSON")
    return _suspects(payload, root, generated), EngineRun("osv-scanner", OSV_IMAGE, seconds)


scan.name = "osv-scanner"  # type: ignore[attr-defined]
scan.image = OSV_IMAGE  # type: ignore[attr-defined]

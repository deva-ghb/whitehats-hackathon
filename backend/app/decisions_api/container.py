"""Run one pinned image and read its JSON. Stdout is parsed in memory and not stored."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .pins import SEMGREP_IMAGE

MOUNT = "/src"
# Files at the top of a tree with which a change could tell an engine to look away (R-052). When one is there, an empty
# file is mounted over it, so the engine reads nothing from it; the repository itself is never changed.
SUPPRESSION_FILES = (".gitleaksignore", ".gitleaks.toml", ".semgrepignore", ".checkov.yaml", ".checkov.yml")
_EMPTY: Path | None = None


def _empty_file() -> Path:
    global _EMPTY
    if _EMPTY is None or not _EMPTY.is_file():
        _EMPTY = Path(tempfile.mkdtemp(prefix="wh-empty-")) / "empty"
        _EMPTY.write_text("", encoding="utf-8")
    return _EMPTY


_VISIBLE: bool | None = None
PROBE_IMAGE = SEMGREP_IMAGE  # an engine image that is pulled anyway, and has `cat`


def folders_visible(timeout: int = 60) -> bool:
    """Whether Docker sees the folders this process makes. Once per process: a marker file is written in a scratch
    folder and a container reads it back. Docker mounts by host path, so a process with a private /tmp makes folders
    Docker cannot see, and Docker silently mounts an empty folder instead: every engine would scan nothing."""
    global _VISIBLE
    if _VISIBLE:
        return True
    marker = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="wh-probe-") as folder:
        (Path(folder) / "marker").write_text(marker, encoding="utf-8")
        try:
            done = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat",
                                   "-v", f"{folder}:/probe:ro", PROBE_IMAGE, "/probe/marker"],
                                  capture_output=True, text=True, timeout=timeout, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False  # no Docker, or no answer: each engine says so itself
    _VISIBLE = done.returncode == 0 and done.stdout.strip() == marker
    return _VISIBLE


@dataclass
class ContainerResult:
    exit_code: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False


def run_container(
    image: str,
    args: list[str],
    root: Path,
    *,
    network: bool,
    timeout: int,
    extra_mounts: list[tuple[Path, str, bool]] | None = None,
) -> ContainerResult:
    """`extra_mounts` entries are `(host path, container path, writable)`."""
    name = f"wh-dec-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    command = ["docker", "run", "--rm", "--name", name]
    if not network:
        command.extend(["--network", "none"])
    command.extend(["-v", f"{root.resolve()}:{MOUNT}:ro"])
    for name in SUPPRESSION_FILES:
        if (root / name).is_file():
            command.extend(["-v", f"{_empty_file()}:{MOUNT}/{name}:ro"])
    for host, dest, writable in extra_mounts or []:
        mode = "rw" if writable else "ro"
        command.extend(["-v", f"{host.resolve()}:{dest}:{mode}"])
    command.append(image)
    command.extend(args)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, text=True, timeout=15, check=False)
        return ContainerResult(exit_code=124, stdout="", timed_out=True)
    except FileNotFoundError:
        return ContainerResult(exit_code=127, stdout="")
    return ContainerResult(
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def parse_json(stdout: str) -> object | None:
    text = stdout.strip()
    if not text:
        return None
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if not starts:
        return None
    try:
        return json.loads(text[min(starts):])
    except json.JSONDecodeError:
        return None


def relative_path(path: str) -> str:
    """Container path `/src/a/b` becomes `a/b`. A host path is not expected."""
    text = path.replace("\\", "/").strip()
    text = text.removeprefix(MOUNT.rstrip("/") + "/")
    return text.lstrip("./")


def skipped(path: str) -> bool:
    parts = set(relative_path(path).split("/"))
    return bool(parts & {
        ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules",
        ".terraform", "dist", ".venv", "venv",
    })

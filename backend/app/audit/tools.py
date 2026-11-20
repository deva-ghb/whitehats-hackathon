"""What an audit agent can do: read the snapshot, search it, and run a bounded check in a sealed container.

Reading is confined to the snapshot root: no absolute paths, no `..`, no symbolic links, nothing under `.git`.
A check runs in Docker with no network, the snapshot mounted read-only, an empty environment, a non-root user, no
capabilities, and hard limits on processor, memory, processes, file size and time. The agent's own scratch folder is
the only place the check can write. What the check printed is captured by this code (the trusted parent), not by
the check, and kept as the agent's artifact, so the evidence a ledger links to was never written by target code.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_READ_LINES = 400
MAX_SEARCH_HITS = 80
MAX_LIST = 400
MAX_TOOL_OUTPUT = 24_000  # characters a tool returns to the model
SANDBOX_IMAGE = os.environ.get("WH_AUDIT_SANDBOX_IMAGE", "whitehats-audit-sandbox:1")
CHECK_TIMEOUT_S = 90
MAX_CHECK_FILES = 6
MAX_CHECK_FILE_BYTES = 64_000
MAX_ARTIFACT_BYTES = 200_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".terraform"}


class ToolError(ValueError):
    """A tool call the agent made wrongly; the message goes back to it so it can try again."""


@dataclass
class Snapshot:
    root: Path
    files: list[str] = field(default_factory=list)
    deps: Path | None = None  # the pinned dependencies WhiteHats installed for checks, mounted read-only
    deps_notes: list[str] = field(default_factory=list)
    cloud: list[dict] = field(default_factory=list)  # the live cloud map's nodes as WhiteHats last read them
    leads: list[dict] = field(default_factory=list)  # what WhiteHats' scanners raised on this snapshot

    @classmethod
    def at(cls, root: Path) -> Snapshot:
        root = root.resolve()
        files = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if full.is_symlink() or not full.is_file():
                    continue
                files.append(full.relative_to(root).as_posix())
        return cls(root, files)

    def resolve(self, rel: str) -> Path:
        rel = (rel or "").strip().lstrip("./") if rel not in ("", ".", "./") else ""
        parts = PurePosixPath(rel).parts
        if rel.startswith("/") or ".." in parts or any(p in SKIP_DIRS for p in parts):
            raise ToolError(f"{rel!r} is not a repository-relative path inside the snapshot.")
        path = self.root.joinpath(*parts) if parts else self.root
        probe = self.root
        for p in parts:
            probe = probe / p
            if probe.is_symlink():
                raise ToolError(f"{rel} is a symbolic link; links are not followed.")
        if not path.exists():
            raise ToolError(f"{rel} does not exist in the snapshot.")
        return path

    def text(self, rel: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise ToolError(f"{rel} is a folder, not a file.")
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            raise ToolError(f"{rel} is a binary file.")
        return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------------ read-only source tools

def list_files(snap: Snapshot, path: str = "") -> str:
    base = snap.resolve(path)
    if base.is_file():
        return path
    prefix = "" if base == snap.root else base.relative_to(snap.root).as_posix() + "/"
    hits = [f for f in snap.files if f.startswith(prefix)]
    shown = hits[:MAX_LIST]
    more = f"\n... and {len(hits) - len(shown)} more; list a subfolder." if len(hits) > len(shown) else ""
    return "\n".join(shown) + more if shown else "(no files)"


def read_file(snap: Snapshot, path: str, start_line: int = 1, end_line: int | None = None) -> str:
    lines = snap.text(path).splitlines()
    start = max(1, int(start_line or 1))
    end = min(len(lines), int(end_line) if end_line else start + MAX_READ_LINES - 1, start + MAX_READ_LINES - 1)
    if start > len(lines):
        return f"{path} has {len(lines)} lines."
    body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
    tail = f"\n[{path}: lines {start}-{end} of {len(lines)}]"
    return body + tail


def search(snap: Snapshot, pattern: str, path: str = "", literal: bool = False) -> str:
    try:
        rx = re.compile(re.escape(pattern) if literal else pattern)
    except re.error as exc:
        raise ToolError(f"The pattern does not compile: {exc}. Try literal=true.") from exc
    base = snap.resolve(path)
    prefix = "" if base == snap.root else base.relative_to(snap.root).as_posix()
    hits = []
    for rel in snap.files:
        if prefix and not (rel == prefix or rel.startswith(prefix + "/")):
            continue
        try:
            text = snap.text(rel)
        except ToolError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                if len(hits) >= MAX_SEARCH_HITS:
                    return "\n".join(hits) + f"\n[stopped at {MAX_SEARCH_HITS} matches; narrow the search]"
    return "\n".join(hits) or "(no matches)"


def cloud_facts(snap: Snapshot, query: str) -> str:
    """Nodes of the live cloud map whose name, type, id or settings mention `query` (any case), with their settings.

    These are facts WhiteHats read from the cloud account with its read-only role. The agent gets no cloud access of
    its own: it reads what was already collected.
    """
    if not snap.cloud:
        return "No live cloud facts are available for this audit."
    needle = (query or "").strip().lower()
    hits = [n for n in snap.cloud if not needle or needle in json.dumps(n).lower()]
    shown = [json.dumps({k: n.get(k) for k in ("id", "type", "name", "properties")})[:3000] for n in hits[:15]]
    more = f"\n[{len(hits) - 15} more nodes match; narrow the query]" if len(hits) > 15 else ""
    return ("\n".join(shown) + more) if shown else f"No cloud node mentions {query!r}."


# ------------------------------------------------------------------ the sealed local check

def sandbox_ready() -> str:
    """'' when checks can run; otherwise why not, in words that go into needs_validation blockers."""
    if not shutil.which("docker"):
        return "Docker is not installed, so no bounded local check can run."
    probe = subprocess.run(["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        return f"The sandbox image {SANDBOX_IMAGE} is not built, so no bounded local check can run."
    return ""


def prepare_dependencies(snap: Snapshot, into: Path) -> list[str]:
    """Install the snapshot's pinned Python dependencies into `into`, for checks to import read-only at /deps.

    Done by WhiteHats before the audit, in a throwaway container that has the network but not the snapshot. Only
    ready-built wheels are taken (`--only-binary=:all:`), so no package's build script runs. Returns notes on any
    dependency that could not be installed, which verifiers read as facts about their sandbox.
    """
    pins: list[str] = []
    for rel in snap.files:
        if rel.endswith("pyproject.toml"):
            try:
                project = tomllib.loads(snap.text(rel)).get("project", {})
            except tomllib.TOMLDecodeError:
                continue
            pins += [d for d in project.get("dependencies", []) if "==" in d]
    into.mkdir(parents=True, exist_ok=True)
    notes = []
    for pin in sorted(set(pins)):
        proc = subprocess.run(
            ["docker", "run", "--rm", "--user", "0:0", "-v", f"{into}:/deps", SANDBOX_IMAGE, "pip", "install",
             "--quiet", "--no-cache-dir", "--only-binary=:all:", "--target", "/deps", pin],
            capture_output=True, text=True, timeout=300, check=False)
        if proc.returncode != 0:
            notes.append(f"{pin} has no ready-built wheel for Python 3.12, so checks run without it "
                         f"(the image's own versions are pyyaml 6.0.3 and python-hcl2 7.3.1).")
    return notes


def run_check(snap: Snapshot, agent_dir: Path, agent_id: str, files: dict[str, str], command: str,
              workdir: str = "") -> tuple[str, str, int]:
    """Run `command` in the sealed container. Returns what the agent reads, the artifact's path relative to the run
    folder, and the exit code (-1 when it was killed at the time limit)."""
    if not files or len(files) > MAX_CHECK_FILES:
        raise ToolError(f"Send between 1 and {MAX_CHECK_FILES} check files.")
    if not command.strip() or len(command) > 400:
        raise ToolError("Give one short command, such as: python check.py")
    scratch = agent_dir / "scratch" / uuid.uuid4().hex[:8]
    scratch.mkdir(parents=True)
    for name, content in files.items():
        parts = PurePosixPath(name).parts
        if len(parts) != 1 or name.startswith(".") or "/" in name:
            raise ToolError(f"{name!r}: check files are plain names in the scratch folder, such as check.py.")
        if len(content.encode()) > MAX_CHECK_FILE_BYTES:
            raise ToolError(f"{name} is larger than {MAX_CHECK_FILE_BYTES} bytes.")
        (scratch / name).write_text(content)
    cwd = "/scratch"
    if workdir:
        inside = snap.resolve(workdir).relative_to(snap.root).as_posix()
        cwd = "/target" if inside == "." else f"/target/{inside}"
    name = f"wh-audit-{agent_id}-{uuid.uuid4().hex[:6]}"
    argv = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "--cpus", "1", "--memory", "512m", "--memory-swap", "512m", "--pids-limit", "64",
            "--ulimit", "fsize=10485760", "--ulimit", "nofile=256",
            "--tmpfs", "/tmp:rw,size=32m,mode=1777",
            "-v", f"{snap.root}:/target:ro", "-v", f"{scratch}:/scratch:rw",
            *(["-v", f"{snap.deps}:/deps:ro"] if snap.deps else []),
            "-e", "HOME=/scratch", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTHONPATH=/scratch" + (":/deps" if snap.deps else ""),
            "-w", cwd, SANDBOX_IMAGE, "sh", "-c", command]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=CHECK_TIMEOUT_S, check=False)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        code, out, err = -1, _text(exc.stdout), _text(exc.stderr) + f"\n[killed after {CHECK_TIMEOUT_S}s]"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)  # target-controlled output is never kept
    out, err = out[-MAX_ARTIFACT_BYTES // 2:], err[-MAX_ARTIFACT_BYTES // 2:]
    artifacts = agent_dir / "artifacts"
    artifacts.mkdir(exist_ok=True)
    n = len(list(artifacts.iterdir())) + 1
    artifact = artifacts / f"check-{n}.json"
    with open(artifact, "x") as f:  # exclusive create: never overwrite evidence
        json.dump({"command": command, "workdir": workdir or "", "files": files, "exit_code": code, "stdout": out,
                   "stderr": err, "image": SANDBOX_IMAGE,
                   "limits": "no network, read-only target, 1 cpu, 512 MB, 64 processes, 10 MB files, "
                             f"{CHECK_TIMEOUT_S}s"}, f, indent=1)
    rel = f"agents/{agent_id}/artifacts/{artifact.name}"
    shown = f"exit code {code}\n--- stdout ---\n{out[-8000:]}\n--- stderr ---\n{err[-4000:]}\n[artifact: {rel}]"
    return shown, rel, code


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def clip(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    return text[:MAX_TOOL_OUTPUT] + f"\n[cut at {MAX_TOOL_OUTPUT} characters; read a narrower range]"

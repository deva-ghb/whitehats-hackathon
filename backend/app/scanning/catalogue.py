"""The scanner catalogue (spec 5.3): the only scanners a scan plan may name, what each reads, and its pinned image.

A plan names scanners by these names and nothing else: every option a scanner runs with lives in its own module
(app/decisions_api/scanners), never in a plan. The licence is recorded because every scanner here is open source.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass

from ..decisions_api.scanners import SCANNERS
from ..decisions_api.suspect import EngineRun, Suspect

LOCK_FILES = ("pyproject.toml", "requirements.txt", "poetry.lock", "uv.lock", "Pipfile.lock", "package-lock.json",
              "yarn.lock", "pnpm-lock.yaml", "go.mod", "Cargo.lock")
CODE = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".php", ".cs", ".sh")


@dataclass(frozen=True)
class Entry:
    name: str
    reads: str  # what it is for, in a few words
    licence: str
    image: str
    run: Callable[..., tuple[list[Suspect], EngineRun]]  # (root, paths, *, timeout)


def _scanner(name: str):
    return next(scan for scan in SCANNERS if getattr(scan, "name", "") == name)


def _entry(name: str, reads: str, licence: str) -> Entry:
    scan = _scanner(name)
    return Entry(name, reads, licence, str(getattr(scan, "image", "")), scan)


CATALOGUE: dict[str, Entry] = {entry.name: entry for entry in (
    _entry("semgrep", "source code", "LGPL-2.1 (Semgrep CE)"),
    _entry("gitleaks", "secrets in any file", "MIT"),
    _entry("osv-scanner", "package lock files and manifests", "Apache-2.0"),
    _entry("checkov", "Terraform and Dockerfiles", "Apache-2.0"),
    _entry("trivy", "container images (a Dockerfile's base, or an image by digest)", "Apache-2.0"),
)}


def is_lock_file(path: str) -> bool:
    return posixpath.basename(path) in LOCK_FILES


def is_dockerfile(path: str) -> bool:
    name = posixpath.basename(path)
    return name == "Dockerfile" or name.lower().endswith(".dockerfile")


def defaults(path: str) -> tuple[str, ...]:
    """The scanners that read this file by default: the weekly floor runs exactly these on every file."""
    chosen = ["gitleaks"]
    if path.endswith(CODE):
        chosen.append("semgrep")
    if is_lock_file(path):
        chosen.append("osv-scanner")
    if path.endswith((".tf", ".tf.json")):
        chosen.append("checkov")
    if is_dockerfile(path):
        chosen += ["checkov", "trivy"]
    return tuple(chosen)


def run(name: str, root, paths: list[str], *, timeout: int) -> tuple[list[Suspect], EngineRun]:
    """Run one catalogue scanner over `paths` and keep only what it says about them (gitleaks reads the whole tree)."""
    suspects, engine = CATALOGUE[name].run(root, paths, timeout=timeout)
    wanted = set(paths)
    return [suspect for suspect in suspects if suspect.evidence.path in wanted], engine

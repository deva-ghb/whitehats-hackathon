"""Build a fixed version of a library from its upstream release, prove it, and keep it for the company's index.

The upstream source is fetched from PyPI and checked against the sha256 PyPI lists. The fix is applied with git, the
version gets a local label (3.0.1 becomes 3.0.1+whitehats.1), and a wheel is written directly: pure Python packages
need no build tool, so none of the library's own build code runs. The proof runs twice in a sealed container with no
network, on the original and on the fixed wheel: it must fail on the first and pass on the second, or nothing is kept.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..models import RegistryPackage
from .recipes import FLAW_SEEN, Recipe

log = logging.getLogger("whitehats.registry")

PYPI = "https://pypi.org/pypi"
LABEL = "whitehats"
PROOF_IMAGE = os.environ.get("WH_REGISTRY_PROOF_IMAGE", "python:3.12-slim")
PROOF_TIMEOUT_S = 120


class BuildRefused(Exception):
    """The build stopped; the message says why in words."""


@dataclass
class Ran:
    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def storage(org_id: int) -> Path:
    folder = DATA_DIR / "registry" / str(org_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def normalized(name: str) -> str:
    """The project name as package indexes compare it (PEP 503)."""
    out = name.lower()
    for mark in ("_", "."):
        out = out.replace(mark, "-")
    while "--" in out:
        out = out.replace("--", "-")
    return out


def next_version(db: Session, org_id: int, recipe: Recipe) -> str:
    made = db.scalars(select(RegistryPackage.version).where(
        RegistryPackage.org_id == org_id, RegistryPackage.name == recipe.name)).all()
    builds = [int(v.rsplit(".", 1)[1]) for v in made if v.startswith(f"{recipe.upstream_version}+{LABEL}.")]
    return f"{recipe.upstream_version}+{LABEL}.{max(builds, default=0) + 1}"


def build(db: Session, org_id: int, recipe: Recipe, *, by: str, runner=None) -> RegistryPackage:
    """Make, prove and keep the next fixed build. Raises BuildRefused, keeping nothing, when any step fails."""
    run = runner or run_proof
    version = next_version(db, org_id, recipe)
    with tempfile.TemporaryDirectory(prefix="wh-registry-") as work:
        root = Path(work)
        source = _upstream_source(recipe, root)
        original = root / "original"
        shutil.copytree(source, original)
        _apply(recipe.patch, source)
        _set_version(source / recipe.version_file, recipe.upstream_version, version)
        wheel = _wheel(recipe, source, version, root / "dist")
        before = run(recipe.proof, original_tree=original, wheel=None)
        after = run(recipe.proof, original_tree=None, wheel=wheel)
        if before.code != FLAW_SEEN:
            raise BuildRefused(f"The proof did not see the flaw in {recipe.name} {recipe.upstream_version}, so it proves "
                               f"nothing: {before.output.strip()[-300:]}")
        if not after.ok:
            raise BuildRefused(f"The fixed build did not pass its proof: {after.output.strip()[-300:]}")
        data = wheel.read_bytes()
        kept = storage(org_id) / wheel.name
        kept.write_bytes(data)
    row = RegistryPackage(
        org_id=org_id, name=recipe.name, version=version, upstream_version=recipe.upstream_version,
        upstream_sha256=recipe.upstream_sha256, advisories=list(recipe.advisories), summary=recipe.summary,
        fix_source=recipe.fix_source, patch=recipe.patch, filename=kept.name, sha256=hashlib.sha256(data).hexdigest(),
        size=len(data), status="proved", built_by=by,
        proof={"before": {"on": f"{recipe.name} {recipe.upstream_version}", "passed": before.ok, "said": before.output.strip()[-400:]},
               "after": {"on": f"{recipe.name} {version}", "passed": after.ok, "said": after.output.strip()[-400:]}})
    db.add(row)
    db.commit()
    log.info("registry build org=%s %s %s sha256=%s", org_id, recipe.name, version, row.sha256[:12])
    return row


def _upstream_source(recipe: Recipe, root: Path) -> Path:
    with urllib.request.urlopen(f"{PYPI}/{recipe.name}/{recipe.upstream_version}/json", timeout=30) as res:
        listing = json.load(res)
    sdist = next((u for u in listing["urls"] if u["packagetype"] == "sdist"), None)
    if sdist is None or sdist["digests"]["sha256"] != recipe.upstream_sha256:
        raise BuildRefused(f"PyPI no longer lists the {recipe.name} {recipe.upstream_version} source this fix was made for.")
    with urllib.request.urlopen(sdist["url"], timeout=60) as res:
        data = res.read()
    if hashlib.sha256(data).hexdigest() != recipe.upstream_sha256:
        raise BuildRefused("The downloaded source does not match the sha256 PyPI lists, so it was not used.")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(root / "unpacked", filter="data")  # refuses links and paths that leave the folder
    tops = list((root / "unpacked").iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise BuildRefused("The source release is not laid out as one folder.")
    return tops[0]


def _apply(patch: str, tree: Path) -> None:
    done = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=tree, input=patch, text=True,
                          capture_output=True, check=False)
    if done.returncode:
        raise BuildRefused(f"The fix does not apply to this source: {done.stderr.strip()[:300]}")


def _set_version(path: Path, upstream: str, version: str) -> None:
    text = path.read_text()
    stated = f'__version__ = "{upstream}"'
    if stated not in text:
        raise BuildRefused(f"{path.name} does not state version {upstream}.")
    path.write_text(text.replace(stated, f'__version__ = "{version}"'))


def _wheel(recipe: Recipe, source: Path, version: str, out: Path) -> Path:
    """A pure Python wheel: the import packages, plus the metadata from the release with the new version."""
    out.mkdir(parents=True, exist_ok=True)
    dist = f"{recipe.name}-{version}"
    info = f"{dist}.dist-info"
    metadata = (source / "PKG-INFO").read_text()
    if f"\nVersion: {recipe.upstream_version}\n" not in metadata:
        raise BuildRefused("The release's metadata does not state its version.")
    metadata = metadata.replace(f"\nVersion: {recipe.upstream_version}\n", f"\nVersion: {version}\n", 1)
    files: list[tuple[str, bytes]] = []
    for package in recipe.package_dirs:
        for path in sorted((source / package).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append((path.relative_to(source).as_posix(), path.read_bytes()))
    files.append((f"{info}/METADATA", metadata.encode()))
    files.append((f"{info}/WHEEL", b"Wheel-Version: 1.0\nGenerator: whitehats-registry\nRoot-Is-Purelib: true\nTag: py3-none-any\n"))
    if (source / "LICENSE").is_file():
        files.append((f"{info}/LICENSE", (source / "LICENSE").read_bytes()))
    record = "".join(f"{name},sha256={_digest(data)},{len(data)}\n" for name, data in files) + f"{info}/RECORD,,\n"
    files.append((f"{info}/RECORD", record.encode()))
    wheel = out / f"{dist}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            stamp = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # the same build gives the same bytes
            stamp.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(stamp, data)
    return wheel


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def run_proof(proof: str, *, original_tree: Path | None, wheel: Path | None) -> Ran:
    """Run the proof in a container with no network: on the original source, or with the fixed wheel installed."""
    with tempfile.TemporaryDirectory(prefix="wh-proof-") as work:
        folder = Path(work)
        (folder / "proof.py").write_text(proof)
        if original_tree is not None:
            shutil.copytree(original_tree, folder / "lib")
            command = "PYTHONPATH=/proof/lib python /proof/proof.py"
        else:
            assert wheel is not None
            shutil.copy(wheel, folder / wheel.name)
            command = (f"python -m venv /tmp/v && /tmp/v/bin/pip install -q --no-deps --no-index /proof/{wheel.name} "
                       "&& /tmp/v/bin/python /proof/proof.py")
        try:
            done = subprocess.run(["docker", "run", "--rm", "--quiet", "--network", "none", "--memory", "512m",
                                   "-v", f"{folder}:/proof:ro", PROOF_IMAGE, "sh", "-c", command],
                                  capture_output=True, text=True, timeout=PROOF_TIMEOUT_S, check=False)
        except FileNotFoundError as exc:
            raise BuildRefused("Docker is not available, so the proof could not run.") from exc
        except subprocess.TimeoutExpired:
            return Ran(124, f"the proof did not finish within {PROOF_TIMEOUT_S} s")
        return Ran(done.returncode, done.stdout + done.stderr)

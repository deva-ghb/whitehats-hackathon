"""Trivy in its own container (VS.5c): the known weaknesses in a container image's packages.

At a change, the base image each Dockerfile names (the last stage's `FROM`) is scanned, so a move to an old base is
seen on the pull request, before anything is built. The same scan runs on the images in a company's registry
(`scan_image`). Only weaknesses with a fixed version count: there is something to do about them. A change gets one
suspect per base image, naming how many and the worst few, since moving the base fixes them together.
"""

from __future__ import annotations

import base64
import json
import os
import posixpath
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ...services.repo_manifests import last_base
from ..container import parse_json, run_container, skipped
from ..cvss import base_score
from ..pins import TRIVY_IMAGE
from ..suspect import EngineRun, Evidence, Suspect, severity_from_cvss

RULE_ID = "trivy.base-image-weaknesses"
PULL_TIMEOUT = 300  # a download, not a scan: a large base image seen for the first time takes over a minute
PLATFORM = "linux/arm64"  # the proving ground's functions run on arm64; a registry image says its own
_WORD = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low", "UNKNOWN": "low"}


@dataclass(frozen=True)
class ImageWeakness:
    image: str
    digest: str
    package: str
    installed: str
    fixed: str
    advisory: str
    severity: str
    title: str
    cwe: tuple[str, ...]
    vex_status: str = ""  # "not_affected" when a VEX statement covers it (SCAN.5); it is kept, never dropped
    vex_reason: str = ""
    vex_source: str = ""


def _cache() -> Path:
    path = Path(os.environ.get("WH_TRIVY_CACHE", Path.home() / ".cache" / "whitehats" / "trivy"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _severity(vuln: dict) -> str:
    for source in (vuln.get("CVSS") or {}).values():
        score = base_score(str((source or {}).get("V3Vector") or ""))
        if score is not None:
            return severity_from_cvss(score)
    return _WORD.get(str(vuln.get("Severity") or "").upper(), "low")


def weaknesses(payload: object, image: str, digest: str = "") -> tuple[list[ImageWeakness], int]:
    """The fixable weaknesses Trivy reports for one image, and how many it reports with no fix yet. A saved image
    carries no registry digest, so the one Docker pulled is passed in."""
    if not isinstance(payload, dict):
        return [], 0
    digests = (payload.get("Metadata") or {}).get("RepoDigests") or []
    digest = str(digests[0]).rpartition("@")[2] if digests else digest
    found, unfixed = [], 0
    for result in payload.get("Results") or []:
        covered = [(item.get("Finding") or {}, item) for item in result.get("ExperimentalModifiedFindings") or []
                   if item.get("Type", "vulnerability") == "vulnerability"]  # what a VEX statement covered (SCAN.5)
        for vuln, vex in [(vuln, None) for vuln in result.get("Vulnerabilities") or []] + covered:
            if not vuln.get("FixedVersion"):
                unfixed += vex is None
                continue
            found.append(ImageWeakness(
                image=image, digest=digest, package=str(vuln.get("PkgName") or ""),
                installed=str(vuln.get("InstalledVersion") or ""), fixed=str(vuln["FixedVersion"]),
                advisory=str(vuln.get("VulnerabilityID") or ""), severity=_severity(vuln),
                title=str(vuln.get("Title") or vuln.get("VulnerabilityID") or "")[:200],
                cwe=tuple(item for item in vuln.get("CweIDs") or [] if str(item).startswith("CWE-")),
                vex_status=str((vex or {}).get("Status") or ""), vex_reason=str((vex or {}).get("Statement") or "")))
    found.sort(key=lambda item: (-["low", "medium", "high", "critical"].index(item.severity), item.package, item.advisory))
    return found, unfixed


Login = tuple[str, str, str]  # registry, user, password: a short-lived pull token (the AWS connector's registry_login)


def _engine() -> str:
    """The Docker engine the CLI uses now. A login keeps its own settings folder, and Docker Desktop names its engine in
    the usual one, so the address is passed on."""
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    found = subprocess.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                           capture_output=True, text=True, timeout=30, check=False)
    return found.stdout.strip() if found.returncode == 0 else ""


def _pulled(image: str, platform: str, timeout: int, into: Path, login: Login | None = None) -> tuple[str, str]:
    """Pull the image with Docker and save it to a file Trivy reads. Why not (empty when it worked), and the digest.

    Trivy can fetch an image itself, but its layer downloads broke off ("unexpected EOF") on the Lambda base images;
    Docker's pull retries. Reading a saved file means the scanner never gets access to the Docker engine itself.
    A private image is pulled with a login kept in a settings folder of its own, removed when the pull is done, so the
    token never lands in the machine's Docker settings or in the folder Trivy reads.
    """
    with tempfile.TemporaryDirectory(prefix="wh-docker-") as settings:
        env = None
        if login is not None:
            # The token goes straight into this folder's settings file. `docker login` is not used: with no credential
            # store named, it picks the machine's own (the macOS keychain through Docker Desktop) and the token would
            # outlive the scan there. A settings file that already holds an entry makes Docker read only that file.
            registry, user, password = login
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            (Path(settings) / "config.json").write_text(json.dumps({"auths": {registry: {"auth": token}}}), encoding="utf-8")
            env = {**os.environ, "DOCKER_CONFIG": settings}
            if host := _engine():
                env["DOCKER_HOST"] = host
        steps = (["docker", "pull", "--quiet", "--platform", platform, image],
                 ["docker", "save", "--platform", platform, "-o", str(into / "image.tar"), image])
        for command in steps:
            try:
                done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env, check=False)
            except FileNotFoundError:
                return "docker is not available", ""
            except subprocess.TimeoutExpired:
                return "timed out", ""
            if done.returncode != 0 and command[1] == "save" and "--platform" in done.stderr:
                done = subprocess.run([part for part in command if part not in ("--platform", platform)],
                                      capture_output=True, text=True, timeout=timeout, env=env, check=False)  # an older Docker
            if done.returncode != 0:
                return f"docker {command[1]} failed: {(done.stderr or done.stdout).strip()[:200]}", ""
        found = subprocess.run(["docker", "image", "inspect", "--format", "{{range .RepoDigests}}{{println .}}{{end}}", image],
                               capture_output=True, text=True, timeout=timeout, env=env, check=False)
    digests = found.stdout.split() if found.returncode == 0 else []
    return "", digests[0].rpartition("@")[2] if digests else ""


def scan_image(image: str, *, timeout: int, platform: str = PLATFORM,
               login: Login | None = None) -> tuple[list[ImageWeakness], int, EngineRun]:
    """Scan one image reference (a tag or a digest): Docker pulls it (with `login` for a private registry), Trivy reads it."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="wh-image-") as folder:
        problem, digest = _pulled(image, platform, max(timeout, PULL_TIMEOUT), Path(folder), login)
        if problem:
            return [], 0, EngineRun("trivy", TRIVY_IMAGE, time.perf_counter() - started, error=problem)
        args = ["image", "--quiet", "--format", "json", "--scanners", "vuln", "--input", "/image/image.tar"]
        result = run_container(TRIVY_IMAGE, args, Path(folder), network=True, timeout=timeout,
                               extra_mounts=[(_cache(), "/root/.cache/trivy", True), (Path(folder), "/image", False)])
    seconds = time.perf_counter() - started
    if result.timed_out:
        return [], 0, EngineRun("trivy", TRIVY_IMAGE, seconds, error="timed out")
    if result.exit_code == 127:
        return [], 0, EngineRun("trivy", TRIVY_IMAGE, seconds, error="docker is not available")
    payload = parse_json(result.stdout)
    if not isinstance(payload, dict):
        return [], 0, EngineRun("trivy", TRIVY_IMAGE, seconds, error=f"no JSON (exit {result.exit_code})")
    found, unfixed = weaknesses(payload, image, digest)
    return found, unfixed, EngineRun("trivy", TRIVY_IMAGE, seconds)


def inventory(image: str, *, timeout: int, platform: str = PLATFORM,
              login: Login | None = None) -> tuple[dict | None, str, EngineRun]:
    """A CycloneDX inventory of every part in the image (SCAN.2), the digest Docker pulled, and how the run went."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="wh-image-") as folder:
        problem, digest = _pulled(image, platform, max(timeout, PULL_TIMEOUT), Path(folder), login)
        if problem:
            return None, "", EngineRun("trivy", TRIVY_IMAGE, time.perf_counter() - started, error=problem)
        args = ["image", "--quiet", "--format", "cyclonedx", "--input", "/image/image.tar"]
        result = run_container(TRIVY_IMAGE, args, Path(folder), network=True, timeout=timeout,
                               extra_mounts=[(_cache(), "/root/.cache/trivy", True), (Path(folder), "/image", False)])
    seconds = time.perf_counter() - started
    document = parse_json(result.stdout)
    if result.timed_out or not isinstance(document, dict):
        error = "timed out" if result.timed_out else f"no inventory (exit {result.exit_code})"
        return None, digest, EngineRun("trivy", TRIVY_IMAGE, seconds, error=error)
    return document, digest, EngineRun("trivy", TRIVY_IMAGE, seconds)


def scan_inventory(document: dict, image: str, digest: str, *, timeout: int,
                   vex: dict | None = None) -> tuple[list[ImageWeakness], int, EngineRun]:
    """The known weaknesses of a stored inventory: no pull, so a daily rescan of an unchanged image is quick (SCAN.2).

    `vex` is an OpenVEX document Trivy reads as input (SCAN.5); what it covers comes back marked "not_affected" with
    its justification (`--show-suppressed`), never left out."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="wh-inventory-") as folder:
        (Path(folder) / "inventory.cdx.json").write_text(json.dumps(document), encoding="utf-8")
        args = ["sbom", "--quiet", "--format", "json", "--scanners", "vuln", "/inventory/inventory.cdx.json"]
        if vex and vex.get("statements"):
            (Path(folder) / "vex.json").write_text(json.dumps(vex), encoding="utf-8")
            args[1:1] = ["--vex", "/inventory/vex.json", "--show-suppressed"]
        result = run_container(TRIVY_IMAGE, args, Path(folder), network=True, timeout=timeout,
                               extra_mounts=[(_cache(), "/root/.cache/trivy", True), (Path(folder), "/inventory", False)])
    seconds = time.perf_counter() - started
    payload = parse_json(result.stdout)
    if result.timed_out or not isinstance(payload, dict):
        error = "timed out" if result.timed_out else f"no JSON (exit {result.exit_code})"
        return [], 0, EngineRun("trivy", TRIVY_IMAGE, seconds, error=error)
    found, unfixed = weaknesses(payload, image, digest)
    return found, unfixed, EngineRun("trivy", TRIVY_IMAGE, seconds)


def _dockerfiles(root: Path, paths: list[str] | None) -> list[str]:
    if paths is not None:
        return [path for path in paths if posixpath.basename(path) == "Dockerfile" and (root / path).is_file()]
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("Dockerfile")
                  if not skipped(path.relative_to(root).as_posix()))


def _from_line(text: str, base: str) -> int:
    lines = text.splitlines()
    for number in range(len(lines), 0, -1):  # the last stage's FROM is the base the image runs on
        if base in lines[number - 1] and lines[number - 1].strip().upper().startswith("FROM"):
            return number
    return 1


def _image_suspect(relative: str, line: int, base: str, found: list[ImageWeakness]) -> Suspect:
    """One suspect per base image, not one per weakness: moving the base fixes them all at once, so it is one decision
    for a person (and one confirmation, not a hundred). It carries the worst severity; the worst few are named."""
    counts = {level: sum(item.severity == level for item in found) for level in ("critical", "high", "medium", "low")}
    split = ", ".join(f"{count} {level}" for level, count in counts.items() if count)
    worst = found[0]
    named = "; ".join(f"{item.advisory} {item.package} {item.installed}, fixed in {item.fixed}" for item in found[:5])
    resource = f"{base.partition('@')[0]}@{worst.digest}" if worst.digest else base
    return Suspect(
        engine="trivy", rule_id=RULE_ID, evidence=Evidence(relative, line, line, resource=resource),
        title=f"{len(found)} known weaknesses with a fix in the base image {base.partition('@')[0]} ({split}); "
              f"worst: {worst.package} {worst.installed}: {worst.title}",
        severity=worst.severity, cwe=sorted({cwe for item in found for cwe in item.cwe})[:5] or ["CWE-1104"],
        note=f"Worst first: {named}.")


def scan(root: Path, paths: list[str] | None, *, timeout: int) -> tuple[list[Suspect], EngineRun]:
    """Scan the base image of each Dockerfile the change touches (the whole tree on a full run)."""
    files = _dockerfiles(root, paths)
    if not files:
        return [], EngineRun("trivy", TRIVY_IMAGE, 0.0)
    started, suspects, errors = time.perf_counter(), [], []
    for relative in files:
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
        base = last_base(text)
        if not base or base.lower() == "scratch":
            continue
        found, _unfixed, run = scan_image(base, timeout=timeout)  # what has no fix yet is counted, not reported
        if run.error:
            errors.append(f"{relative}: {run.error}")
            continue
        if found:
            suspects.append(_image_suspect(relative, _from_line(text, base), base, found))
    return suspects, EngineRun("trivy", TRIVY_IMAGE, time.perf_counter() - started, error="; ".join(errors))


scan.name = "trivy"  # type: ignore[attr-defined]
scan.image = TRIVY_IMAGE  # type: ignore[attr-defined]

"""SCAN.2: every part of a service, direct and hidden, per deployed version.

A service's parts come from two places: the lock file its code pins (the repository import keeps them on the node as
`packages`), and the image it runs, is built from or, for a zip function, the AWS runtime it runs on. An image's parts
are a CycloneDX inventory made by Trivy, once per digest, and kept: a later scan of the same version reads the stored
inventory instead of pulling the image again. A part the service's manifest does not name is hidden.

Weaknesses: the language parts are matched against our OSV.dev copy (KB.1); the image's operating system parts by
Trivy reading the stored inventory (image_scans).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from ..decisions_api.scanners import trivy
from ..knowledge.versions import normalise
from ..knowledge.watch import used_packages
from ..models import Inventory, utcnow

MAKE_TIMEOUT = 600
RETAG_AFTER = timedelta(days=1)  # an image by tag may move; one by digest never does
MADE_BY = f"trivy {trivy.TRIVY_IMAGE.rpartition(':')[2]} (from the image)"
# The image AWS publishes for each managed runtime: a zip function runs on it, so its parts are the function's too.
RUNTIMES = {"python": "public.ecr.aws/lambda/python:{version}-arm64", "nodejs": "public.ecr.aws/lambda/nodejs:{version}-arm64"}
_PURL_ECOSYSTEMS = {"pypi": "PyPI", "npm": "npm", "golang": "Go", "maven": "Maven", "gem": "RubyGems", "cargo": "crates.io"}

Make = Callable[..., tuple[dict | None, str, object]]


def runtime_image(runtime: str) -> str:
    """The AWS runtime image for "python3.12" or "nodejs20.x"; empty when there is none to name."""
    for language, template in RUNTIMES.items():
        if runtime.startswith(language):
            version = runtime[len(language):].removesuffix(".x")
            if version and version.replace(".", "").isdigit():
                return template.format(version=version)
    return ""


def image_of(node: dict) -> tuple[str, str]:
    """(image, how the service uses it): the image it runs, else the one it is built from, else its AWS runtime."""
    properties = node.get("properties") or {}
    if properties.get("image"):
        return str(properties["image"]), "runs"
    if properties.get("base_image"):
        return str(properties["base_image"]), "is built from"
    if properties.get("platform") == "aws_lambda" and properties.get("package_type") != "image":
        runtime = runtime_image(str(properties.get("runtime") or ""))
        if runtime:
            return runtime, "runs on the AWS runtime"
    return "", ""


def stored(db: Session, image: str, *, make: Make | None = trivy.inventory, login=None, now=utcnow) -> Inventory | None:
    """The image's inventory: the stored one when it still describes the image, else a new one. None when it cannot be
    made. With `make=None` nothing is made or written: what is stored, or None."""
    row = db.get(Inventory, image)
    fresh = row is not None and row.made_at is not None and (
        "@sha256:" in image or now() - row.made_at < RETAG_AFTER)
    if make is None:
        return row if row is not None and row.made_at is not None else None
    if not fresh:
        row = row or Inventory(image=image)
        db.add(row)
        document, digest, run = make(image, timeout=MAKE_TIMEOUT, login=login)
        error = getattr(run, "error", "")
        if error or document is None:
            row.last_error = (error or "no inventory")[:2000]
            db.commit()
            return None
        row.document, row.digest, row.made_at, row.made_by, row.last_error = document, digest, now(), MADE_BY, ""
    row.last_used_at = now()
    db.commit()
    return row


def parts(document: dict) -> list[dict]:
    """{name, version, ecosystem, purl} for each package in a CycloneDX inventory (the image itself and its OS left out)."""
    out = []
    for component in document.get("components") or []:
        purl = str(component.get("purl") or "")
        if not purl.startswith("pkg:"):
            continue
        kind = purl[4:].split("/", 1)[0]
        ecosystem = _PURL_ECOSYSTEMS.get(kind, kind)
        out.append({"name": str(component.get("name") or ""), "version": str(component.get("version") or ""),
                    "ecosystem": ecosystem, "purl": purl})
    return sorted(out, key=lambda item: (item["ecosystem"], item["name"].lower(), item["version"]))


def for_node(db: Session, node: dict, *, make: Make | None = trivy.inventory, login=None, now=utcnow) -> dict:
    """Every part of the service, each saying where it came from and whether the manifest names it."""
    properties = node.get("properties") or {}
    chosen = [str(name) for name in properties.get("direct_dependencies") or []]  # what the manifest names
    found: dict[tuple[str, str, str], dict] = {}
    for ecosystem, name, version in used_packages(node):
        found[(ecosystem, normalise(ecosystem, name), version)] = {
            "name": name, "version": version, "ecosystem": ecosystem, "purl": "", "from": "lock file",
            "hidden": normalise(ecosystem, name) not in {normalise(ecosystem, item) for item in chosen}}
    image, how = image_of(node)
    row = stored(db, image, make=make, login=login, now=now) if image else None
    if row is not None:
        for part in parts(row.document):
            key = (part["ecosystem"], normalise(part["ecosystem"], part["name"]), part["version"])
            if key in found:
                continue
            found[key] = {**part, "from": f"image {image}",
                          "hidden": normalise(part["ecosystem"], part["name"]) not in {
                              normalise(part["ecosystem"], item) for item in chosen}}
    rows = sorted(found.values(), key=lambda item: (item["hidden"], item["ecosystem"], item["name"].lower()))
    return {"service": str(node.get("name", node["id"])), "image": image, "image_use": how,
            "digest": row.digest if row is not None else "", "made_at": row.made_at.isoformat() if row and row.made_at else "",
            "made_by": row.made_by if row is not None else "", "parts": rows,
            "counts": {"parts": len(rows), "hidden": sum(item["hidden"] for item in rows)}}

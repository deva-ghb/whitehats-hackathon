"""The one lookup the agents use for advisory facts (KB.1): by package and version, by id or alias, and what changed.

Every answer carries the record's modified time, where it came from and the licence, so whatever an agent states
can be cited. An advisory id, an affected range or a fixed version an agent states must come from here: the model
never supplies them from memory (see `cited_ids` and `unknown_ids`).
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Advisory, AdvisoryAlias, AdvisoryPackage
from .osv import ATTRIBUTION, LICENCE, SOURCE, when
from .versions import affects, normalise

# The id families OSV and its sources use. A word that starts with one of these and has a digit is an advisory id.
ID_PREFIXES = ("CVE-", "GHSA-", "PYSEC-", "OSV-", "MAL-", "RUSTSEC-", "GO-", "SNYK-", "DSA-", "USN-", "BIT-")


def facts(advisory: Advisory, package: tuple[str, str] | None = None) -> dict:
    """The record's facts, with the affected ranges and fixed versions for one package when it is named."""
    record = advisory.record or {}
    entries = record.get("affected") or []
    if package is not None:
        ecosystem, name = package
        entries = [item for item in entries if isinstance(item.get("package"), dict)
                   and item["package"].get("ecosystem") == ecosystem
                   and normalise(ecosystem, str(item["package"].get("name", ""))) == normalise(ecosystem, name)]
    affected, fixed, functions = [], [], []
    for item in entries:
        ranges = [{"type": span.get("type"), "events": span.get("events") or []} for span in item.get("ranges") or []]
        affected.append({"package": item.get("package"), "ranges": ranges, "versions": item.get("versions") or []})
        for span in ranges:
            fixed.extend(str(event["fixed"]) for event in span["events"] if "fixed" in event)
        for place in ("ecosystem_specific", "database_specific"):
            imports = (item.get(place) or {}).get("affected_functions") or (item.get(place) or {}).get("imports") or []
            functions.extend(str(entry) if not isinstance(entry, dict) else entry.get("path", "") for entry in imports)
    return {
        "id": advisory.id, "aliases": list(advisory.aliases or []), "summary": advisory.summary,
        "details": str(record.get("details") or ""), "severity": record.get("severity") or [],
        "database_severity": (record.get("database_specific") or {}).get("severity"),
        "affected": affected, "fixed_versions": sorted(set(fixed)), "affected_functions": sorted(set(filter(None, functions))),
        "references": [ref.get("url") for ref in record.get("references") or [] if ref.get("url")],
        "published": advisory.published, "modified": advisory.modified, "withdrawn": advisory.withdrawn,
        "source": SOURCE, "url": f"https://osv.dev/vulnerability/{advisory.id}", "licence": LICENCE,
        "attribution": ATTRIBUTION,
    }


def by_package(db: Session, ecosystem: str, name: str, version: str | None = None) -> list[dict]:
    """Every advisory naming this package, or only those covering `version` when it is given. Withdrawn ones are left out."""
    wanted = normalise(ecosystem, name)
    ids = select(AdvisoryPackage.advisory_id).where(AdvisoryPackage.ecosystem == ecosystem, AdvisoryPackage.name == wanted)
    found = []
    for advisory in db.scalars(select(Advisory).where(Advisory.id.in_(ids)).order_by(Advisory.id)):
        if advisory.withdrawn:
            continue
        entries = [item for item in (advisory.record or {}).get("affected") or [] if isinstance(item.get("package"), dict)
                   and item["package"].get("ecosystem") == ecosystem
                   and normalise(ecosystem, str(item["package"].get("name", ""))) == wanted]
        if version is None or any(affects(ecosystem, item, version) for item in entries):
            found.append(facts(advisory, (ecosystem, name)))
    return found


def by_id(db: Session, advisory_id: str) -> dict | None:
    """One advisory by its own id or by any alias (a CVE finds the GHSA that names it)."""
    alias = select(AdvisoryAlias.advisory_id).where(AdvisoryAlias.alias == advisory_id)
    advisory = db.scalar(select(Advisory).where(or_(Advisory.id == advisory_id, Advisory.id.in_(alias))).order_by(Advisory.id))
    return facts(advisory) if advisory is not None else None


def changed_since(db: Session, since: str, ecosystem: str | None = None) -> list[dict]:
    """Advisories added or changed after `since` (OSV's time text), oldest change first: id, modified, ecosystem."""
    after = when(since)
    # The time text sorts by its first 19 characters (to the second); the exact comparison then reads the fraction.
    query = select(Advisory.id, Advisory.modified, Advisory.ecosystem).where(Advisory.modified >= since.strip()[:19])
    if ecosystem:
        query = query.where(Advisory.ecosystem == ecosystem)
    rows = [row for row in db.execute(query) if row.modified and when(row.modified) > after]
    return [{"id": row.id, "modified": row.modified, "ecosystem": row.ecosystem}
            for row in sorted(rows, key=lambda row: when(row.modified))]


def cited_ids(text: str) -> list[str]:
    """The advisory ids a piece of text names, in order, each once. Plain word splitting, no pattern language."""
    found = []
    for word in text.replace("(", " ").replace(")", " ").replace(",", " ").replace(";", " ").split():
        word = word.strip(".:'\"`[]?!")
        if word.upper().startswith(ID_PREFIXES) and _looks_like_id(word) and word not in found:
            found.append(word)
    return found


def _looks_like_id(word: str) -> bool:
    """GHSA ids are three groups of four letters or digits (all letters is possible); the others carry a number."""
    parts = word.split("-")
    if parts[0].upper() == "GHSA":
        return len(parts) == 4 and all(len(part) == 4 and part.isalnum() for part in parts[1:])
    return any(char.isdigit() for char in word)


def unknown_ids(db: Session, text: str) -> list[str]:
    """The advisory ids in `text` that the knowledge base does not hold: an agent made them up or misremembered."""
    return [advisory_id for advisory_id in cited_ids(text) if by_id(db, advisory_id) is None]

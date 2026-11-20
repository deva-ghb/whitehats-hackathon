"""A new or changed advisory for a package a clone uses becomes a finding on that node, with no code change (KB.1).

Each service node can carry `packages`, the exact versions its code uses (`PyPI:pyyaml==5.3.1`), read from the
repository's manifests and lock files. After a refresh, every advisory added or changed is matched against those, in
the latest version of every clone; a match is a live signal on the node in the clone's project, once per advisory and
node while it stays open. This is the "new weakness feeds" trigger of P7.3.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..decisions_api.cvss import base_score
from ..decisions_api.suspect import severity_from_cvss
from ..models import Advisory, AdvisoryPackage, Clone, Signal
from ..services.clones import read_clone
from .lookup import facts
from .osv import ATTRIBUTION
from .versions import affects, normalise

log = logging.getLogger("whitehats.knowledge")

SOURCE = "whitehats:advisory"
_WORDS = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium", "LOW": "low"}


def severity(record: dict) -> str:
    """The record's own CVSS score when it gives one, else the source's word, else medium."""
    for entry in record.get("severity") or []:
        score = base_score(str((entry or {}).get("score") or ""))
        if score is not None:
            return severity_from_cvss(score)
    return _WORDS.get(str((record.get("database_specific") or {}).get("severity") or "").upper(), "medium")


def used_packages(node: dict) -> list[tuple[str, str, str]]:
    """(ecosystem, name, version) for each exact version the node's code uses."""
    found = []
    for item in (node.get("properties") or {}).get("packages") or []:
        ecosystem, _, pin = str(item).partition(":")
        name, _, version = pin.partition("==")
        if ecosystem and name and version:
            found.append((ecosystem, name, version))
    return found


def raise_findings(db: Session, changed: list[str]) -> int:
    """Signals for the advisories in `changed` that cover a package version some clone's node uses. Returns how many."""
    if not changed:
        return 0
    names = {(row.ecosystem, row.name) for row in db.scalars(
        select(AdvisoryPackage).where(AdvisoryPackage.advisory_id.in_(changed)))}
    if not names:
        return 0
    raised = 0
    for clone in db.scalars(select(Clone).order_by(Clone.id)):
        document = read_clone(db, clone.org_id, clone.id)
        for node in (document or {}).get("nodes", []):
            for ecosystem, name, version in used_packages(node):
                if (ecosystem, normalise(ecosystem, name)) not in names:
                    continue
                for advisory in _covering(db, changed, ecosystem, name, version):
                    raised += _signal(db, clone, node, advisory, ecosystem, name, version)
    db.commit()
    if raised:
        log.info("knowledge findings raised=%d from changed=%d", raised, len(changed))
    return raised


def _covering(db: Session, changed: list[str], ecosystem: str, name: str, version: str) -> list[Advisory]:
    wanted = normalise(ecosystem, name)
    ids = select(AdvisoryPackage.advisory_id).where(
        AdvisoryPackage.ecosystem == ecosystem, AdvisoryPackage.name == wanted, AdvisoryPackage.advisory_id.in_(changed))
    out = []
    for advisory in db.scalars(select(Advisory).where(Advisory.id.in_(ids)).order_by(Advisory.id)):
        entries = [item for item in (advisory.record or {}).get("affected") or [] if isinstance(item.get("package"), dict)
                   and item["package"].get("ecosystem") == ecosystem
                   and normalise(ecosystem, str(item["package"].get("name", ""))) == wanted]
        if not advisory.withdrawn and any(affects(ecosystem, item, version) for item in entries):
            out.append(advisory)
    return out


def _signal(db: Session, clone: Clone, node: dict, advisory: Advisory, ecosystem: str, name: str, version: str) -> int:
    key = f"{advisory.id}|{node['id']}"[:240]
    open_one = db.scalar(select(Signal.id).where(
        Signal.project_id == clone.project_id, Signal.source == SOURCE, Signal.fingerprint == key, Signal.closed_at.is_(None)))
    if open_one is not None:
        return 0
    known = facts(advisory, (ecosystem, name))
    fixed = ", ".join(known["fixed_versions"]) or "no fixed version yet"
    also = f" ({', '.join(known['aliases'])})" if known["aliases"] else ""
    db.add(Signal(
        org_id=clone.org_id, project_id=clone.project_id, severity=severity(advisory.record or {}),
        title=f"{advisory.id}{also} in {name} {version}"[:240],
        detail=(f"{node.get('name', node['id'])} uses {ecosystem} {name} {version}, which {advisory.id} covers. "
                f"{known['summary']} Fixed in: {fixed}. Published {known['published'] or 'unknown'}, record modified "
                f"{known['modified']}. {known['url']}\n{ATTRIBUTION}"),
        entity=str(node.get("name", node["id"]))[:160], resource=known["url"][:300], source=SOURCE, origin="live",
        clone_id=clone.id, node_key=str(node["id"])[:80], fingerprint=key))
    return 1

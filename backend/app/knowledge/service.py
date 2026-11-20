"""Refresh the knowledge base and act on what changed (KB.1): new findings on nodes, and the Runner's offline copy."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from . import osv, watch

RUNNER_COPY = DATA_DIR / "knowledge.db"


def configured() -> list[str]:
    return [name.strip() for name in settings.knowledge_ecosystems.split(",") if name.strip() in osv.ECOSYSTEMS]


def refresh_and_watch(db: Session, ecosystems: Iterable[str] | None = None, *, fetch: osv.Fetch = osv.http_fetch) -> dict:
    """One refresh: download what changed, raise findings for it, and rewrite the Runner's copy when anything did."""
    done = osv.refresh(db, list(ecosystems) if ecosystems is not None else configured(), fetch=fetch)
    changed = [advisory_id for result in done for advisory_id in result.changed]
    raised = watch.raise_findings(db, changed)
    if changed:
        osv.export_copy(db, RUNNER_COPY)
    return {"ecosystems": [{"ecosystem": r.ecosystem, "fetched": r.fetched, "stored": r.stored, "error": r.error}
                           for r in done], "changed": len(changed), "findings": raised}

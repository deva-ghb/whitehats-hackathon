"""Connectors: how WhiteHats reads a company's systems, each one held to the same contract (checklist 3A).

Connectors are moved onto this framework one at a time; the rest still live in services/cloudlogs.py.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Connector
from ..security import unseal
from .aws import Aws
from .base import Source, SourceError
from .cloudwatch import CloudWatch
from .github import GitHub

REGISTRY: dict[str, type[Source]] = {Aws.key: Aws, CloudWatch.key: CloudWatch, GitHub.key: GitHub}
# The first prototype connects AWS and GitHub only (checklist section P). Every other connector is
# "coming soon": shown, but it cannot be saved, synced or read.
PROTOTYPE_CONNECTORS = frozenset({"aws", "cloudwatch", "github"})

__all__ = ["PROTOTYPE_CONNECTORS", "REGISTRY", "Source", "SourceError", "for_row"]


def for_row(db: Session, row: Connector) -> Source | None:
    """The framework connector for a stored row, or None while that connector is not on the framework yet."""
    kind = REGISTRY.get(row.key)
    if kind is None:
        return None
    return kind(row.config or {}, lambda: unseal(row.secret_enc, db, row.org_id))

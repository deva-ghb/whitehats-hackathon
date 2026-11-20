"""Scan the repositories a project's GitHub connection reaches, as the source of an import (P5.2).

The scan itself is repo_scan.py. This is the part that finds the connection, honours the project's scope and turns
what the GitHub connector cannot do into a sentence a person can act on. Only reads.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..connectors import routing
from ..connectors.base import SourceError
from ..connectors.github import GitHub
from ..models import Connector
from .imports import ImportRefused
from .repo_scan import Found, scan_repositories


def connection(db: Session, org_id: int, project_id: int) -> tuple[GitHub, list[str] | None]:
    """The account's GitHub connection as this project reaches it, and the repositories its scope names (None: all)."""
    row = db.scalar(select(Connector).where(Connector.org_id == org_id, Connector.key == "github"))
    if row is None or row.status in ("available", "coming_soon") or not (row.config or {}).get("installation_id"):
        raise ImportRefused("GitHub is not connected yet. Install the WhiteHats GitHub App on the repositories, then read them.")
    scopes = [scope for project, scope in routing.attachments(db, row) if project == project_id]
    if not scopes:
        raise ImportRefused("GitHub is connected for this account but not attached to this project. "
                            "Attach it, choosing the repositories this project holds.")
    source = connectors.for_row(db, row)
    assert isinstance(source, GitHub)
    return source, scopes[0].get("repos")


def scan_project(db: Session, org_id: int, project_id: int, environment: str) -> tuple[Found, str]:
    """What the project's repositories hold, and a label for the import."""
    source, only = connection(db, org_id, project_id)
    try:
        found = scan_repositories(source, environment=environment, only=only)
    except SourceError as exc:
        raise ImportRefused(str(exc)) from None
    names = sorted(node["properties"]["repo"] for node in found.nodes if node["type"] == "repo.github@1")
    if not names:
        raise ImportRefused("The GitHub App can see none of this project's repositories, so there is nothing to read.")
    return found, f"What {', '.join(names)} holds"


def readiness(db: Session, org_id: int, project_id: int) -> dict:
    """Whether a scan can start for this project, and if not, why in words. Makes no call to GitHub."""
    try:
        _source, only = connection(db, org_id, project_id)
    except ImportRefused as exc:
        return {"ready": False, "why": exc.problems[0], "repos": []}
    return {"ready": True, "why": "", "repos": only or []}

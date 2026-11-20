"""The WhiteHat plug-in contract.

A WhiteHat is a small specialist. It declares which data it needs and
implements one or more hooks. The platform calls a hook only for tenants that
have that WhiteHat switched on, and only with data the tenant already sent.
Hooks never call AI models and never touch customer systems: they read data and
raise signals through the context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Agent, Signal

# Data a WhiteHat can subscribe to, and where it comes from.
DATA_SOURCES = {
    "logs": "Log collectors and cloud log connectors",
    "network": "Network analyzers",
    "processes": "Process monitors",
    "cloud_audit": "AWS CloudTrail via CloudWatch",
    "code": "GitHub connector and Fix Factory",
    "identity": "Okta, Entra ID or Google Workspace connector",
}


@dataclass
class Context:
    """What a hook may use: the database session, the tenant, and a way to raise signals."""

    db: Session
    org_id: int
    agent: Agent
    raised: list[Signal] = field(default_factory=list)

    def signal(self, severity: str, title: str, detail: str = "", entity: str = "", mitre: str = "",
               dedupe_hours: int = 6, resource: str = "") -> Signal | None:
        """Raise a signal unless the same title is still open from recently for the same resource (a second bucket made
        public is its own signal). One that was closed does not count: a change undone and then made again is a new
        problem, and must not hide behind the old one. Critical and high ones also alert, unless the WhiteHat is
        learning: then the signal is only kept, to show what it would have raised (GL.2)."""
        since = utcnow() - timedelta(hours=dedupe_hours)
        exists = self.db.scalar(select(Signal.id).where(Signal.org_id == self.org_id, Signal.title == title[:240],
                                                        Signal.resource == resource[:300], Signal.created_at >= since,
                                                        Signal.closed_at.is_(None)))
        if exists or any(r.title == title[:240] and r.resource == resource[:300] for r in self.raised):
            return None  # raised before, or earlier in this same batch (not yet written)
        s = Signal(org_id=self.org_id, project_id=self.agent.project_id, agent_id=self.agent.id,
                   severity=severity, title=title[:240], resource=resource[:300],
                   detail=detail[:4000], entity=entity[:160], mitre=mitre, source=f"whitehat:{self.agent.key}",
                   learning=self.agent.status == "learning")
        self.db.add(s)
        self.agent.last_signal_at = utcnow()
        self.raised.append(s)
        if severity in ("critical", "high") and not s.learning:
            from ..services.notify import alert

            alert(self.db, self.org_id, f"{severity.upper()}: {title[:120]}", f"{detail[:600]}\n\nSeen by the "
                  f"{self.agent.name} WhiteHat.")
        return s

    def close(self, title: str, resource: str, reason: str) -> int:
        """Close this WhiteHat's open signals with this title on this resource: the change they report was undone, as
        the cloud's own record shows. Returns how many were closed."""
        if not resource:
            return 0
        rows = list(self.db.scalars(select(Signal).where(Signal.org_id == self.org_id, Signal.title == title[:240],
                                                         Signal.resource == resource[:300],
                                                         Signal.closed_at.is_(None))))
        for row in rows:
            row.closed_at, row.closed_reason = utcnow(), reason[:60]
        return len(rows)


class WhiteHat:
    key: str = ""
    name: str = ""
    domain: str = ""
    description: str = ""
    data_sources: tuple[str, ...] = ()
    # False for catalog entries whose data source is not built yet: they can be listed, not switched on.
    available: bool = True
    unavailable_reason: str = ""
    # None until coverage can be computed. A number here is a measurement, never a fixed stamp.
    coverage: int | None = None
    # Optional labels when the shared DATA_SOURCES text claims more than this WhiteHat reads.
    source_labels: ClassVar[dict[str, str]] = {}

    # Hooks. Override the ones this WhiteHat needs.
    def on_log_batch(self, ctx: Context, source_host: str, lines: list[dict]) -> None:
        """A batch of raw log lines, as ingested (before shape mining)."""

    def on_log_window(self, ctx: Context, items: list[dict]) -> None:
        """Scored log shapes for a digest window (see services/loganalyzer.score_window)."""

    def on_network_findings(self, ctx: Context, host: str, new_findings: list) -> None:
        """Network findings that appeared for the first time in the latest snapshot."""

    def on_processes(self, ctx: Context, host: str, processes: list[dict]) -> None:
        """A snapshot of the busiest processes on a host."""

    def info(self) -> dict:
        labels = self.source_labels
        return {"key": self.key, "name": self.name, "domain": self.domain, "description": self.description,
                "data_sources": [{"key": d, "label": labels.get(d, DATA_SOURCES[d])} for d in self.data_sources],
                "available": self.available, "unavailable_reason": self.unavailable_reason,
                "coverage": self.coverage}

"""GitHub webhook deliveries: checked, kept once, and fed in like a poll (CN.1).

One WhiteHats GitHub App serves every account, so a delivery names its installation and
lands only on the account whose GitHub connection holds that installation.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..connectors import bookmarks
from ..connectors.base import SourceError
from ..connectors.github import GitHub
from ..connectors.github_events import absorb
from ..connectors.webhooks import remember_delivery, signature_matches
from ..db import utcnow
from ..models import Connector
from . import cloudlogs

log = logging.getLogger("whitehats.github.webhook")
# SQLite has no row locks; one process takes deliveries one at a time so a redelivery racing the
# original cannot both pass the delivery check. Postgres also locks the row (with_for_update).
_one_at_a_time = threading.Lock()


class Refused(Exception):
    """A delivery that is not provably from GitHub, answered with this status and reason."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def webhook_secret() -> str:
    return settings.github_webhook_secret


def receive(db: Session, headers: Mapping[str, str], body: bytes) -> dict:
    """Check, de-duplicate and record one delivery. Refused when it is not provably from GitHub."""
    lowered = {k.lower(): v for k, v in headers.items()}
    secret = webhook_secret()
    if not secret:
        raise Refused(503, "GitHub webhooks are not set up on this server.")
    if not signature_matches(secret, body, lowered.get("x-hub-signature-256", "")):
        log.warning("github webhook refused reason=signature delivery=%s", lowered.get("x-github-delivery", "-"))
        raise Refused(401, "The webhook signature does not match.")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise Refused(400, "The webhook body is not JSON.") from exc
    installation = str((payload.get("installation") or {}).get("id") or "")
    delivery = lowered.get("x-github-delivery", "")
    with _one_at_a_time:
        rows = _connections(db, installation)
        results = [_take(db, row, lowered, body, delivery, secret) for row in rows]
        db.commit()
    log.info("github webhook event=%s delivery=%s installation=%s accounts=%d", lowered.get("x-github-event", "-"),
             delivery or "-", installation or "-", len(rows))
    return {"accounts": len(rows), "read": sum(r for r in results if r > 0),
            "duplicate": bool(results) and all(r < 0 for r in results)}


def _connections(db: Session, installation: str) -> list[Connector]:
    if not installation:
        return []
    rows = db.scalars(select(Connector).where(Connector.key == GitHub.key).with_for_update()).all()
    return [row for row in rows if str((row.config or {}).get("installation_id") or "") == installation]


def _take(db: Session, row: Connector, headers: Mapping[str, str], body: bytes, delivery: str, secret: str) -> int:
    """Lines read from this delivery for this account, or -1 when it was already taken."""
    state = dict(row.state or {})
    seen = remember_delivery(list(state.get("deliveries") or []), delivery)
    if seen is None:
        return -1
    try:
        lines = GitHub(row.config or {}, webhook_secret=lambda: secret).handle_event(headers, body)
    except SourceError as exc:
        log.warning("github webhook not read connector=%s: %s", row.id, exc)
        return 0
    fresh, mark = absorb(bookmarks.load(row), lines)
    bookmarks.save(row, mark)
    row.state = {**row.state, "deliveries": seen,
                 "last_event": {"event": headers.get("x-github-event", ""), "delivery": delivery,
                                "at": utcnow().isoformat(), "lines": len(fresh)}}
    cloudlogs.record_lines(db, row, fresh, source="webhook")
    return len(fresh)

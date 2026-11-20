"""Pushed events: check the signature, and keep each delivery once.

GitHub sends `sha256=` plus the hex HMAC-SHA256 of the raw body. The GitHub connector and
its webhook route (CN.1) call these helpers. A source that is only polled does not.
"""

from __future__ import annotations

import hashlib
import hmac


def signature_matches(secret: str, body: bytes, header: str) -> bool:
    """True only when the header is the sha256 HMAC of this exact body."""
    prefix = "sha256="
    if not header.startswith(prefix) or len(header) != len(prefix) + 64:
        return False
    try:
        given = header[len(prefix):].encode("ascii")
    except UnicodeEncodeError:
        return False  # compare_digest raises on non-ASCII text, which would answer 500 instead of 401
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest().encode("ascii")
    return hmac.compare_digest(digest, given)


# GitHub redelivers within days, not thousands of deliveries later; the list stays small in its JSON column.
DELIVERIES_KEPT = 1000


def remember_delivery(seen: list[str], delivery_id: str, keep: int = DELIVERIES_KEPT) -> list[str] | None:
    """The seen list plus this id, newest last and at most keep long, or None when the id is blank or stored."""
    if not delivery_id or delivery_id in seen:
        return None
    return [*seen, delivery_id][-keep:]

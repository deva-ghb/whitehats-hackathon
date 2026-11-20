"""Calls the Google Cloud connector will make, and nothing else.

The reader's roles already grant no writes and no data reads. This is the second wall, like aws_guard: every request is
checked against a short list of read calls before it leaves, so a bug can never send a write, and the contents of
buckets, databases or messages are never asked for even if a role were widened by mistake.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# (method, host, path prefix, path suffix): the call is allowed when all four match.
ALLOWED: tuple[tuple[str, str, str, str], ...] = (
    # The project's resources with their configuration, and the permissions on them (Cloud Asset Inventory).
    ("GET", "cloudasset.googleapis.com", "/v1/projects/", "/assets"),
    # The project's admin activity log: who changed what, when.
    ("POST", "logging.googleapis.com", "/v2/entries:list", ""),
)

# Hosts whose calls read data rather than configuration. Refused by name, so the reason is plain.
DATA_HOSTS = {
    "storage.googleapis.com": "objects in Cloud Storage buckets",
    "bigquery.googleapis.com": "rows in BigQuery tables",
    "firestore.googleapis.com": "Firestore documents",
    "pubsub.googleapis.com": "Pub/Sub messages",
    "secretmanager.googleapis.com": "secret values",
}


def blocked(method: str, url: str) -> str:
    """Why this call is refused, or "" when it may go."""
    parts = urlsplit(url)
    if parts.hostname in DATA_HOSTS:
        return f"WhiteHats refuses to read {DATA_HOSTS[parts.hostname]}. It reads configuration and logs only."
    for m, host, prefix, suffix in ALLOWED:
        if method.upper() == m and parts.hostname == host and parts.path.startswith(prefix) and parts.path.endswith(suffix):
            return ""
    return f"WhiteHats only makes its listed read calls; {method.upper()} {parts.hostname}{parts.path} is not one."

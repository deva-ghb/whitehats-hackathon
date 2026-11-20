"""Where each connector keeps its place between reads.

The bookmark lives in Connector.state under "cursors": the key CloudWatch has always used and the one
the Connectors screen leaves out, so an upgrade keeps every stored position.
"""

from __future__ import annotations

from ..models import Connector

KEY = "cursors"


def load(row: Connector) -> dict:
    return dict((row.state or {}).get(KEY) or {})


def save(row: Connector, bookmark: dict) -> None:
    # Reassign the whole dict so SQLAlchemy sees the JSON column change.
    row.state = {**(row.state or {}), KEY: dict(bookmark)}

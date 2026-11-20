"""The connector contract (checklist section 3A): one interface every connector implements.

A connector reads; it never decides what to do with what it read. The caller stores the
bookmark it returns, feeds the lines to the log analyzer and places discovered nodes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar


class SourceError(RuntimeError):
    """A read or a check failed. The message is plain words a person can act on, never a secret."""


class EventsNotSupported(SourceError):
    """This source is read by polling only."""


@dataclass(frozen=True)
class Permission:
    """One permission the connector needs, and what it is used for."""

    name: str
    why: str
    writes: bool = False
    # The node types (library ids, no version) this permission reads; "*" means every node from this source.
    # When the permission is missing, those nodes are marked on the canvas (CN.5).
    covers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Probe:
    permission: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ConnectionTest:
    """'Who am I' plus one probe per declared permission."""

    identity: str
    probes: tuple[Probe, ...]
    sample: int  # events read while checking

    @property
    def is_ok(self) -> bool:
        return all(p.ok for p in self.probes)

    def first_problem(self) -> str:
        return next((p.detail for p in self.probes if not p.ok), "")


@dataclass(frozen=True)
class Discovery:
    """Clone nodes and edges, each with provenance `imported`."""

    nodes: list[dict]
    edges: list[dict]


@dataclass(frozen=True)
class Batch:
    lines: list[dict]  # {"source": ..., "message": ...}, as the log analyzer takes them
    bookmark: dict  # where the next read starts; the caller stores it


class Source(ABC):
    """One connector. Built from the stored config and a way to read its secret."""

    key: ClassVar[str]
    permissions: ClassVar[tuple[Permission, ...]]
    has_events: ClassVar[bool] = False

    def __init__(self, config: Mapping[str, str], secret: Callable[[], str] = lambda: ""):
        self.config = dict(config)
        self._secret = secret

    @abstractmethod
    def test(self) -> ConnectionTest:
        """Sign in, say who the source thinks we are, and probe each permission."""

    @abstractmethod
    def discover(self) -> Discovery:
        """Everything in scope, as clone nodes and edges."""

    @abstractmethod
    def read(self, bookmark: dict, limit: int | None = None) -> Batch:
        """New data since the bookmark. With a limit, a sample whose bookmark must not be stored."""

    def handle_event(self, headers: Mapping[str, str], body: bytes) -> list[dict]:
        """A pushed event, verified by signature. Sources without events are polled instead."""
        raise EventsNotSupported(f"{self.key} is read on a schedule; it does not take pushed events.")


@dataclass(frozen=True)
class Health:
    last_read_at: datetime | None
    items_read: int
    lag_seconds: int | None
    last_error: str
    is_stalled: bool  # a stalled source is a blind spot


# A source read every 5 minutes that has not read for three rounds has stalled.
STALL_AFTER_S = 15 * 60


def health(last_read_at: datetime | None, state: Mapping, now: datetime) -> Health:
    """Health from what the sync stored. Pure, so the screen and the tests agree."""
    lag = int((now - last_read_at).total_seconds()) if last_read_at else None
    error = str(state.get("last_error") or "")
    return Health(last_read_at=last_read_at, items_read=int(state.get("total", 0)), lag_seconds=lag,
                  last_error=error, is_stalled=bool(error) or lag is None or lag > STALL_AFTER_S)

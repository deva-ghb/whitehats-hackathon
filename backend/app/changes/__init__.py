"""The change trigger (checklist VS.1): a push or pull request is noticed once, its changed files and diff are
read, and the Decisions API judges them. Showing the result where people look is the next task (VS.4)."""

from __future__ import annotations

from . import process
from .process import process_pending
from .record import record

__all__ = ["process", "process_pending", "record"]

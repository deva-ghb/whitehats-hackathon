"""Decisions API v0. A run returns clean or suspect. It never calls a model."""

from __future__ import annotations

from .decide import decide

__all__ = ["decide"]

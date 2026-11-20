"""Confirmation: the only step of a scan that calls a model, and only for what the Decisions API flagged."""

from __future__ import annotations

from .confirm import Reply, confirm, gateway_ask
from .verdict import CONFIRMED, DISPUTED, REJECTED, UNCONFIRMED, Confirmation

__all__ = ["CONFIRMED", "DISPUTED", "REJECTED", "UNCONFIRMED", "Confirmation", "Reply", "confirm", "gateway_ask"]

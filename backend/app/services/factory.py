"""Bridge to the Fix Factory engine (../../factory), which proves and delivers code fixes.

An engine works on one company's repositories and keeps no notion of companies, so each
engine belongs to exactly one company (`WH_FACTORY_TENANT`). Every call names the company
asking; any other company is told the engine is not set up for it and never sees its runs.
"""

from __future__ import annotations

import string
import time

import httpx
from fastapi import HTTPException

from ..config import settings
from ..models import Organization
from . import local_workspace

OFFLINE = ("The Fix Factory engine is not running. Start it with: cd factory && uv run factory serve "
           "(with WHITEHATS_DISPATCH_TOKEN set to the same value as this server).")


SLOW = "The Fix Factory engine is not answering in time. Its numbers will show again when it does."
CALL_TIMEOUT_S = 20  # proving a fix or merging it can take a while
# The Overview, the briefing and suggestions wait on the status. A slow engine gets this long, then
# its last known status is shown for SLOW_BACKOFF_S without asking again.
STATUS_TIMEOUT_S = 2
SLOW_BACKOFF_S = 30

NOT_YOURS = ("Fix Factory is not set up for your company yet. WhiteHats connects an engine to your own "
             "repositories during onboarding.")


# Run ids are the engine's own "<intent id>-<timestamp>" names. Only these characters may reach a URL,
# so a crafted id cannot point the request at another engine endpoint.
RUN_ID_CHARS = frozenset(string.ascii_letters + string.digits + "-_")
RUN_ID_MAX = 80  # Remediation.factory_run_id is String(80)


def run_path(run_id: str, action: str | None = None) -> str:
    """The engine path for one run, or 404 when run_id is not a plain run id."""
    if not run_id or len(run_id) > RUN_ID_MAX or not set(run_id) <= RUN_ID_CHARS:
        raise HTTPException(404, "No Fix Factory run has that id.")
    return f"/api/runs/{run_id}/{action}" if action else f"/api/runs/{run_id}"


def serves(org: Organization) -> bool:
    return bool(org.slug) and org.slug == settings.factory_tenant


def call(org: Organization, method: str, path: str, body: dict | None = None,
         timeout: float = CALL_TIMEOUT_S) -> dict | list:
    if not serves(org):
        raise HTTPException(409, NOT_YOURS)
    if local_workspace.ENABLED:
        return local_workspace.factory_request(method, path, body)
    if not settings.factory_token:
        raise HTTPException(503, "Fix Factory is not connected: set WHITEHATS_DISPATCH_TOKEN on this server.")
    try:
        res = httpx.request(method, settings.factory_url.rstrip("/") + path, json=body,
                            headers={"Authorization": f"Bearer {settings.factory_token}"}, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, SLOW) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(503, OFFLINE) from exc
    try:
        data = res.json()
    except ValueError:
        data = {"error": res.text}
    if res.status_code >= 400:
        raise HTTPException(res.status_code if res.status_code < 500 else 502, data.get("error", "Fix Factory error"))
    return data


# Per engine (one engine serves one company): the last status it gave, and until when it counts as slow.
_last_status: dict[str, dict] = {}
_slow_until: dict[str, float] = {}


def read_for_screen(org: Organization, path: str) -> dict | list:
    """A read a screen waits on: STATUS_TIMEOUT_S at most, and no call at all while the engine counts as slow."""
    if time.monotonic() < _slow_until.get(org.slug, 0):
        raise HTTPException(504, SLOW)
    try:
        answer = call(org, "GET", path, timeout=STATUS_TIMEOUT_S)
    except HTTPException as exc:
        if exc.status_code == 504:
            _slow_until[org.slug] = time.monotonic() + SLOW_BACKOFF_S
        raise
    _slow_until.pop(org.slug, None)
    return answer


def status(org: Organization) -> dict:
    if not serves(org):
        return {"connected": False, "message": NOT_YOURS}
    try:
        board = read_for_screen(org, "/api/runs")
    except HTTPException as exc:
        return _last_known(org) if exc.status_code == 504 else {"connected": False, "message": exc.detail}
    _last_status[org.slug] = {"connected": True, "summary": board.get("summary", {})}
    return _last_status[org.slug]


def _last_known(org: Organization) -> dict:
    known = _last_status.get(org.slug)
    return {**known, "stale": True} if known else {"connected": False, "message": SLOW}

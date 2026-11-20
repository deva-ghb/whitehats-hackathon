"""Plain HTTP for connectors that are not AWS: one answer shape, a timeout, and a transport tests replace.

A connector takes a Transport as an argument. In production it is httpx; in tests it is a
recording (tests/connector_kit.py HttpReplay), so no unit test touches the network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .base import SourceError

TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Answer:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)  # names in lower case
    body: Any = None  # parsed JSON, or None when the answer had no JSON
    text: str = ""  # the answer as text when it was not JSON (a diff, for one)


# (method, url, request headers, JSON body or None) -> Answer
Transport = Callable[[str, str, Mapping[str, str], Any], Answer]

_client = None


def httpx_transport(method: str, url: str, headers: Mapping[str, str], body: Any = None) -> Answer:
    """The real network. A failure to connect is a SourceError naming the host, never a traceback."""
    global _client
    import httpx

    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT_S, follow_redirects=False)
    try:
        response = _client.request(method, url, headers=dict(headers), json=body)
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach {urlsplit(url).hostname}: {exc}.") from exc
    parsed, text = None, ""
    if response.content and "json" in response.headers.get("content-type", ""):
        parsed = response.json()
    elif response.content:
        text = response.text
    return Answer(response.status_code, {k.lower(): v for k, v in response.headers.items()}, parsed, text)


def next_link(headers: Mapping[str, str]) -> str | None:
    """The rel="next" address from a Link header (RFC 8288), or None on the last page."""
    for part in (headers.get("link") or "").split(","):
        address, _, params = part.partition(";")
        if 'rel="next"' in params.replace(" ", ""):
            return address.strip().removeprefix("<").removesuffix(">")
    return None

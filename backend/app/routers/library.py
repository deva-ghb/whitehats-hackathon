"""Node library search: what the Studio's left rail and "Request a node" ask (design 9.2)."""

from __future__ import annotations

from dataclasses import asdict
from functools import cache
from typing import Literal

from fastapi import APIRouter, Depends, Query
from library import LIBRARY_VERSION
from library.resolve import load_library
from library.search import Context, Index

from ..permissions import allow
from ..security import Principal

router = APIRouter(prefix="/api")
MAX_QUERY = 500  # a pasted ARN or Azure id fits; a pasted file does not


@cache
def _index() -> Index:
    """The library ships with the app and only changes with a release, so it is read once."""
    return Index(load_library())


@router.get("/library/search")
def search(
    q: str = Query(max_length=MAX_QUERY),
    cloud: Literal["aws", "azure", "gcp"] | None = None,  # the canvas's cloud: ranks, does not filter
    inside: str | None = Query(None, max_length=80),
    dragging_from: str | None = Query(None, alias="from", max_length=80),
    family: str | None = Query(None, max_length=40),
    provider: Literal["aws", "azure", "gcp", "none"] | None = None,  # the cloud facet: filters
    vendor: str | None = Query(None, max_length=80),
    importable: Literal["yes", "no"] | None = None,
    whitehat_mode: Literal["yes", "no"] | None = None,
    limit: int = Query(10, ge=1, le=50),
    user: Principal = Depends(allow("use_account")),
):
    chosen = {"family": family, "cloud": provider, "vendor": vendor, "importable": importable,
              "whitehat_mode": whitehat_mode}
    filters = {name: value for name, value in chosen.items() if value}
    results = _index().search(q, Context(cloud=cloud, inside=inside, dragging_from=dragging_from), filters, limit)
    return {"hits": [asdict(hit) for hit in results.hits], "facets": results.facets}


@cache
def _types() -> list[dict]:
    return sorted(load_library().values(), key=lambda t: (t["family"], t["display_name"]))


@router.get("/library/types")
def types(user: Principal = Depends(allow("use_account"))):
    """Every type, exactly as the library holds it, for the Studio's library rail and inspector."""
    return {"library_version": LIBRARY_VERSION, "types": _types()}

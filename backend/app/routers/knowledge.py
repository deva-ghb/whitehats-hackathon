"""The knowledge base the agents cite (KB.1): read by anyone in an account, refreshed hourly or by the platform's operator."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..knowledge import lookup, osv
from ..knowledge.service import refresh_and_watch
from ..models import KnowledgeSource
from ..permissions import allow
from ..security import Principal

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class RefreshIn(BaseModel):
    ecosystems: list[str] | None = None


@router.get("")
def status(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    sources = db.scalars(select(KnowledgeSource).order_by(KnowledgeSource.ecosystem)).all()
    return {"source": osv.SOURCE, "licence": osv.LICENCE, "attribution": osv.ATTRIBUTION, "ecosystems": [
        {"ecosystem": row.ecosystem, "records": row.records, "newest_change": row.cursor,
         "refreshed_at": row.refreshed_at.isoformat() if row.refreshed_at else None,
         "last_fetched": row.last_fetched, "last_error": row.last_error} for row in sources]}


@router.get("/packages")
def package(ecosystem: str, name: str, version: str | None = None,
            user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    if ecosystem not in osv.ECOSYSTEMS:
        raise HTTPException(400, f"The knowledge base holds {', '.join(osv.ECOSYSTEMS)} for now.")
    return {"ecosystem": ecosystem, "name": name, "version": version,
            "advisories": lookup.by_package(db, ecosystem, name, version)}


@router.get("/advisories/{advisory_id}")
def advisory(advisory_id: str, user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    found = lookup.by_id(db, advisory_id)
    if found is None:
        raise HTTPException(404, f"{advisory_id} is not in the knowledge base.")
    return found


@router.get("/changes")
def changes(since: str, ecosystem: str | None = None,
            user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    return {"since": since, "changes": lookup.changed_since(db, since, ecosystem)}


@router.post("/refresh", status_code=202)
def refresh(body: RefreshIn, background: BackgroundTasks, user: Principal = Depends(allow("operate_platform"))):
    wanted = body.ecosystems or list(osv.ECOSYSTEMS)
    unknown = [name for name in wanted if name not in osv.ECOSYSTEMS]
    if unknown:
        raise HTTPException(400, f"Not an ecosystem the knowledge base holds: {', '.join(unknown)}.")
    background.add_task(_refresh_in_new_session, wanted)
    return {"started": wanted}


def _refresh_in_new_session(ecosystems: list[str]) -> None:
    with SessionLocal() as db:
        refresh_and_watch(db, ecosystems)

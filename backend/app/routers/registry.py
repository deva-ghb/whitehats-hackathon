"""The WhiteHats Registry (REG.1): the company's own package index of fixed library builds.

Build systems install from `/api/registry/simple/` (the simple index pip and uv read, PEP 503), signing in with a
registry token as the password of HTTP basic sign-in; the user name is not checked. People see and manage the
registry from the Registry screen: what was fixed, the proof, and who holds a token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..models import Organization, RegistryPackage, RegistryToken
from ..permissions import allow
from ..registry.build import BuildRefused, build, normalized, storage
from ..registry.recipes import RECIPES
from ..security import Principal, audit

router = APIRouter(prefix="/api")

ASK = {"WWW-Authenticate": 'Basic realm="WhiteHats Registry"'}
TOKEN_PREFIX = "whr_"
ONE_RUN_S = 3600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _holder(db: Session, authorization: str) -> RegistryToken:
    """The token a build system signed in with, or 401. A revoked token is refused like a wrong one."""
    scheme, _, value = authorization.partition(" ")
    token = ""
    if scheme.lower() == "basic":
        try:
            token = base64.b64decode(value).decode().partition(":")[2]
        except (ValueError, UnicodeDecodeError):
            token = ""
    elif scheme.lower() == "bearer":
        token = value
    row = db.scalar(select(RegistryToken).where(RegistryToken.token_hash == _hash(token))) if token else None
    if row is None or row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= utcnow()):
        raise HTTPException(401, "A WhiteHats Registry token is needed.", headers=ASK)
    row.last_used_at = utcnow()
    db.commit()
    return row


def _page(title: str, links: list[str]) -> HTMLResponse:
    body = "\n".join(links)
    return HTMLResponse(f'<!DOCTYPE html>\n<html><head><meta name="pypi:repository-version" content="1.0">'
                        f"<title>{html.escape(title)}</title></head><body>\n{body}\n</body></html>\n")


@router.get("/registry/simple/")
def simple_index(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    holder = _holder(db, authorization)
    names = sorted({normalized(name) for name in db.scalars(select(RegistryPackage.name).where(
        RegistryPackage.org_id == holder.org_id, RegistryPackage.status == "proved"))})
    return _page("WhiteHats Registry", [f'<a href="{name}/">{name}</a>' for name in names])


@router.get("/registry/simple/{project}/")
def simple_project(project: str, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    holder = _holder(db, authorization)
    rows = [row for row in db.scalars(select(RegistryPackage).where(
        RegistryPackage.org_id == holder.org_id, RegistryPackage.status == "proved")) if normalized(row.name) == normalized(project)]
    if not rows:
        raise HTTPException(404, "The registry holds no build of that project.")
    return _page(f"Links for {project}", [
        f'<a href="../../files/{html.escape(row.filename)}#sha256={row.sha256}">{html.escape(row.filename)}</a>'
        for row in rows])


@router.get("/registry/files/{filename}")
def simple_file(filename: str, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    holder = _holder(db, authorization)
    row = db.scalar(select(RegistryPackage).where(
        RegistryPackage.org_id == holder.org_id, RegistryPackage.filename == filename, RegistryPackage.status == "proved"))
    if row is None:
        raise HTTPException(404, "The registry holds no such file.")
    return FileResponse(storage(holder.org_id) / row.filename, media_type="application/octet-stream", filename=row.filename)


# ------------------------------------------------------------------ the Registry screen


def package_out(row: RegistryPackage) -> dict:
    return {"id": row.id, "ecosystem": row.ecosystem, "name": row.name, "version": row.version, "upstream_version": row.upstream_version,
            "advisories": row.advisories, "summary": row.summary, "fix_source": row.fix_source, "patch": row.patch,
            "filename": row.filename, "sha256": row.sha256, "size": row.size, "proof": row.proof, "status": row.status,
            "built_by": row.built_by, "created_at": row.created_at.isoformat(timespec="seconds") + "Z"}


def token_out(row: RegistryToken) -> dict:
    return {"id": row.id, "name": row.name, "hint": row.hint, "created_by": row.created_by,
            "created_at": row.created_at.isoformat(timespec="seconds") + "Z",
            "last_used_at": row.last_used_at.isoformat(timespec="seconds") + "Z" if row.last_used_at else None,
            "revoked": row.revoked_at is not None,
            "expired": row.expires_at is not None and row.expires_at <= utcnow()}


@router.get("/registry")
def registry(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    packages = db.scalars(select(RegistryPackage).where(RegistryPackage.org_id == user.org_id)
                          .order_by(RegistryPackage.created_at.desc())).all()
    tokens = db.scalars(select(RegistryToken).where(RegistryToken.org_id == user.org_id)
                        .order_by(RegistryToken.created_at.desc())).all()
    return {"packages": [package_out(row) for row in packages], "tokens": [token_out(row) for row in tokens],
            "recipes": [{"key": r.key, "ecosystem": "PyPI", "name": r.name, "upstream_version": r.upstream_version,
                         "advisories": list(r.advisories), "summary": r.summary} for r in RECIPES.values()]}


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.post("/registry/tokens")
def make_token(body: NewToken, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    """A token is shown once, here; only its hash is kept."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = RegistryToken(org_id=user.org_id, name=body.name.strip(), token_hash=_hash(token), hint=token[-4:],
                        created_by=user.name)
    db.add(row)
    audit(db, user, "made a registry token", row.name)
    db.commit()
    return {**token_out(row), "token": token}


@router.delete("/registry/tokens/{tid}")
def revoke_token(tid: int, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    row = db.get(RegistryToken, tid)
    if row is None or row.org_id != user.org_id:
        raise HTTPException(404, "No such token.")
    row.revoked_at = row.revoked_at or utcnow()
    audit(db, user, "revoked a registry token", row.name)
    db.commit()
    return token_out(row)


class Build(BaseModel):
    recipe: str


@router.post("/registry/builds")
def make_build(body: Build, user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    """Build, prove and keep the next fixed version of a library from one of the registry's recipes."""
    recipe = RECIPES.get(body.recipe)
    if recipe is None:
        raise HTTPException(404, "The registry has no such recipe.")
    org = db.get(Organization, user.org_id)
    try:
        row = build(db, org.id, recipe, by=user.name)
    except BuildRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, user, "built a fixed library", f"{row.name} {row.version}", ", ".join(row.advisories))
    db.commit()
    return package_out(row)


@router.post("/factory/registry-access")
def factory_registry_access(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    """A one-hour registry token for Fix Factory's install step, asked with the dispatch token it shares with this
    server; no person signs in to it. Made for the company the engine serves."""
    if not settings.factory_token or not hmac.compare_digest(f"Bearer {settings.factory_token}", authorization):
        raise HTTPException(401, "Fix Factory's token is needed for registry access.")
    org = db.scalar(select(Organization).where(Organization.slug == settings.factory_tenant))
    if org is None:
        raise HTTPException(409, "Fix Factory serves no company on this server.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.add(RegistryToken(org_id=org.id, name="Fix Factory (one run)", token_hash=_hash(token), hint=token[-4:],
                         created_by="Fix Factory", expires_at=utcnow() + timedelta(seconds=ONE_RUN_S)))
    db.commit()
    return {"token": token, "index_url": settings.public_url.rstrip("/") + "/api/registry/simple/", "seconds": ONE_RUN_S}

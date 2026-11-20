"""Per-company vault. A response names a reference and its scope, never the value."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Organization
from ..permissions import allow
from ..security import Principal
from ..vault_store import VaultError, list_secrets, store_secret

router = APIRouter(prefix="/api")


@router.get("/vault/secrets")
def index(db: Session = Depends(get_db), user: Principal = Depends(allow("use_account"))):
    """Which credentials are set, and the scope of each. Any signed-in role of the account."""
    return {"secrets": list_secrets(db, user.org_id)}


@router.put("/vault/secrets")
async def put(request: Request, db: Session = Depends(get_db),
              user: Principal = Depends(allow("manage_connections"))):
    """Store a value for this company. Account owner and Account admin only."""
    body = await _object(request)
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(401, "This company no longer exists.")
    try:
        row = store_secret(db, org, _text(body, "reference"), _text(body, "value"), _text(body, "scope"), user)
    except VaultError as exc:
        raise HTTPException(422, exc.message) from None
    return {"reference": row.reference, "set": True, "scope": row.scope}


async def _object(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - the body may be the secret, so the error stays generic
        raise HTTPException(422, "Send a JSON object with a reference and a value.") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "Send a JSON object with a reference and a value.")
    return body


def _text(body: dict, key: str) -> str:
    if key not in body:
        if key == "scope":
            return ""
        raise HTTPException(422, "Send a reference and a value.")
    value = body[key]
    if not isinstance(value, str):
        raise HTTPException(422, "The reference, value and scope must be text.")
    return value

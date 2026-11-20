"""Per-company secret store.

A clone holds a vault reference such as vault://tenant/acme/github-app. The value
is sealed with that company's own key and is never written back to the clone.
"""

from __future__ import annotations

import logging

from cryptography.fernet import InvalidToken
from library.clone_document import vault_problem
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import Organization, VaultSecret
from .security import Principal, audit, seal, tenant_box

log = logging.getLogger("whitehats.vault")

# The audit target and the reference column are both 200 characters.
MAX_REFERENCE = 200
MAX_VALUE = 8_000  # a token or a small private key fits; a pasted file does not
MAX_SCOPE = 4_000  # a copyable policy from P3.2, not the secret itself


class VaultError(Exception):
    """A reference or value the store will not accept. The message never contains a secret."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def company_of(reference: str) -> str:
    """The company slug in a vault reference, using the same split as vault_problem."""
    if not reference.startswith("vault://"):
        return ""
    parts = reference[len("vault://"):].split("/")
    return parts[1] if len(parts) > 1 else ""


def accepted_reference(reference: str, company: str) -> str:
    """The reference when it is a safe path inside this company."""
    text = reference.strip()
    problem = vault_problem(text)
    if problem:
        raise VaultError(f"Vault reference {problem}.")
    if len(text) > MAX_REFERENCE:
        raise VaultError("That vault reference is too long.")
    if company_of(text) != company:
        raise VaultError("Vault reference belongs to another company.")
    return text


def store_secret(
    db: Session, org: Organization, reference: str, value: str, scope: str, user: Principal,
) -> VaultSecret:
    """Seal a value for this company. A second store at the same reference replaces it."""
    if not org.slug:
        raise VaultError("This company has no slug, so it cannot hold a vault reference.")
    ref = accepted_reference(reference, org.slug)
    _reject_value(value, ref, scope)
    sealed = seal(value, db, org.id)
    row = db.scalar(select(VaultSecret).where(VaultSecret.org_id == org.id, VaultSecret.reference == ref))
    if row is None:
        row = VaultSecret(org_id=org.id, reference=ref, value_enc=sealed, scope=scope, updated_at=utcnow())
        db.add(row)
        action = "stored a vault credential"
    else:
        row.value_enc, row.scope, row.updated_at = sealed, scope, utcnow()
        action = "replaced a vault credential"
    audit(db, user, action, ref, "The value is sealed and was not recorded.")
    db.commit()
    log.info("vault credential saved org_id=%s reference=%s", org.id, ref)
    return row


def list_secrets(db: Session, org_id: int) -> list[dict]:
    """References and scopes for one company. The sealed value stays out."""
    rows = db.scalars(select(VaultSecret).where(VaultSecret.org_id == org_id).order_by(VaultSecret.reference))
    return [_public(row) for row in rows]


def open_secret(db: Session, org: Organization, reference: str) -> str:
    """The value for this company only. Callers must not log it or send it back."""
    if not org.slug:
        raise VaultError("This company has no slug, so it cannot hold a vault reference.")
    ref = accepted_reference(reference, org.slug)
    row = db.scalar(select(VaultSecret).where(VaultSecret.org_id == org.id, VaultSecret.reference == ref))
    if row is None or not row.value_enc:
        raise VaultError("No secret is stored at that reference.")
    try:
        return tenant_box(db, org.id).decrypt(row.value_enc.encode()).decode()
    except InvalidToken as exc:
        raise VaultError("This company's key cannot open that secret.") from exc


def _public(row: VaultSecret) -> dict:
    return {
        "reference": row.reference,
        "set": True,
        "scope": row.scope,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _reject_value(value: str, reference: str, scope: str) -> None:
    if value == "":
        raise VaultError("Enter a secret value.")
    if len(value) > MAX_VALUE:
        raise VaultError("That secret is too long to store.")
    if len(scope) > MAX_SCOPE:
        raise VaultError("The scope text is too long.")
    if _contains(scope, value) or _contains(reference, value):
        raise VaultError("The scope and the reference must not contain the secret.")


def _contains(haystack: str, secret: str) -> bool:
    """True when the secret would be visible in a field the API returns.

    A short value is compared whole, so a one-letter secret is not refused just
    because that letter also appears in the company slug.
    """
    if len(secret) < 8:
        return haystack == secret
    return secret in haystack

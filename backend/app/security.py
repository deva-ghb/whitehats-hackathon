"""Passwords, sessions, two-step sign-in, per-tenant secrets and the audit trail. Access lives in permissions.py."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db, utcnow
from .models import AuditLog, Organization, Project, User

PBKDF2_ROUNDS = 260_000
# Job titles people are invited with. What each may do comes from permissions.py, not from this list.
ROLES = ("ceo", "ciso", "analyst", "engineer")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


# ------------------------------------------------------------ session tokens

def _encode(payload: dict, minutes: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode({**payload, "iat": now, "exp": now + timedelta(minutes=minutes)}, settings.jwt_secret,
                      algorithm="HS256")


def issue_token(user: User, *, support_org: int | None = None, minutes: int | None = None) -> str:
    payload = {"sub": str(user.id), "org": support_org or user.org_id, "role": user.role, "purpose": "session"}
    if support_org:
        payload["support"] = True
    return _encode(payload, minutes or settings.token_hours * 60)


def issue_step_token(user: User, purpose: str, **claims: str) -> str:
    """Short-lived token between password and the second step (mfa or mfa_setup), or for one round trip elsewhere."""
    return _encode({**claims, "sub": str(user.id), "purpose": purpose}, 10)


def read_token(token: str, purpose: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This step expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session. Sign in again.") from exc
    if payload.get("purpose") != purpose:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session. Sign in again.")
    return payload


bearer = HTTPBearer(auto_error=False)


class Principal:
    """The signed-in person, acting in one tenant. Support sessions are read-only."""

    def __init__(self, user: User, org_id: int, support: bool):
        self.user, self.org_id, self.support = user, org_id, support
        self.id, self.name, self.email = user.id, user.name, user.email
        self.role = "support" if support else user.role
        self.title = user.title
        # Set by permissions.allow for a project permission: the project this request acts on.
        self.project: Project | None = None
        # For a support session the account limited to one project.
        self.support_project_id: int | None = None


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    payload = read_token(creds.credentials, "session")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account is no longer active.")
    org = db.get(Organization, int(payload["org"]))
    if org is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This company no longer exists.")
    if payload.get("support"):
        if user.role != "operator" or not org.support_access_until or org.support_access_until < utcnow():
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Support access to this company has ended.")
        principal = Principal(user, org.id, True)
        principal.support_project_id = org.support_project_id
        return principal
    if org.status in ("suspended", "offboarded") and not org.is_platform:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"This company's access is {org.status}. Contact WhiteHats.")
    return Principal(user, org.id, False)


# ------------------------------------------------------------ two-step sign-in (RFC 6238 TOTP)

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_verify(secret: str, code: str, window: int = 1) -> bool:
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return False
    now = int(time.time() // 30)
    return any(hmac.compare_digest(totp_at(secret, now + d), code) for d in range(-window, window + 1))


def totp_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/WhiteHats:{quote(email)}?secret={secret}&issuer=WhiteHats&digits=6&period=30"


# ------------------------------------------------------------ secrets at rest

def _fernet(raw_key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode()).digest()))


def _master() -> Fernet:
    return _fernet(settings.secret_box_key)


def tenant_box(db: Session, org_id: int | None) -> Fernet:
    """Each tenant's secrets are sealed with its own key; that key is sealed with the platform master key."""
    if org_id is None:
        return _master()
    org = db.get(Organization, org_id)
    if org is None:
        return _master()
    if not org.secret_key_enc:
        org.secret_key_enc = _master().encrypt(secrets.token_urlsafe(32).encode()).decode()
        db.flush()
    return _fernet(_master().decrypt(org.secret_key_enc.encode()).decode())


def seal(secret: str, db: Session | None = None, org_id: int | None = None) -> str:
    if not secret:
        return ""
    box = tenant_box(db, org_id) if db is not None else _master()
    return box.encrypt(secret.encode()).decode()


def unseal(token: str, db: Session | None = None, org_id: int | None = None) -> str:
    if not token:
        return ""
    boxes = ([tenant_box(db, org_id)] if db is not None else []) + [_master()]
    for box in boxes:
        try:
            return box.decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    return ""


def mask(secret: str) -> str:
    return "" if not secret else f"••••{secret[-4:]}" if len(secret) > 8 else "••••"


def new_token(prefix: str) -> tuple[str, str]:
    token = prefix + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def new_collector_token() -> tuple[str, str]:
    return new_token("whc_")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------ audit

def audit(db: Session, user, action: str, target: str = "", detail: str = "", org_id: int | None = None):
    """Record an action in the tenant's audit trail. Support sessions are labelled as such.

    An action taken inside a project (permissions.allow set user.project) is filed under that project.
    """
    actor = "system"
    if user is not None:
        actor = f"{user.name} (WhiteHats support)" if getattr(user, "support", False) else user.name
    project = getattr(user, "project", None) if org_id is None else None
    db.add(AuditLog(
        org_id=org_id if org_id is not None else user.org_id,
        project_id=project.id if project is not None else None,
        user_id=user.id if user else None,
        actor=actor, action=action, target=target, detail=detail,
    ))

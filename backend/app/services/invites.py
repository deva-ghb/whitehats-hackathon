"""Invitations: the only way anyone gets an account in a tenant.

Operators invite a company's first admin; that admin invites everyone else.
Both go through `create`, so the domain rule, seat limit and one-time link are the same for each.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import utcnow
from ..models import Invite, Organization, User
from ..security import new_token
from . import notify
from .tenancy import PLAN_LIMITS

INVITE_HOURS = 72


def open_invites(db: Session, org: Organization) -> list[Invite]:
    return list(db.scalars(select(Invite).where(Invite.org_id == org.id, Invite.accepted_at.is_(None),
                                                Invite.revoked_at.is_(None), Invite.expires_at > utcnow())))


def create(
    db: Session, org: Organization, email: str, name: str, role: str, by: str, *, account_role: str | None = None,
) -> dict:
    """Validate, store and (when mail is set up) send an invitation. Returns the one-time link."""
    email = email.strip().lower()
    if org.domain and not email.endswith("@" + org.domain):
        raise HTTPException(400, f"Only addresses at {org.domain} can be invited to this company.")
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(409, "Someone already has an account with that email.")
    if any(i.email == email for i in open_invites(db, org)):
        raise HTTPException(409, "That person already has an open invitation. Revoke it first to send a new one.")
    seats = PLAN_LIMITS.get(org.plan, PLAN_LIMITS["growth"])["seats"]
    used = (db.scalar(select(func.count(User.id)).where(User.org_id == org.id, User.active.is_(True))) or 0) \
        + len(open_invites(db, org))
    if used >= seats:
        raise HTTPException(402, f"The {org.plan} plan includes {seats} seats, and all are taken or invited.")
    token, digest = new_token("whi_")
    db.add(Invite(org_id=org.id, email=email, name=name, role=role, token_hash=digest,
                  expires_at=utcnow() + timedelta(hours=INVITE_HOURS), created_by=by, account_role=account_role))
    link = f"{settings.public_url.rstrip('/')}/invite/{token}"
    emailed = ""
    if settings.smtp_host:
        try:
            notify.email(email, f"You're invited to {org.name} on WhiteHats",
                         f"{by} invited you. Open this link within {INVITE_HOURS} hours to set up your account:\n{link}")
            emailed = "Emailed."
        except notify.NotifyError as exc:
            emailed = f"Email failed ({exc}); share the link yourself."
    return {"link": link, "expires_in_hours": INVITE_HOURS, "emailed": emailed}


def out(i: Invite) -> dict:
    return {"id": i.id, "email": i.email, "name": i.name, "role": i.role, "expires_at": i.expires_at.isoformat(),
            "created_by": i.created_by,
            "accepted_at": i.accepted_at.isoformat() if i.accepted_at else None,
            "revoked_at": i.revoked_at.isoformat() if i.revoked_at else None,
            "expired": i.expires_at < utcnow() and not i.accepted_at}

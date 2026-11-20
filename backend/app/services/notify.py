"""Alerts to the tenant's own channel: Slack or Teams incoming webhook, or email."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Organization
from ..security import unseal

KINDS = {"slack": "Slack incoming webhook", "teams": "Microsoft Teams incoming webhook", "email": "Email"}


class NotifyError(RuntimeError):
    pass


def send(db: Session, org: Organization, title: str, text: str) -> str:
    """Deliver one alert. Raises NotifyError with a plain explanation when it cannot."""
    cfg = org.alerts or {}
    kind = cfg.get("kind")
    target = unseal(cfg.get("target_enc", ""), db, org.id)
    if not kind or not target:
        raise NotifyError("No alerts channel is set up for this company.")
    if kind in ("slack", "teams"):
        body = {"text": f"*{title}*\n{text}"} if kind == "slack" else {"text": f"**{title}**\n\n{text}"}
        try:
            res = httpx.post(target, json=body, timeout=10)
        except httpx.HTTPError as exc:
            raise NotifyError(f"Could not reach the webhook: {exc}") from exc
        if res.status_code >= 300:
            raise NotifyError(f"The webhook answered {res.status_code}: {res.text[:200]}")
        return f"Delivered to {KINDS[kind]}."
    if kind == "email":
        email(target, title, text)
        return f"Emailed {target}."
    raise NotifyError(f"Unknown alert channel {kind}.")


def email(to: str, subject: str, text: str) -> None:
    if not settings.smtp_host:
        raise NotifyError("Email needs WH_SMTP_HOST set on the server.")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = f"[WhiteHats] {subject}", settings.smtp_from, to
    msg.set_content(text)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise NotifyError(f"Email failed: {exc}") from exc


def alert(db: Session, org_id: int, title: str, text: str) -> None:
    """Best-effort alert for live incidents; never breaks the caller."""
    org = db.get(Organization, org_id)
    if org is None or not (org.alerts or {}).get("verified_at"):
        return
    try:
        send(db, org, title, text)
    except NotifyError:
        pass

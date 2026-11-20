"""The WhiteHats GitHub App: its webhook, and what the Connectors screen needs to install it (CN.1)."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import changes
from ..config import settings
from ..connectors.github import GitHub
from ..connectors.github_app import APP_EVENTS, APP_PERMISSIONS
from ..db import get_db
from ..models import User
from ..permissions import allow
from ..security import Principal
from ..services import github_install, github_webhook

router = APIRouter(prefix="/api")


@router.post("/github/webhook")
async def webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """A delivery from GitHub. No session: the signature over the exact body is the proof.

    It is answered as soon as the delivery is kept; judging the change it brings runs after the answer.
    """
    body = await request.body()
    try:
        answer = github_webhook.receive(db, request.headers, body)
    except github_webhook.Refused as refused:
        raise HTTPException(refused.status, refused.reason) from None
    if answer["read"]:
        background.add_task(changes.process.process_pending_in_new_session)
    return answer


@router.get("/github/app")
def app_details(user: Principal = Depends(allow("use_account"))):
    """Whether this server has the App, where to install it, and exactly what it may do."""
    slug = settings.github_app_slug
    return {
        "configured": bool(settings.github_app_id and settings.github_app_private_key_path and slug),
        "webhooks": bool(settings.github_webhook_secret),
        "install_url": f"https://github.com/apps/{slug}/installations/new" if slug else None,
        "permissions": dict(APP_PERMISSIONS), "events": list(APP_EVENTS),
        "reads_with": [{"name": p.name, "why": p.why} for p in GitHub.permissions],
    }


@router.post("/github/install")
def start_install(project: str = "", user: Principal = Depends(allow("manage_connections")), db: Session = Depends(get_db)):
    """Where to send the person to install the App. The link carries a signed state that expires in 10 minutes, and
    the project the person started from (checked to be theirs on the way back)."""
    return {"url": github_install.install_url(db.get(User, user.id), project[:80])}


@router.get("/github/callback")
def finish_install(request: Request, db: Session = Depends(get_db)):
    """GitHub sends the person back here after the install. No session: the signed state and GitHub's code are the proof."""
    params = request.query_params
    try:
        row = github_install.link(db, params.get("state", ""), params.get("code", ""),
                                  params.get("installation_id", ""))
    except github_install.NotLinked as exc:
        return RedirectResponse("/connectors?" + urlencode({"github": "not-linked", "reason": str(exc)}), 303)
    project = github_install.started_from(db, params.get("state", ""))
    if project:  # back to the map the person connected from, with the connection open
        return RedirectResponse(f"/p/{project}/studio?" + urlencode({"connection": "github", "github": row.status}), 303)
    return RedirectResponse("/connectors?" + urlencode({"github": row.status}), 303)

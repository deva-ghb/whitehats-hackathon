"""Linking an account to its installation of the WhiteHats GitHub App, proved rather than typed (CN.1).

Anyone can type an installation number, so WhiteHats never takes one on trust. The person starts
from WhiteHats (a signed, ten-minute state ties the install to them and their account), installs the
App on GitHub, and GitHub sends them back with a one-time code. WhiteHats exchanges the code for the
person's own GitHub token and keeps the installation only if GitHub lists it among theirs.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..connectors import github as github_connector
from ..connectors.http import next_link
from ..db import utcnow
from ..models import Connector, Project, User
from ..permissions import is_allowed
from ..security import audit, issue_step_token, read_token
from . import cloudlogs

PURPOSE = "github-install"
OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
MAX_PAGES = 10


class NotLinked(Exception):
    """The installation was not linked; the message says why in plain words."""


def install_url(user: User, project: str = "") -> str:
    """GitHub's install page. The state names the project the person started from, so the return lands there."""
    if not settings.github_app_slug:
        raise HTTPException(503, "The WhiteHats GitHub App is not set up on this server yet.")
    state = issue_step_token(user, PURPOSE, project=project)
    return f"https://github.com/apps/{settings.github_app_slug}/installations/new?{urlencode({'state': state})}"


def started_from(db: Session, state: str) -> str:
    """The project (its short name) the install was started from, when it is still the starter's; "" otherwise."""
    try:
        payload = read_token(state, PURPOSE)
    except HTTPException:
        return ""
    user = db.get(User, int(payload["sub"]))
    slug = str(payload.get("project") or "")
    if user is None or not slug:
        return ""
    found = db.scalar(select(Project.slug).where(Project.org_id == user.org_id, Project.slug == slug))
    return found or ""


def link(db: Session, state: str, code: str, installation_id: str) -> Connector:
    """Check the state and the person's GitHub token, then link and test the installation."""
    user = _starter(db, state)
    if not installation_id.isdigit() or not code:
        raise NotLinked("GitHub did not send back an installation. Start again from the GitHub connection in the Studio.")
    login = _owner_if_theirs(_user_token(code), installation_id)
    row = db.scalar(select(Connector).where(Connector.org_id == user.org_id, Connector.key == "github"))
    if row is None:
        raise NotLinked("This account has no GitHub connection.")
    row.config = {"installation_id": installation_id, "account": login}
    row.state, row.last_sync_at = {}, None
    row.status, row.last_check = cloudlogs.test_source(db, row)
    row.connected_at = None if row.status == "error" else utcnow()
    if row.status in cloudlogs.READABLE:
        from . import auto_map

        auto_map.mark_due(row, f"{row.name} was connected")  # its map fills, and an audit is decided (AUD.6)
    audit(db, user, "connected GitHub App", row.name, f"installation {installation_id} on {login}: {row.status}")
    db.commit()
    return row


def _starter(db: Session, state: str) -> User:
    """The person who started the install from WhiteHats, still able to manage connections."""
    try:
        payload = read_token(state, PURPOSE)
    except HTTPException as exc:
        raise NotLinked("The install link expired or was not started from WhiteHats. Start again.") from exc
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.active:
        raise NotLinked("The person who started the install can no longer sign in.")
    if not is_allowed({user.account_role or ""}, "manage_connections"):
        raise NotLinked("Only an Account owner or Account admin can connect GitHub.")
    return user


def _user_token(code: str) -> str:
    if not settings.github_app_client_id or not settings.github_app_client_secret:
        raise NotLinked("The WhiteHats GitHub App's sign-in is not set up on this server.")
    answer = github_connector.httpx_transport("POST", OAUTH_TOKEN_URL, {"Accept": "application/json"}, {
        "client_id": settings.github_app_client_id, "client_secret": settings.github_app_client_secret,
        "code": code})
    token = (answer.body or {}).get("access_token") if isinstance(answer.body, dict) else None
    if answer.status != 200 or not token:
        raise NotLinked("GitHub did not accept the sign-in code. Start again from the GitHub connection in the Studio.")
    return token


def _owner_if_theirs(token: str, installation_id: str) -> str:
    """The installation's account login, only when GitHub lists it among this person's installations."""
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
               "X-GitHub-Api-Version": github_connector.API_VERSION, "User-Agent": "WhiteHats"}
    url: str | None = f"{github_connector.API}/user/installations?per_page=100"
    for _ in range(MAX_PAGES):
        if url is None:
            break
        answer = github_connector.httpx_transport("GET", url, headers, None)
        if answer.status != 200:
            raise NotLinked(f"GitHub would not list your installations ({answer.status}). Start again.")
        for item in (answer.body or {}).get("installations", []):
            if str(item.get("id")) == installation_id:
                return str((item.get("account") or {}).get("login", ""))
        url = next_link(answer.headers)
    raise NotLinked("That installation is not one you can manage on GitHub, so it was not linked.")


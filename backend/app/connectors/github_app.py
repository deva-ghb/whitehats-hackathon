"""Signing in as the WhiteHats GitHub App: a short App token, exchanged for one installation's token.

GitHub Apps are preferred over personal tokens because the customer chooses the repositories, the
permissions are fixed by the App, and every token expires within an hour. The installation token's
permissions come back with it, so WhiteHats refuses to run when the App was given more than it needs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from .base import SourceError

# The App's full permission list, as registered. The connector reads with the first three plus
# pull requests; pull request write (comments) and checks write (the "WhiteHats" check) are for the
# pull request feature (VS.4), not for reading. Anything above these makes the connector refuse.
APP_PERMISSIONS: Mapping[str, str] = {
    "metadata": "read",
    "contents": "read",
    "actions": "read",
    "pull_requests": "write",
    "checks": "write",
}
APP_EVENTS = ("push", "pull_request", "workflow_run")
_LEVELS = {"read": 1, "write": 2, "admin": 3}
APP_TOKEN_S = 9 * 60  # GitHub accepts at most 10 minutes; a minute of slack for clock drift
CLOCK_SKEW_S = 60
RENEW_BEFORE_S = 5 * 60  # renew an installation token this long before it expires


def app_jwt(app_id: str, private_key: str, now: float) -> str:
    """The App's own token (RS256), used only to ask for an installation token."""
    import jwt

    if not app_id or not private_key:
        raise SourceError("The WhiteHats GitHub App is not set up on this server: GITHUB_APP_ID and "
                          "GITHUB_APP_PRIVATE_KEY_PATH are needed.")
    issued = int(now) - CLOCK_SKEW_S
    return jwt.encode({"iat": issued, "exp": issued + APP_TOKEN_S, "iss": app_id}, private_key, algorithm="RS256")


def app_token_from_settings(clock: Callable[[], float] = time.time) -> Callable[[], str]:
    """A maker of App tokens from the server's settings. The key file is read when a token is needed."""
    from ..config import settings

    def make() -> str:
        path = settings.github_app_private_key_path
        try:
            key = Path(path).read_text() if path else ""
        except OSError as exc:
            raise SourceError(f"The GitHub App private key file could not be read: {exc.strerror}.") from exc
        return app_jwt(settings.github_app_id, key, clock())

    return make


def excess_permissions(granted: Mapping[str, str]) -> list[str]:
    """Each permission the installation holds above what the App needs, in plain words."""
    excess = []
    for name, level in sorted(granted.items()):
        allowed = APP_PERMISSIONS.get(name)
        if allowed is None or _LEVELS.get(level, 3) > _LEVELS[allowed]:
            excess.append(f"{name.replace('_', ' ')}: {level}")
    return excess


def expires_epoch(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()

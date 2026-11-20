"""The company's sign-in provider, read for weak accounts (ID.2): Okta, Microsoft Entra ID or Google Workspace.

Most breaches start with an account: a password guessed or phished where nothing else is asked, an administrator
without a second step, or the account of someone who left and still works. Every READ_EVERY_S, WhiteHats reads the
provider's people through the read-only access the customer gave and raises:

- an administrator who can sign in without multi-factor (high)
- a person who can sign in without multi-factor (medium)
- an account still active that nobody has signed in to for STALE_DAYS days or more (medium)
- more than MOST_ADMINS administrators (medium): every one is a way to everything

Each is a signal for the Identity WhiteHat, closed when the provider no longer shows it. What the provider does not say
(a plan without sign-in dates, a person whose methods cannot be read) is unknown: never a finding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors import routing
from ..connectors.base import SourceError
from ..db import utcnow
from ..models import Connector, Signal
from . import auto_incidents

log = logging.getLogger("whitehats.identity")

KEYS = ("okta", "entra", "google")
READ_EVERY_S = 3600
STALE_DAYS = 90
MOST_ADMINS = 5
MOST_PEOPLE = 2000  # people read in one pass; a larger directory is read in part and says so
SOURCE = "whitehat:identity"
PREFIX = "idp-"
NAMES = {"okta": "Okta", "entra": "Microsoft Entra ID", "google": "Google Workspace"}


@dataclass
class Person:
    id: str
    name: str
    admin: bool
    mfa: bool | None  # None: the provider did not say
    last_sign_in: datetime | None
    created: datetime | None = None


def _when(text) -> datetime | None:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return when.astimezone(UTC).replace(tzinfo=None) if when.tzinfo else when  # naive UTC, as the database keeps


def _get(http, url: str, headers: dict) -> tuple[Any, dict]:
    try:
        res = http.get(url, headers=headers)
    except Exception as exc:
        raise SourceError(f"Could not reach the sign-in provider: {exc}") from exc
    if res.status_code != 200:
        raise SourceError(f"The sign-in provider answered {res.status_code}: {res.text[:200]}")
    return res.json(), dict(res.headers)


def _okta_next(headers: dict) -> str:
    for part in str(headers.get("link") or headers.get("Link") or "").split(","):
        if 'rel="next"' in part:
            return part.split(";", 1)[0].strip().strip("<>")
    return ""


def read_okta(http, domain: str, token: str) -> list[Person]:
    base = f"https://{domain.removeprefix('https://').rstrip('/')}"
    auth = {"Authorization": f"SSWS {token}", "Accept": "application/json"}
    admins = set()
    try:
        found, _ = _get(http, f"{base}/api/v1/iam/assignees/users?limit=200", auth)
        admins = {str(u.get("id")) for u in (found.get("value") if isinstance(found, dict) else found) or []}
    except SourceError as exc:
        log.info("okta administrators unreadable: %s", exc)
    people, url = [], f"{base}/api/v1/users?limit=200&filter=status%20eq%20%22ACTIVE%22"
    while url and len(people) < MOST_PEOPLE:
        page, headers = _get(http, url, auth)
        for u in page:
            uid = str(u.get("id"))
            profile = u.get("profile") or {}
            try:
                factors, _ = _get(http, f"{base}/api/v1/users/{uid}/factors", auth)
                mfa = any(f.get("status") == "ACTIVE" for f in factors)
            except SourceError:
                mfa = None
            people.append(Person(uid, profile.get("login") or profile.get("email") or uid, uid in admins, mfa,
                                 _when(u.get("lastLogin")), _when(u.get("created"))))
        url = _okta_next(headers)
    return people


def read_entra(http, token: str) -> list[Person]:
    auth = {"Authorization": f"Bearer {token}"}
    methods = {}
    try:
        url = "https://graph.microsoft.com/v1.0/reports/authenticationMethods/userRegistrationDetails"
        while url:
            page, _ = _get(http, url, auth)
            for r in page.get("value") or []:
                methods[str(r.get("id"))] = (bool(r.get("isMfaRegistered")), bool(r.get("isAdmin")))
            url = page.get("@odata.nextLink") or ""
    except SourceError as exc:  # the report needs an Entra ID P1 or P2 plan: without it, multi-factor is unknown
        log.info("entra registration details unreadable: %s", exc)
    people = []
    url = ("https://graph.microsoft.com/v1.0/users?$filter=accountEnabled eq true&$top=999"
           "&$select=id,displayName,userPrincipalName,createdDateTime,signInActivity")
    try:
        page, _ = _get(http, url, auth)
    except SourceError:  # sign-in activity also needs P1: read without it, sign-in dates unknown
        page, _ = _get(http, url.replace(",signInActivity", ""), auth)
    while True:
        for u in page.get("value") or []:
            uid = str(u.get("id"))
            mfa, admin = methods.get(uid, (None, False))
            last = (u.get("signInActivity") or {}).get("lastSignInDateTime")
            people.append(Person(uid, u.get("userPrincipalName") or u.get("displayName") or uid, admin, mfa,
                                 _when(last), _when(u.get("createdDateTime"))))
        nxt = page.get("@odata.nextLink")
        if not nxt or len(people) >= MOST_PEOPLE:
            return people
        page, _ = _get(http, nxt, auth)


def read_google(http, token: str) -> list[Person]:
    auth = {"Authorization": f"Bearer {token}"}
    people, page_token = [], ""
    while len(people) < MOST_PEOPLE:
        url = ("https://admin.googleapis.com/admin/directory/v1/users?customer=my_customer&maxResults=500"
               "&query=isSuspended%3Dfalse" + (f"&pageToken={page_token}" if page_token else ""))
        page, _ = _get(http, url, auth)
        for u in page.get("users") or []:
            last = u.get("lastLoginTime")
            people.append(Person(str(u.get("id")), u.get("primaryEmail") or str(u.get("id")), bool(u.get("isAdmin")),
                                 bool(u.get("isEnrolledIn2Sv")),
                                 None if not last or str(last).startswith("1970") else _when(last),
                                 _when(u.get("creationTime"))))
        page_token = page.get("nextPageToken") or ""
        if not page_token:
            break
    return people


def judge(people: list[Person], provider: str, now: datetime) -> list[dict]:
    """What is weak, as signals to raise: {"key", "severity", "title", "detail", "resource"}."""
    name = NAMES.get(provider, provider)
    out = []
    for p in people:
        if p.mfa is False:
            out.append({"key": f"nomfa-{p.id}", "severity": "high" if p.admin else "medium", "resource": p.name,
                        "title": f"{'Administrator' if p.admin else 'Person'} can sign in without multi-factor",
                        "detail": f"{p.name} can sign in to {name} with a password alone"
                                  f"{', and is an administrator' if p.admin else ''}. A guessed or phished password is "
                                  "then enough. Require a second step (an app or a security key) for them."})
        since = p.last_sign_in  # never signed in, or not said: unknown, not stale
        if since is not None and (now - since).days >= STALE_DAYS:
            out.append({"key": f"stale-{p.id}", "severity": "medium", "resource": p.name,
                        "title": f"Account unused for {(now - since).days} days",
                        "detail": f"Nobody has signed in to {p.name} on {name} for {(now - since).days} days, yet it "
                                  "still works. If they left or no longer need it, switch it off: an unused account "
                                  "is one nobody notices being used."})
    admins = [p for p in people if p.admin]
    if len(admins) > MOST_ADMINS:
        out.append({"key": "admins", "severity": "medium", "resource": name,
                    "title": f"{len(admins)} administrators on {name}",
                    "detail": f"{len(admins)} people can change everything on {name}: "
                              f"{', '.join(p.name for p in admins[:10])}{' and more' if len(admins) > 10 else ''}. "
                              f"Keep it to the few who need it (at most {MOST_ADMINS})."})
    return out


def read(db: Session, c: Connector, http=None) -> list[Person]:
    from . import cloudlogs

    http = http or cloudlogs.http
    if c.key == "okta":
        return read_okta(http, c.config.get("domain", ""), cloudlogs._secret(db, c))
    if c.key == "entra":
        return read_entra(http, cloudlogs._azure_token(db, c, "https://graph.microsoft.com/.default"))
    token, _sa = cloudlogs._google_token(db, c, "https://www.googleapis.com/auth/admin.directory.user.readonly",
                                         subject=c.config.get("admin_email", ""))
    return read_google(http, token)


def apply(db: Session, c: Connector, found: list[dict]) -> tuple[int, int]:
    """Open or refresh each, in the projects the connection belongs to; close what the provider no longer shows."""
    opened = closed = 0
    for project_id in routing.projects_for(db, c, routing.connection_resource(c)):
        held = {s.fingerprint: s for s in db.scalars(select(Signal).where(
            Signal.org_id == c.org_id, Signal.project_id == project_id, Signal.source == SOURCE,
            Signal.fingerprint.like(f"{PREFIX}{c.key}-%"), Signal.closed_at.is_(None)))}
        keys = set()
        for f in found:
            key = f"{PREFIX}{c.key}-{f['key']}"[:240]
            keys.add(key)
            if key in held:
                held[key].title, held[key].detail = f["title"][:240], f["detail"][:4000]
                continue
            db.add(Signal(org_id=c.org_id, project_id=project_id, severity=f["severity"], title=f["title"][:240],
                          detail=f["detail"][:4000], resource=f["resource"][:300], entity=NAMES[c.key],
                          source=SOURCE, fingerprint=key, origin="live", mitre="T1078"))
            opened += 1
        for key, row in held.items():
            if key not in keys:
                row.closed_at, row.closed_reason = utcnow(), "put right in the sign-in provider"
                closed += 1
    db.flush()
    return opened, closed


def sync(db: Session, c: Connector, http=None) -> dict:
    now = utcnow()
    try:
        people = read(db, c, http)
    except SourceError as exc:
        c.state = {**(c.state or {}), "last_error": str(exc)[:300], "last_error_at": now.isoformat()}
        c.last_check = str(exc)[:300]
        db.commit()
        return {"error": str(exc)}
    opened, closed = apply(db, c, judge(people, c.key, now))
    c.last_sync_at = now
    c.status = "reading"
    c.last_check = f"Read {len(people)} people from {NAMES[c.key]}."
    c.state = {**(c.state or {}), "last_error": "", "people": len(people),
               "unknown_mfa": sum(1 for p in people if p.mfa is None), "partial": len(people) >= MOST_PEOPLE}
    db.commit()
    if opened:
        auto_incidents.sweep(db)
    return {"people": len(people), "opened": opened, "closed": closed}


def sync_due(db: Session) -> int:
    """Read each connected sign-in provider whose turn has come. Returns how many were read."""
    now = utcnow()
    done = 0
    for c in db.scalars(select(Connector).where(Connector.key.in_(KEYS),
                                                Connector.status.in_(("checked", "reading", "verified")))):
        if c.last_sync_at is not None and (now - c.last_sync_at).total_seconds() < READ_EVERY_S - 30:
            continue
        sync(db, c)
        done += 1
    return done

"""Cloud log sources and identity checks.

Cloud logs are pulled on a schedule, never pushed, using read-only credentials:
  AWS CloudWatch Logs   app/connectors/cloudwatch.py, on the connector framework
  GitHub                app/connectors/github.py, polled every minute as the fallback to its webhook
  Azure Monitor         Log Analytics query API (client credentials)
  Google Cloud Logging  entries:list (service account JWT)

Each source feeds the same log analyzer as a collector would, through a
"cloud" collector row, so context budgets, shape mining and WhiteHats apply
unchanged. Cursors live in Connector.state so each event is read once.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import changes, connectors
from ..connectors import bookmarks, routing
from ..connectors.base import SourceError
from ..connectors.github import GitHub
from ..db import utcnow
from ..models import Collector, Connector
from ..security import new_collector_token, unseal
from . import connector_status

MAX_EVENTS_PER_SYNC = 20_000
http = httpx.Client(timeout=30)  # replaced in tests


def _secret(db: Session, c: Connector) -> str:
    return unseal(c.secret_enc, db, c.org_id)


# ------------------------------------------------------------------ sources on the connector framework

def pull_framework(db: Session, c: Connector, limit: int | None = None) -> list[dict]:
    source = connectors.for_row(db, c)
    batch = source.read(bookmarks.load(c), limit=limit)
    if not limit:
        bookmarks.save(c, batch.bookmark)
        if getattr(source, "flow_logs", None) is not None:  # each network's traffic log, for the screens (NET.2)
            c.state = {**(c.state or {}), "flow_logs": source.flow_logs,
                       "flow_logs_error": getattr(source, "flow_error", "")}
    return batch.lines


# ------------------------------------------------------------------ Azure Monitor (Log Analytics)

def _azure_token(db: Session, c: Connector, scope: str) -> str:
    cfg = c.config
    try:
        res = http.post(f"https://login.microsoftonline.com/{cfg.get('tenant_id')}/oauth2/v2.0/token",
                        data={"grant_type": "client_credentials", "client_id": cfg.get("client_id"),
                              "client_secret": _secret(db, c), "scope": scope})
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach Microsoft sign-in: {exc}") from exc
    if res.status_code != 200:
        raise SourceError(f"Microsoft sign-in refused: {res.json().get('error_description', res.text)[:200]}")
    return res.json()["access_token"]


def pull_azure_monitor(db: Session, c: Connector, limit: int | None = None) -> list[dict]:
    token = _azure_token(db, c, "https://api.loganalytics.io/.default")
    cursor = (c.state or {}).get("cursor") or (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    base = c.config.get("query") or "union AzureActivity, SigninLogs"
    take = limit or 5000
    kql = f"{base} | where TimeGenerated > datetime({cursor}) | order by TimeGenerated asc | take {take}"
    try:
        res = http.post(f"https://api.loganalytics.io/v1/workspaces/{c.config.get('workspace_id')}/query",
                        headers={"Authorization": f"Bearer {token}"}, json={"query": kql})
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach Log Analytics: {exc}") from exc
    if res.status_code != 200:
        raise SourceError(f"Log Analytics answered {res.status_code}: {res.text[:200]}")
    out, newest = [], cursor
    for table in res.json().get("tables", []):
        cols = [col["name"] for col in table.get("columns", [])]
        for row in table.get("rows", []):
            rec = dict(zip(cols, row, strict=False))
            newest = max(newest, str(rec.get("TimeGenerated", newest)))
            out.append({"source": f"azure:{table.get('name', 'table')}", "message": json.dumps(rec, default=str)})
    if not limit:
        c.state = {**(c.state or {}), "cursor": newest}
    return out


# ------------------------------------------------------------------ Google Cloud Logging

def _google_token(db: Session, c: Connector, scope: str, subject: str = "") -> tuple[str, dict]:
    """A Google access token for the service account; with `subject`, acting as that Workspace administrator (the
    directory is only read through an administrator the customer named, by domain-wide delegation)."""
    try:
        sa = json.loads(_secret(db, c))
    except ValueError as exc:
        raise SourceError("The service account key must be the JSON file Google gave you.") from exc
    if not isinstance(sa, dict) or not isinstance(sa.get("client_email"), str):
        raise SourceError("The Google service account key requires a client email.")
    now = int(time.time())
    claims = {"iss": sa["client_email"], "scope": scope, "aud": "https://oauth2.googleapis.com/token",
              "iat": now, "exp": now + 3600, **({"sub": subject} if subject else {})}
    try:
        assertion = jwt.encode(claims, sa["private_key"], algorithm="RS256")
    except (KeyError, ValueError, TypeError, jwt.PyJWTError) as exc:
        raise SourceError("The Google service account key could not be parsed.") from exc
    try:
        res = http.post("https://oauth2.googleapis.com/token",
                        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach Google sign-in: {exc}") from exc
    if res.status_code != 200:
        raise SourceError(f"Google sign-in refused with HTTP {res.status_code}.")
    return res.json()["access_token"], sa


def pull_gcp_logging(db: Session, c: Connector, limit: int | None = None) -> list[dict]:
    token, sa = _google_token(db, c, "https://www.googleapis.com/auth/logging.read")
    project = c.config.get("project_id") or sa.get("project_id")
    cursor = (c.state or {}).get("cursor") or (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    flt = c.config.get("filter") or 'logName:"cloudaudit.googleapis.com"'
    body = {"resourceNames": [f"projects/{project}"], "filter": f'({flt}) AND timestamp>"{cursor}"',
            "orderBy": "timestamp asc", "pageSize": min(limit or 1000, 1000)}
    out, newest = [], cursor
    for _ in range(20):
        try:
            res = http.post("https://logging.googleapis.com/v2/entries:list",
                            headers={"Authorization": f"Bearer {token}"}, json=body)
        except httpx.HTTPError as exc:
            raise SourceError(f"Could not reach Cloud Logging: {exc}") from exc
        if res.status_code != 200:
            raise SourceError(f"Cloud Logging answered HTTP {res.status_code}.")
        data = res.json()
        for e in data.get("entries", []):
            payload = e.get("protoPayload") or e.get("jsonPayload") or e.get("textPayload") or {}
            out.append({"source": f"gcp:{e.get('logName', '')[-80:]}",
                        "message": payload if isinstance(payload, str) else json.dumps(payload)})
            newest = max(newest, e.get("timestamp", newest))
        if limit or not data.get("nextPageToken") or len(out) >= MAX_EVENTS_PER_SYNC:
            break
        body["pageToken"] = data["nextPageToken"]
    if not limit:
        c.state = {**(c.state or {}), "cursor": newest}
    return out


PULLERS = {"aws": pull_framework, "cloudwatch": pull_framework, "github": pull_framework, "azure_monitor": pull_azure_monitor, "gcp_logging": pull_gcp_logging}


# ------------------------------------------------------------------ identity checks (read-only, cheap)

def verify_identity(db: Session, c: Connector) -> tuple[str, str]:
    try:
        if c.key == "okta":
            res = http.get(f"https://{c.config.get('domain', '').removeprefix('https://')}/api/v1/users?limit=1",
                           headers={"Authorization": f"SSWS {_secret(db, c)}", "Accept": "application/json"})
            return ("checked", "Credentials checked. Okta accepted the token. WhiteHats reads its people every hour.") \
                if res.status_code == 200 else ("error", f"Okta answered {res.status_code}: {res.text[:160]}")
        if c.key == "entra":
            _azure_token(db, c, "https://graph.microsoft.com/.default")
            return "checked", "Credentials checked. Microsoft Entra ID issued a token. WhiteHats reads its people every hour."
        if c.key == "google":
            _google_token(db, c, "https://www.googleapis.com/auth/admin.directory.user.readonly",
                          subject=c.config.get("admin_email", ""))
            return "checked", "Credentials checked. Google issued a token. WhiteHats reads its people every hour."
    except SourceError as exc:
        return "error", str(exc)
    except httpx.HTTPError as exc:
        return "error", f"Could not reach the service: {exc}"
    return "checked", "Credentials checked. Nothing is read from this service yet."


# ------------------------------------------------------------------ sync into the log analyzer

def cloud_collector(db: Session, c: Connector) -> Collector:
    col = db.scalar(select(Collector).where(Collector.org_id == c.org_id, Collector.mode == "cloud",
                                            Collector.name == c.name))
    if col is None:
        _, digest = new_collector_token()  # cloud sources never use a token; the hash only fills the column
        col = Collector(org_id=c.org_id, name=c.name, kind="log", mode="cloud", host=c.key, token_hash=digest,
                        status="running", interval_s=300, context_budget=800, version="cloud")
        db.add(col)
        db.flush()
    return col


def sync(db: Session, c: Connector) -> int:
    if c.key == "gcp":
        from . import gcp_checks  # the settings checks and the activity in one read

        return gcp_checks.sync(db, c)["activity"]
    try:
        lines = PULLERS[c.key](db, c)
    except SourceError as exc:
        # The screen shows the error, not "Reading data"; sync_all keeps trying so a passing fault clears itself.
        c.status, c.last_check = "error", str(exc)
        c.state = {**(c.state or {}), "last_error": str(exc), "last_error_at": utcnow().isoformat()}
        db.commit()
        raise
    record_lines(db, c, lines)
    if c.status == "error":
        c.last_check = "Reading again: the last read worked after an earlier failure."
    c.status = "reading"
    db.commit()
    return len(lines)


def record_lines(db: Session, c: Connector, lines: list[dict], *, source: str = "poll") -> None:
    """Feed what a source read, by poll or by webhook, to the log analyzer and the WhiteHats of the right projects.

    GitHub pushes and pull requests are also kept as changes to judge (VS.1), in the same transaction as
    the bookmark that moved past them, so a change is never lost and never kept twice.
    """
    from ..whitehats import dispatch
    from . import loganalyzer
    from .tenancy import mark_milestone

    col = cloud_collector(db, c)
    if lines:
        loganalyzer.ingest_lines(db, col, lines)
        # Only the projects whose scope holds this connection's data see it (AP.3).
        for projects, group in _by_projects(db, c, lines):
            dispatch(db, c.org_id, "on_log_batch", project_ids=projects, source_host=c.name, lines=group)
        mark_milestone(db, c.org_id, "first_data")
        if c.key == GitHub.key:
            changes.record(db, c, lines, source=source)
        from . import auto_map  # the map reads connections; imported here to keep the import order simple

        if why := auto_map.changed_the_setup(lines):
            auto_map.mark_due(c, why)  # a change in the account is read into the map on the next pass (STUDIO.6)
        if c.key == "aws":
            from . import cloud_settings

            cloud_settings.note_changes(db, c, lines)  # the changed services' settings are checked again (CS.1)
    col.last_heartbeat, c.last_sync_at = utcnow(), utcnow()
    c.state = {**(c.state or {}), "last_count": len(lines), "last_error": "",
               "total": int((c.state or {}).get("total", 0)) + len(lines)}


def _by_projects(db: Session, c: Connector, lines: list[dict]) -> list[tuple[list[int], list[dict]]]:
    """Lines grouped by the projects that hold them. A line that names its repository goes by that repository."""
    groups: dict[str, list[dict]] = {}
    for line in lines:
        groups.setdefault(str(line.get("repo") or ""), []).append(line)
    out = []
    for repo, group in groups.items():
        resource = (routing.Resource(identity=str(group[0].get("url") or repo), repo=repo) if repo
                    else routing.connection_resource(c))
        out.append((routing.projects_for(db, c, resource), group))
    return out


def test_source(db: Session, c: Connector) -> tuple[str, str]:
    source = connectors.for_row(db, c)
    try:
        if source is None:
            sample = len(PULLERS[c.key](db, c, limit=5))
        else:
            result = source.test()
            connector_status.record_check(c, result)
            if not result.is_ok:
                return "error", result.first_problem()
            sample = result.sample
    except SourceError as exc:
        if source is not None:
            connector_status.record_check(c, None, str(exc))
        return "error", str(exc)
    return "checked", (f"Credentials checked. The check read {sample} recent event(s). "
                       "Reading on a schedule has not started.")


READABLE = ("checked", "reading", "verified")
# Sources outside "Cloud logs" that are polled too. GitHub is polled as the fallback to its webhook.
POLLED = ("github",)
# How often each source is read. A GitHub push must be seen by polling within 70 seconds (CN.1). A change in the AWS
# account is read every minute too, so a risky change shows within a minute of AWS recording it (AWS itself takes a
# few minutes to record an action in the account's activity history).
EVERY_S = {"github": 60, "aws": 60, "cloudwatch": 60}
DEFAULT_EVERY_S = 300
WAKE_EVERY_S = 60  # the background worker wakes this often and reads whatever is due


def can_read(c: Connector) -> bool:
    """A polled source whose check passed, or one that was connected and then failed (even on its very first read), so
    a fault that passes clears itself instead of leaving the source unread for good."""
    polled = c.category == "Cloud logs" or c.key in POLLED
    return polled and (c.status in READABLE or (c.status == "error" and (c.last_sync_at or c.connected_at) is not None))


def is_due(c: Connector, now: datetime) -> bool:
    if c.last_sync_at is None:
        return True
    # A few seconds of slack, so a source read every minute is not pushed to the next wake-up.
    return (now - c.last_sync_at).total_seconds() >= EVERY_S.get(c.key, DEFAULT_EVERY_S) - 5


def sync_all(db: Session, now: datetime | None = None) -> dict[str, int | str]:
    """Read every polled source can_read allows; with now, only those whose turn has come."""
    results = {}
    readable = Connector.status.in_(READABLE)
    failed_after_reading = (Connector.status == "error") & (Connector.last_sync_at.is_not(None)
                                                            | Connector.connected_at.is_not(None))
    polled = (Connector.category == "Cloud logs") | Connector.key.in_(POLLED)
    prototype = Connector.key.in_(connectors.PROTOTYPE_CONNECTORS | {"gcp_logging"})
    for c in db.scalars(select(Connector).where(polled, prototype, readable | failed_after_reading)):
        if now is not None and not is_due(c, now):
            continue
        try:
            results[f"{c.org_id}:{c.key}"] = sync(db, c)
        except SourceError as exc:
            results[f"{c.org_id}:{c.key}"] = f"error: {exc}"
    return results

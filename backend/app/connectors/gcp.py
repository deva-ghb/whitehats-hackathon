"""Google Cloud: one project's resources, the permissions on them and its admin activity, read as a read-only reader.

The customer creates the reader with docs/connectors/gcp-readonly-setup.sh: a service account holding view roles only,
which WhiteHats may act as. WhiteHats signs in as its own identity (Application Default Credentials: a workload identity
in production, the developer's gcloud sign-in locally) and asks Google for a 15-minute token as the reader. No key is
created or stored, the same as the AWS role with its external id.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .base import Batch, ConnectionTest, Discovery, Permission, Probe, Source, SourceError
from .gcp_guard import blocked

log = logging.getLogger("whitehats.connectors.gcp")

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TOKEN_LIFETIME_S = 900
LOOKBACK_S = 3600  # a first read of the activity log starts an hour back
OVERLAP_S = 120  # Google can add an entry a little after a later one; re-read this much and skip what was seen
MAX_PAGES = 20
ACTIVITY_LOG = "cloudaudit.googleapis.com%2Factivity"

# What the settings checks and the map read. Each is a Cloud Asset Inventory type.
ASSET_TYPES = (
    "cloudresourcemanager.googleapis.com/Project",
    "storage.googleapis.com/Bucket",
    "compute.googleapis.com/Firewall",
    "compute.googleapis.com/Network",
    "compute.googleapis.com/Instance",
    "sqladmin.googleapis.com/Instance",
    "iam.googleapis.com/ServiceAccount",
    "iam.googleapis.com/ServiceAccountKey",
    "run.googleapis.com/Service",
    "container.googleapis.com/Cluster",
    "bigquery.googleapis.com/Dataset",
    "pubsub.googleapis.com/Topic",
)
# The types whose permissions are read: who can reach the project, its buckets, datasets and topics.
POLICY_TYPES = (
    "cloudresourcemanager.googleapis.com/Project",
    "storage.googleapis.com/Bucket",
    "bigquery.googleapis.com/Dataset",
    "pubsub.googleapis.com/Topic",
    "run.googleapis.com/Service",
)


def _default_token(reader: str) -> str:
    """A short-lived token as the reader, from WhiteHats' own identity."""
    try:
        import google.auth
        import google.auth.transport.requests
        from google.auth import impersonated_credentials
    except ImportError as exc:  # pragma: no cover - a dependency of the backend
        raise SourceError("Google sign-in is not installed on this server.") from exc
    try:
        source, _ = google.auth.default(scopes=[SCOPE])
        creds = impersonated_credentials.Credentials(source_credentials=source, target_principal=reader,
                                                     target_scopes=[SCOPE], lifetime=TOKEN_LIFETIME_S)
        creds.refresh(google.auth.transport.requests.Request())
    except google.auth.exceptions.DefaultCredentialsError as exc:
        raise SourceError("WhiteHats has no Google identity on this server to sign in with.") from exc
    except google.auth.exceptions.RefreshError as exc:
        text = str(exc)
        if "getAccessToken" in text or "PERMISSION_DENIED" in text:
            raise SourceError(f"Google refused to let WhiteHats act as {reader}. Run the setup script again, "
                              "or wait a few minutes: a new permission takes up to 7 minutes to apply.") from exc
        raise SourceError("Google sign-in failed. Check the reader's email.") from exc
    return str(creds.token)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Gcp(Source):
    """One customer's Google Cloud project, read as the reader they created."""

    key = "gcp"
    permissions = (
        Permission("iam.serviceAccounts.getAccessToken", "sign in as the read-only reader the customer created",
                   covers=("*",)),
        Permission("cloudasset.assets.listResource", "list the project's resources and their configuration",
                   covers=("*",)),
        Permission("cloudasset.assets.listIamPolicy", "read who has access to the project and its resources"),
        Permission("logging.logEntries.list", "read the admin activity log: who changed what, and when"),
    )

    def __init__(self, config: Mapping[str, str], secret: Callable[[], str] = lambda: "",
                 token: Callable[[str], str] | None = None, client: httpx.Client | None = None):
        super().__init__(config, secret)
        self.project = str(config.get("project_id", "")).strip()
        self.reader = str(config.get("service_account", "")).strip()
        if not self.project or not self.reader:
            raise SourceError("Give the project ID and the reader's email from the setup script.")
        self._token_for = token or _default_token
        self._token = ""
        self._client = client or httpx.Client(timeout=30, follow_redirects=False)

    # ------------------------------------------------------------------ calls

    def _call(self, method: str, url: str, **kw: Any) -> dict:
        reason = blocked(method, url)
        if reason:
            raise SourceError(reason)
        if not self._token:
            self._token = self._token_for(self.reader)
        try:
            res = self._client.request(method, url, headers={"Authorization": f"Bearer {self._token}"}, **kw)
        except httpx.HTTPError as exc:
            raise SourceError(f"Could not reach Google Cloud: {exc}") from exc
        if res.status_code == 403:
            raise SourceError("Google Cloud refused the read. Check that the setup script ran on this project.")
        if res.status_code >= 400:
            message = ""
            try:
                message = res.json().get("error", {}).get("message", "")
            except ValueError:
                pass
            raise SourceError(f"Google Cloud answered HTTP {res.status_code}. {message}".strip())
        return res.json()

    def _assets(self, content: str, types: tuple[str, ...], page_size: int = 500, pages: int = MAX_PAGES) -> list[dict]:
        url = f"https://cloudasset.googleapis.com/v1/projects/{self.project}/assets"
        params: list[tuple[str, str | int]] = [("contentType", content), ("pageSize", page_size)]
        params += [("assetTypes", t) for t in types]
        out: list[dict] = []
        token = ""
        for _ in range(pages):
            page = self._call("GET", url, params=params + ([("pageToken", token)] if token else []))
            out += page.get("assets", [])
            token = page.get("nextPageToken", "")
            if not token:
                break
        return out

    def _entries(self, since: str, page_size: int = 200, pages: int = MAX_PAGES) -> list[dict]:
        body = {"resourceNames": [f"projects/{self.project}"], "orderBy": "timestamp asc", "pageSize": page_size,
                "filter": f'logName="projects/{self.project}/logs/{ACTIVITY_LOG}" AND timestamp>="{since}"'}
        out: list[dict] = []
        for _ in range(pages):
            page = self._call("POST", "https://logging.googleapis.com/v2/entries:list", json=body)
            out += page.get("entries", [])
            if not page.get("nextPageToken"):
                break
            body["pageToken"] = page["nextPageToken"]
        return out

    # ------------------------------------------------------------------ the contract

    def test(self) -> ConnectionTest:
        try:
            self._token = self._token_for(self.reader)
        except SourceError as exc:
            return ConnectionTest(identity=self.reader, probes=(Probe(self.permissions[0].name, False, str(exc)),),
                                  sample=0)
        probes = [Probe(self.permissions[0].name, True, f"Signed in as {self.reader}.")]
        checks = (
            (self.permissions[1].name, lambda: self._assets("RESOURCE", ASSET_TYPES, page_size=1, pages=1)),
            (self.permissions[2].name, lambda: self._assets("IAM_POLICY", POLICY_TYPES, page_size=1, pages=1)),
            (self.permissions[3].name, lambda: self._entries(_iso(datetime.now(UTC) - timedelta(days=1)),
                                                             page_size=5, pages=1)),
        )
        sample = 0
        for name, run in checks:
            try:
                got = run()
                sample += len(got)
                probes.append(Probe(name, True, "Readable."))
            except SourceError as exc:
                probes.append(Probe(name, False, str(exc)))
        return ConnectionTest(identity=self.reader, probes=tuple(probes), sample=sample)

    def inventory(self) -> tuple[list[dict], list[dict]]:
        """(resources with their configuration, permission policies), as Cloud Asset Inventory gives them."""
        return self._assets("RESOURCE", ASSET_TYPES), self._assets("IAM_POLICY", POLICY_TYPES)

    def discover(self) -> Discovery:
        resources, _ = self.inventory()
        nodes = [{"key": a["name"], "type": a["assetType"], "name": a["name"].rsplit("/", 1)[-1],
                  "provenance": "imported", "location": (a.get("resource") or {}).get("location", "")}
                 for a in resources]
        return Discovery(nodes=nodes, edges=[])

    def read(self, bookmark: dict, limit: int | None = None) -> Batch:
        """Admin activity since the bookmark, as plain lines; the raw entries ride along for the change checks."""
        now = datetime.now(UTC)
        last = bookmark.get("since")
        start = (datetime.fromisoformat(last) - timedelta(seconds=OVERLAP_S)) if last \
            else now - timedelta(seconds=LOOKBACK_S)
        entries = self._entries(_iso(start), pages=1 if limit else MAX_PAGES)
        seen = set(bookmark.get("seen", []))
        fresh = [e for e in entries if e.get("insertId") not in seen]
        if limit:
            fresh = fresh[:limit]
        lines = [line_of(e) for e in fresh]
        newest = max((e.get("timestamp", "") for e in entries), default=last or _iso(now))
        keep = [e.get("insertId") for e in entries if e.get("timestamp", "") >= _iso(
            datetime.fromisoformat(newest) - timedelta(seconds=OVERLAP_S))]
        return Batch(lines=lines, bookmark={"since": newest, "seen": [k for k in keep if k][-500:]})


def line_of(entry: dict) -> dict:
    """One activity entry as the log analyzer takes it, with the fields the change checks need."""
    p = entry.get("protoPayload") or {}
    who = (p.get("authenticationInfo") or {}).get("principalEmail", "someone")
    method = p.get("methodName", "")
    target = p.get("resourceName", "")
    status = (p.get("status") or {}).get("code", 0)
    return {"source": f"gcp:{entry.get('resource', {}).get('type', 'activity')}",
            "message": f"{who} called {method} on {target}" + (" (refused)" if status else ""),
            "ts": entry.get("timestamp", ""), "id": entry.get("insertId", ""), "who": who, "method": method,
            "target": target, "failed": bool(status),
            # Storage records a permission change in serviceData.policyDelta, the others in request.policy.
            "request": {"request": p.get("request") or {}, "delta": p.get("serviceData") or {}},
            "service": p.get("serviceName", "")}

"""Recorded-response kit for connectors: record real answers once, replay them in every test run.

A recording is a JSON file with the calls a connector made, in order, and what came back:
    {"about": "...", "clock": 1761200000.0, "calls": [
        {"service": "logs", "method": "filter_log_events", "region": "ap-southeast-2", "params": {...},
         "response": {...}},
        {"service": "sts", "method": "assume_role", "params": {...},
         "error": {"code": "AccessDenied", "message": "...", "status": 403}}]}
Replay fails on any call that was not recorded, with different parameters, or out of order.
A call with a region is answered only by a client made for that region (older recordings have none).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from app.connectors.http import Answer

RECORDINGS = Path(__file__).parent / "recordings" / "connectors"
PLACEHOLDER_ACCOUNT = "111122223333"
PLACEHOLDER_CREDENTIALS = {"AccessKeyId": "recorded-placeholder", "SecretAccessKey": "recorded-placeholder",
                           "SessionToken": "recorded-placeholder", "Expiration": "2030-01-01T00:00:00Z"}


def load(connector: str, name: str) -> dict:
    return json.loads((RECORDINGS / connector / f"{name}.json").read_text())


class Replay:
    """A ClientFactory that answers from a recording. Call verify() once the connector is done."""

    def __init__(self, recording: dict):
        self.recording = recording
        self.stubbers: list[Stubber] = []
        self._made: set[tuple[str, str | None]] = set()

    def __call__(self, service: str, region: str | None, credentials: Any = None) -> Any:
        self._made.update({(service, region), (service, None)})
        client = boto3.client(service, region_name=region or "us-east-1", aws_access_key_id="replay",
                              aws_secret_access_key="replay", aws_session_token="replay")
        stubber = Stubber(client)
        for call in self.recording["calls"]:
            if call["service"] != service or call.get("region", region) != region:
                continue  # a call recorded in another region belongs to that region's client
            if "error" in call:
                error = call["error"]
                stubber.add_client_error(call["method"], error["code"], error["message"], error["status"],
                                         expected_params=call["params"])
            else:
                stubber.add_response(call["method"], call["response"], call["params"])
        stubber.activate()
        self.stubbers.append(stubber)
        return client

    @property
    def clock(self) -> float:
        return self.recording["clock"]

    def verify(self) -> None:
        for stubber in self.stubbers:
            stubber.assert_no_pending_responses()
        for call in self.recording["calls"]:
            where = (call["service"], call.get("region"))
            if where not in self._made:
                raise AssertionError(f"recorded {call['service']}.{call['method']} in {where[1]} was never asked for")


# What every call to GitHub's API must carry (CN.1): its pinned API version and a Bearer token.
GITHUB_API = "https://api.github.com/"
GITHUB_API_VERSION = "2022-11-28"


def _check_request_headers(url: str, headers: Any) -> None:
    if not url.startswith(GITHUB_API):
        return
    given = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    if given.get("x-github-api-version") != GITHUB_API_VERSION:
        raise AssertionError(f"{url} was called without X-GitHub-Api-Version {GITHUB_API_VERSION}")
    token = given.get("authorization", "").removeprefix("Bearer ")
    if not given.get("authorization", "").startswith("Bearer ") or not token.strip():
        raise AssertionError(f"{url} was called without a Bearer token")


class HttpReplay:
    """A Transport (app/connectors/http.py) that answers from a recording, for a connector that does not use boto.

    An HTTP recording lists calls in the order they were made:
        {"method": "GET", "url": "https://api.github.com/...", "status": 200, "headers": {"link": "..."},
         "body": {...}}
    Replay fails on a call that was not recorded, on a different method or address, or out of order.
    Request headers are never recorded, so no token reaches a recording.
    """

    def __init__(self, recording: dict):
        self.recording = recording
        self._calls = list(recording["calls"])
        self._at = 0

    @property
    def clock(self) -> float:
        return self.recording["clock"]

    def __call__(self, method: str, url: str, headers: Any = None, body: Any = None) -> Answer:
        if self._at >= len(self._calls):
            raise AssertionError(f"no recorded call left for {method} {url}")
        call = self._calls[self._at]
        self._at += 1
        if (call["method"], call["url"]) != (method, url):
            raise AssertionError(f"expected {call['method']} {call['url']}, got {method} {url}")
        _check_request_headers(url, headers or {})
        return Answer(call["status"], dict(call.get("headers") or {}), call.get("body"), call.get("text", ""))

    def verify(self) -> None:
        left = len(self._calls) - self._at
        if left:
            raise AssertionError(f"{left} recorded HTTP call(s) were not made")


# The answer headers a connector reads; everything else (cookies, request ids) is left out of recordings.
KEPT_HEADERS = ("link", "retry-after", "x-ratelimit-remaining", "x-ratelimit-reset")
PLACEHOLDER_TOKEN = "recorded-placeholder"


class HttpRecorder:
    """Wraps a real Transport and keeps every call, to be saved with save(). Tokens never reach the file."""

    def __init__(self, transport: Any, clock: float):
        self._transport, self.clock, self.calls = transport, clock, []

    def __call__(self, method: str, url: str, headers: Any = None, body: Any = None) -> Answer:
        answer = self._transport(method, url, headers or {}, body)
        kept = {k: v for k, v in answer.headers.items() if k in KEPT_HEADERS}
        self.calls.append({"method": method, "url": url, "status": answer.status, "headers": kept,
                           "body": answer.body, **({"text": answer.text} if answer.text else {})})
        return answer

    def save(self, connector: str, name: str, about: str, replace: Mapping[str, str] | None = None) -> Path:
        """Write the recording. replace maps text that must not be kept (an account name) to its stand-in."""
        path = RECORDINGS / connector / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        calls = []
        for call in self.calls:
            body = call["body"]
            if isinstance(body, dict) and "token" in body:
                body = {**body, "token": PLACEHOLDER_TOKEN}
            calls.append({**call, "body": body})
        text = json.dumps({"about": about, "clock": self.clock, "calls": calls}, indent=2) + "\n"
        for real, stand_in in (replace or {}).items():
            text = text.replace(real, stand_in)
        path.write_text(text)
        return path


class _RecordingClient:
    def __init__(self, client: Any, service: str, region: str | None, calls: list[dict]):
        self._client, self._service, self._region, self._calls = client, service, region, calls

    def __getattr__(self, method: str) -> Any:
        real = getattr(self._client, method)
        if not callable(real) or method.startswith("_") or method in ("meta", "exceptions"):
            return real

        def recorded(**params: Any) -> Any:
            entry = {"service": self._service, "method": method, "params": params}
            if self._region:
                entry["region"] = self._region
            try:
                answer = real(**params)
            except ClientError as exc:
                error = exc.response.get("Error", {})
                entry["error"] = {"code": error.get("Code", ""), "message": error.get("Message", ""),
                                  "status": exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 400)}
                self._calls.append(entry)
                raise
            entry["response"] = {k: v for k, v in answer.items() if k != "ResponseMetadata"}
            self._calls.append(entry)
            return answer

        return recorded


class Recorder:
    """Wraps a real ClientFactory and keeps every call, to be saved with save()."""

    def __init__(self, clients: Any, clock: float):
        self._clients, self.clock, self.calls = clients, clock, []

    def __call__(self, service: str, region: str | None, credentials: Any = None) -> Any:
        return _RecordingClient(self._clients(service, region, credentials), service, region, self.calls)

    def save(self, connector: str, name: str, about: str, accounts: tuple[str, ...]) -> Path:
        path = RECORDINGS / connector / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"about": about, "clock": self.clock, "calls": [_scrub(call, accounts) for call in self.calls]}
        path.write_text(json.dumps(data, indent=2, default=_jsonable) + "\n")
        return path


def _jsonable(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"cannot record {type(value).__name__}")


def _scrub(call: dict, accounts: tuple[str, ...]) -> dict:
    """Temporary credentials never reach a recording, and real account ids become the placeholder."""
    call = json.loads(json.dumps(call, default=_jsonable))
    if "Credentials" in call.get("response", {}):
        call["response"]["Credentials"] = dict(PLACEHOLDER_CREDENTIALS)
    text = json.dumps(call)
    for account in accounts:
        text = text.replace(account, PLACEHOLDER_ACCOUNT)
    return json.loads(text)


def app_installation(app_token: str, fallback: str = "") -> str:
    """The id of the one installation the WhiteHats App has, read through the App's own installation list.

    An installation id kept in a settings file goes stale when the App is installed again; the App's list does not.
    Several installations are ambiguous, so then the caller's `fallback` is used, and none is an error.
    """
    from app.connectors.http import httpx_transport

    answer = httpx_transport("GET", f"{GITHUB_API}app/installations", {
        "Authorization": f"Bearer {app_token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION, "User-Agent": "WhiteHats"})
    ids = [str(item["id"]) for item in answer.body] if answer.status == 200 and isinstance(answer.body, list) else []
    if len(ids) == 1:
        return ids[0]
    if fallback and (not ids or fallback in ids):
        return fallback
    raise SystemExit(f"The App lists {len(ids)} installations (status {answer.status}); name one with GITHUB_APP_INSTALLATION_ID.")

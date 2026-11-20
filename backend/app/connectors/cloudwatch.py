"""AWS CloudWatch Logs, read through a role the customer created for WhiteHats. Never a stored key."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from .base import Batch, ConnectionTest, Discovery, Permission, Probe, Source, SourceError
from .retry import Pacer, aws_wait_hint, with_retries

# (service, region, temporary credentials or None) -> a boto3 client. Tests replay recordings through it.
ClientFactory = Callable[[str, str | None, Mapping[str, str] | None], Any]

LOOKBACK_S = 3600  # a group with no bookmark yet starts an hour back
PAGE_SIZE = 10_000
PAGES_PER_GROUP = 10
MAX_EVENTS_PER_SYNC = 20_000
FILTER_CALLS_PER_SECOND = 5  # the FilterLogEvents quota per account and region
# CloudWatch says nothing about the environment; a person sets it when the node is placed.
UNKNOWN_ENVIRONMENT = "shared"
ACCOUNT_TYPE = "boundary.aws-account@1"
LOG_GROUP_TYPE = "observability.cloudwatch@1"


def boto_clients(service: str, region: str | None, credentials: Mapping[str, str] | None = None) -> Any:
    import boto3
    from botocore.config import Config

    # Retries are with_retries' job, so throttling is handled the same way for every connector.
    kwargs: dict[str, Any] = {"region_name": region, "config": Config(retries={"mode": "standard", "max_attempts": 1})}
    if credentials:
        kwargs.update(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"],
                      aws_session_token=credentials["SessionToken"])
    return boto3.client(service, **kwargs)


def _aws_errors() -> tuple[type[Exception], ...]:
    from botocore.exceptions import BotoCoreError, ClientError

    return BotoCoreError, ClientError


def _open_cursor(cursor: int | dict) -> tuple[int, set[str]]:
    if isinstance(cursor, dict):
        return int(cursor["t"]), set(cursor.get("seen") or [])
    return int(cursor), set()


class CloudWatch(Source):
    key = "cloudwatch"
    permissions = (
        Permission("sts:AssumeRole", "sign in as the read-only role you created for WhiteHats"),
        Permission("logs:FilterLogEvents", "read new events from the log groups you named"),
    )
    page_size: ClassVar[int] = PAGE_SIZE  # recordings use a small page so paging is exercised

    def __init__(self, config: Mapping[str, str], secret: Callable[[], str] = lambda: "", *,
                 clients: ClientFactory = boto_clients, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__(config, secret)
        self._clients, self._clock, self._sleep = clients, clock, sleep
        self._pacer = Pacer(1 / FILTER_CALLS_PER_SECOND, sleep=sleep)
        self._signed_in: tuple[Any, str] | None = None

    # -- the contract -------------------------------------------------------------------

    def test(self) -> ConnectionTest:
        _, identity = self._session()
        signed_in = Probe("sts:AssumeRole", True, f"Signed in as {identity}.")
        try:
            sample = len(self.read({}, limit=5).lines)
        except SourceError as exc:
            return ConnectionTest(identity, (signed_in, Probe("logs:FilterLogEvents", False, str(exc))), 0)
        reading = Probe("logs:FilterLogEvents", True, f"Read {sample} recent event(s).")
        return ConnectionTest(identity, (signed_in, reading), sample)

    def discover(self) -> Discovery:
        _, identity = self._session()
        account = identity.split(":")[4]
        region = self.config.get("region", "")
        account_id = f"aws_{account}"
        # arn, account and region let scope.split send each node to the projects whose scope holds it.
        nodes = [self._node(account_id, ACCOUNT_TYPE, f"AWS account {account}",
                            {"account_id": account, "arn": f"arn:aws:iam::{account}:root", "account": account})]
        edges = []
        for group in self._groups():
            self._filter(group, {"logGroupName": group, "startTime": 0, "limit": 1})  # proves it exists and is ours
            node_id = "loggroup_" + hashlib.sha256(f"{account}/{group}".encode()).hexdigest()[:16]
            nodes.append(self._node(node_id, LOG_GROUP_TYPE, group, {
                "log_groups": [group], "region": region, "account": account,
                "arn": f"arn:aws:logs:{region}:{account}:log-group:{group}"}))
            edges.append({"from": node_id, "to": account_id, "type": "contained_in"})
        return Discovery(nodes, edges)

    def read(self, bookmark: dict, limit: int | None = None) -> Batch:
        groups = self._groups()
        default_start = int((self._clock() - LOOKBACK_S) * 1000)
        cursors, lines = dict(bookmark), []
        for group in groups:
            cursors[group] = self._read_group(group, cursors.get(group, default_start), lines, limit)
            if limit and len(lines) >= limit:
                break
        return Batch(lines, bookmark if limit else cursors)

    # -- AWS ----------------------------------------------------------------------------

    def _groups(self) -> list[str]:
        groups = [g.strip() for g in self.config.get("log_groups", "").split(",") if g.strip()]
        if not groups:
            raise SourceError("List at least one CloudWatch log group.")
        return groups

    def _session(self) -> tuple[Any, str]:
        """The logs client under the customer's role, and the role's identity. Signs in once."""
        if self._signed_in:
            return self._signed_in
        role = self.config.get("role_arn")
        if not role:
            raise SourceError("CloudWatch needs a read-only role. Long-lived access keys are not accepted.")
        external = (self.config.get("external_id") or "").strip()
        if not external:
            # Without it, anyone who learns the role ARN could have WhiteHats read that account for them.
            raise SourceError("CloudWatch needs the external id the role was created with.")
        params = {"RoleArn": role, "RoleSessionName": "whitehats-reader", "ExternalId": external}
        region = self.config.get("region")
        try:
            sts = self._clients("sts", region, None)
            answer = with_retries(lambda: sts.assume_role(**params), aws_wait_hint, sleep=self._sleep)
            logs = self._clients("logs", region, answer["Credentials"])
        except _aws_errors() as exc:
            raise SourceError(f"AWS sign-in failed: {exc}") from exc
        self._signed_in = (logs, answer["AssumedRoleUser"]["Arn"])
        return self._signed_in

    def _filter(self, group: str, kwargs: dict) -> dict:
        logs, _ = self._session()

        def call() -> dict:
            self._pacer.wait()
            return logs.filter_log_events(**kwargs)

        try:
            return with_retries(call, aws_wait_hint, sleep=self._sleep)
        except _aws_errors() as exc:
            raise SourceError(f"Reading {group} failed: {exc}") from exc

    def _read_group(self, group: str, cursor: int | dict, lines: list[dict], limit: int | None) -> int | dict:
        """Append the group's new events and return where the next read starts.

        A finished group moves one millisecond past the last event. A group that still
        has another page keeps the exact millisecond and the event ids already taken,
        so a later event in that same millisecond is not skipped.
        """
        start, seen = _open_cursor(cursor)
        kwargs = {"logGroupName": group, "startTime": start, "limit": self.page_size}
        # Resuming inside a millisecond keeps the ids already taken there, or they would be read again.
        newest, kept = start, set(seen)
        caught_up = False
        for _ in range(PAGES_PER_GROUP):
            page = self._filter(group, kwargs)
            hit_limit = False
            for event in page.get("events", []):
                timestamp = int(event.get("timestamp", start))
                event_id = str(event.get("eventId") or "")
                if not event_id:
                    event_id = f"{event.get('logStreamName', '')}:{timestamp}:{event.get('message', '')}"
                if timestamp < start or (timestamp == start and event_id in seen):
                    continue
                lines.append({"source": f"cloudwatch:{group}/{event.get('logStreamName', '')}",
                              "message": event.get("message", "")})
                if timestamp > newest:
                    newest, kept = timestamp, set()
                if timestamp == newest:
                    kept.add(event_id)
                if limit and len(lines) >= limit:
                    hit_limit = True
                    break
            more = bool(page.get("nextToken")) and not hit_limit and len(lines) < MAX_EVENTS_PER_SYNC
            if not more:
                caught_up = not page.get("nextToken")
                break
            kwargs["nextToken"] = page["nextToken"]
        if caught_up:
            if kept:
                return newest + 1
            return start + 1 if seen else start
        if not kept:
            return cursor
        return {"t": newest, "seen": sorted(kept)}

    @staticmethod
    def _node(node_id: str, type_ref: str, name: str, properties: dict) -> dict:
        return {"id": node_id, "type": type_ref, "name": name, "environment": UNKNOWN_ENVIRONMENT,
                "provenance": {"kind": "discovered", "source": "cloudwatch"}, "properties": properties}

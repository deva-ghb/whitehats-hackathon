"""The connector contract as tests. Add a connector by adding a Case and recording its scenarios.

Each scenario is one recording under tests/recordings/connectors/<case>/<scenario>.json:
    test-ok          test() signs in and every probe passes
    signin-refused   test() fails to sign in, in plain words
    read-twice       read() from no bookmark (more than one page), then read() from the bookmark it returned
    throttled        read() is told to slow down once, waits, and finishes
    discover-ok      discover()
A source that takes pushed events (has_events) also records:
    events-forged    handle_event() with a forged signature makes no call and is refused
and its Case names the webhook secret its deliveries are signed with, plus a signed delivery to replay.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from connector_kit import HttpReplay, Replay

from app.connectors.aws import Aws
from app.connectors.base import Source
from app.connectors.cloudwatch import CloudWatch
from app.connectors.github import GitHub


@dataclass(frozen=True)
class Case:
    name: str
    build: Callable[[Any, Callable[[float], None]], Source]  # (recording replay, sleep) -> connector
    items_in: Callable[[dict], int]  # how many items a recording's answers hold, to check nothing is dropped
    replay: type = Replay  # Replay for boto sources, HttpReplay for HTTP sources
    webhook_secret: str = ""  # for a source with events: the secret its recorded deliveries are signed with
    delivery: tuple[str, bytes] = ("", b"")  # for a source with events: (event name, body) of one real delivery


class RecordedCloudWatch(CloudWatch):
    page_size = 2  # small pages, so the recordings page


CLOUDWATCH_CONFIG = {
    "region": "ap-southeast-2",
    "log_groups": "cloudtrail, /aws/lambda/payments",
    "role_arn": "arn:aws:iam::111122223333:role/whitehats-reader",
    "external_id": "recorded-external-id",
}


def cloudwatch(replay: Replay, sleep: Callable[[float], None], **config: str) -> CloudWatch:
    return RecordedCloudWatch({**CLOUDWATCH_CONFIG, **config}, clients=replay, clock=lambda: replay.clock,
                              sleep=sleep)


def cloudwatch_events(recording: dict) -> int:
    return sum(len(c.get("response", {}).get("events", [])) for c in recording["calls"]
               if c["method"] == "filter_log_events")


class RecordedAws(Aws):
    page_size = 2  # small pages, so the recordings page


AWS_CONFIG = {
    "region": "ap-southeast-2",
    "role_arn": "arn:aws:iam::111122223333:role/whitehats-reader",
    "external_id": "recorded-external-id",
    "regions": "us-west-2",
    "tag_key": "project",
    "tag_value": "whitehats-proving-ground",
    "log_groups": "/aws/lambda/shop-api",
}


def aws(replay: Replay, sleep: Callable[[float], None], **config: str) -> Aws:
    return RecordedAws({**AWS_CONFIG, **config}, clients=replay, clock=lambda: replay.clock, sleep=sleep)


def aws_events(recording: dict) -> int:
    """Distinct items in the recording. Activity history reads overlap (TRAIL_OVERLAP_S), so an event can be answered
    twice; it counts, and is read, once."""
    trail: set[str] = set()
    total = 0
    for call in recording["calls"]:
        body = call.get("response") or {}
        if call["method"] == "lookup_events":
            trail |= {str(event.get("EventId")) for event in body.get("Events") or []}
        elif call["method"] == "filter_log_events":
            total += len(body.get("events") or [])
    return total + len(trail)


class RecordedGitHub(GitHub):
    page_size = 2  # small pages, so the recordings page
    lookback_s = 6 * 3600  # the proving ground's commits are hours apart


GITHUB_CONFIG = {"installation_id": "12345678"}
GITHUB_WEBHOOK_SECRET = "recorded-webhook-secret"
# A push to the proving ground's main branch, in the shape GitHub sends it.
GITHUB_PUSH = (b'{"ref": "refs/heads/main", "before": "1b35a5e6558d136e55d82801fdbe470bbc9b46a6", '
               b'"after": "58d7dc787d3aea48e584737fa88f43d752913de8", "installation": {"id": 12345678}, '
               b'"repository": {"full_name": "deva-ghb/whitehats-proving-ground", "default_branch": "main", '
               b'"html_url": "https://github.com/deva-ghb/whitehats-proving-ground", "pushed_at": 1792774400}, '
               b'"commits": [{"id": "58d7dc787d3aea48e584737fa88f43d752913de8", '
               b'"message": "PG.1.4: Drop the unused repository local that tflint flagged (claude-1)", "author": {"name": "deva-ghb"}}]}')


def github(replay: HttpReplay, sleep: Callable[[float], None], **config: str) -> GitHub:
    return RecordedGitHub({**GITHUB_CONFIG, **config}, transport=replay, app_token=lambda: "recorded-app-token",
                          webhook_secret=lambda: GITHUB_WEBHOOK_SECRET, clock=lambda: replay.clock, sleep=sleep)


def github_commits(recording: dict) -> int:
    return sum(len(c["body"]) for c in recording["calls"]
               if c["method"] == "GET" and "/commits?" in c["url"] and c["status"] == 200)


CASES = [
    Case("cloudwatch", cloudwatch, cloudwatch_events),
    Case("aws", aws, aws_events),
    Case("github", github, github_commits, replay=HttpReplay, webhook_secret=GITHUB_WEBHOOK_SECRET,
         delivery=("push", GITHUB_PUSH)),
]

"""Records the CloudWatch connector's AWS answers for the contract tests.

    uv run python tests/record_cloudwatch.py                       # moto, the AWS simulator
    uv run python tests/record_cloudwatch.py --profile <read-only> # adds the refusal from a real account

Re-run against the proving ground's own log groups once it has them (PG.1), with a reader role.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Importing the app reads its settings; keep its data folder and keys out of the real one.
os.environ["WH_DATA_DIR"] = tempfile.mkdtemp(prefix="wh-record-")
os.environ["WH_LOAD_DOTENV"] = "0"
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]

import boto3
from connector_contract import CLOUDWATCH_CONFIG, RecordedCloudWatch
from connector_kit import PLACEHOLDER_ACCOUNT, RECORDINGS, Recorder
from moto import mock_aws

from app.connectors.base import SourceError
from app.connectors.cloudwatch import boto_clients

SIMULATED = "Recorded from moto, the AWS simulator, on {day}. Shapes are AWS's; the data is invented."
MOTO_ACCOUNT = "123456789012"
EVENTS = {
    "cloudtrail": [
        {"eventName": "StopLogging", "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::1:user/mallory"}},
        {"eventName": "ConsoleLogin", "userIdentity": {"type": "Root", "arn": "arn:aws:iam::1:root"},
         "responseElements": {"ConsoleLogin": "Success"}, "sourceIPAddress": "203.0.113.5"},
        {"eventName": "CreateAccessKey", "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::1:user/dev"}},
    ],
    "/aws/lambda/payments": [{"level": "error", "message": "payment provider timed out after 30s"}],
}


def _seed(clock: float) -> None:
    logs = boto3.client("logs", region_name=CLOUDWATCH_CONFIG["region"])
    for group, events in EVENTS.items():
        logs.create_log_group(logGroupName=group)
        logs.create_log_stream(logGroupName=group, logStreamName="s1")
        logs.put_log_events(logGroupName=group, logStreamName="s1", logEvents=[
            {"timestamp": int(clock * 1000) - 60_000 + i, "message": json.dumps(e)} for i, e in enumerate(events)])


def _record(name: str, clock: float, run, about: str, accounts: tuple[str, ...]) -> dict:
    recorder = Recorder(boto_clients, clock)
    # moto keeps each account apart, so the role is in moto's account; the scrubber maps it to the placeholder.
    role = CLOUDWATCH_CONFIG["role_arn"].replace(PLACEHOLDER_ACCOUNT, MOTO_ACCOUNT)
    source = RecordedCloudWatch({**CLOUDWATCH_CONFIG, "role_arn": role}, clients=recorder, clock=lambda: clock)
    try:
        run(source)
    except SourceError:
        pass  # a refusal is what some scenarios record
    path = recorder.save("cloudwatch", name, about, accounts)
    print(f"wrote {path.relative_to(RECORDINGS.parent.parent)} ({len(recorder.calls)} calls)")
    return json.loads(path.read_text())


def _read_twice(source: RecordedCloudWatch) -> None:
    first = source.read({})
    source.read(first.bookmark)


def _add_throttle(recording: dict, about: str) -> None:
    """Puts one throttling answer ahead of the first page of a recorded read."""
    calls = recording["calls"]
    first_page = next(i for i, c in enumerate(calls) if c["method"] == "filter_log_events")
    throttle = {key: calls[first_page][key] for key in ("service", "method", "params")}
    throttle["error"] = {"code": "ThrottlingException", "message": "Rate exceeded", "status": 400}
    path = RECORDINGS / "cloudwatch" / "throttled.json"
    data = {"about": about, "clock": recording["clock"], "calls": calls[:first_page] + [throttle] + calls[first_page:]}
    path.write_text(json.dumps(data, indent=2) + "\n")


def record_simulated() -> None:
    clock = float(int(time.time()))
    about = SIMULATED.format(day=time.strftime("%Y-%m-%d"))
    with mock_aws():
        _seed(clock)
        accounts = (MOTO_ACCOUNT,)
        _record("test-ok", clock, lambda s: s.test(), about, accounts)
        _record("read-twice", clock, _read_twice, about, accounts)
        _record("discover-ok", clock, lambda s: s.discover(), about, accounts)
        read_once = _record("throttled", clock, lambda s: s.read({}), about, accounts)
    _add_throttle(read_once, about + " The throttling answer is added by hand: AWS cannot be made to throttle on demand.")


def record_real_refusal(profile: str) -> None:
    session = boto3.Session(profile_name=profile)
    account = session.client("sts").get_caller_identity()["Account"]

    def clients(service, region, credentials=None):
        return session.client(service, region_name=region)

    clock = float(int(time.time()))
    recorder = Recorder(clients, clock)
    source = RecordedCloudWatch(CLOUDWATCH_CONFIG, clients=recorder, clock=lambda: clock)
    try:
        source.test()
    except SourceError:
        pass
    about = (f"Recorded from a real AWS account on {time.strftime('%Y-%m-%d')}: asking to assume a role this "
             "caller may not assume. Account ids are replaced.")
    path = recorder.save("cloudwatch", "signin-refused", about, (account,))
    print(f"wrote {path.name} ({len(recorder.calls)} calls)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", help="a read-only AWS profile, for the real refusal")
    args = parser.parse_args()
    record_simulated()
    if args.profile:
        record_real_refusal(args.profile)

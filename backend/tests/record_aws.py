"""Records the AWS connector's answers for the contract tests.

    uv run python tests/record_aws.py                       # moto, the AWS simulator
    uv run python tests/record_aws.py --profile <read-only> # adds the refusal from a real account

The CloudTrail lookup in the simulator is not implemented, so this script answers
that one call from a fixed list of events. Every other call is moto's own.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

os.environ["WH_DATA_DIR"] = tempfile.mkdtemp(prefix="wh-record-")
os.environ["WH_LOAD_DOTENV"] = "0"
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]

import boto3
from connector_contract import AWS_CONFIG, RecordedAws
from connector_kit import PLACEHOLDER_ACCOUNT, RECORDINGS, Recorder
from moto import mock_aws

from app.connectors.aws import _epoch
from app.connectors.base import SourceError
from app.connectors.cloudwatch import boto_clients

SIMULATED = "Recorded from moto, the AWS simulator, on {day}. Shapes are AWS's; the data is invented."
MOTO_ACCOUNT = "123456789012"
REGION = AWS_CONFIG["region"]
OTHER_REGION = AWS_CONFIG["regions"]
TAG = {"Key": "project", "Value": "whitehats-proving-ground"}
TRUST = json.dumps({"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
LOGS_POLICY = json.dumps({"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": "logs:FilterLogEvents", "Resource": "*"}]})


class _Lookup:
    """Answers LookupEvents. moto does not implement that action."""

    def __init__(self, client: object, events: list[dict]):
        self._client = client
        self._events = events

    def lookup_events(self, **kwargs: object) -> dict:
        start = _epoch(kwargs["StartTime"]) if kwargs.get("StartTime") else 0
        size = int(kwargs.get("MaxResults") or 50)
        offset = int(kwargs["NextToken"]) if kwargs.get("NextToken") else 0
        pending = [event for event in self._events if _epoch(event["EventTime"]) >= start]
        chunk = pending[offset:offset + size]
        answer = {"Events": chunk}
        if offset + size < len(pending):
            answer["NextToken"] = str(offset + size)
        return answer

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


def _events(clock: float) -> list[dict]:
    found = []
    for index, name in enumerate(("StopLogging", "ConsoleLogin", "CreateAccessKey")):
        found.append({
            "EventId": f"evt-{index}",
            "EventName": name,
            "EventTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock - 60 + index)),
            "CloudTrailEvent": json.dumps({"eventName": name}),
        })
    return found


def _bucket(name: str, *, project: str | None) -> None:
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION})
    if project:
        s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": [{"Key": "project", "Value": project}]})


def _seed(clock: float) -> None:
    iam = boto3.client("iam", region_name=REGION)
    iam.create_role(RoleName="whitehats-reader", AssumeRolePolicyDocument=TRUST)
    iam.create_role(RoleName="pg-shop", AssumeRolePolicyDocument=TRUST, Tags=[TAG])
    iam.put_role_policy(RoleName="pg-shop", PolicyName="pg-shop-logs", PolicyDocument=LOGS_POLICY)
    role = iam.get_role(RoleName="pg-shop")["Role"]["Arn"]
    for name in ("pg-receipts", "pg-trail", "pg-state"):
        _bucket(name, project="whitehats-proving-ground")
    _bucket("pg-other", project=None)
    _bucket("pg-foreign", project="other")
    boto3.client("lambda", region_name=REGION).create_function(
        FunctionName="shop-api", Runtime="python3.12", Role=role, Handler="app.handler",
        Code={"ZipFile": b"placeholder"}, Tags={"project": "whitehats-proving-ground"})
    boto3.client("dynamodb", region_name=REGION).create_table(
        TableName="orders", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}], Tags=[TAG])
    boto3.client("sqs", region_name=REGION).create_queue(QueueName="payment-jobs", tags={"project": "whitehats-proving-ground"})
    logs = boto3.client("logs", region_name=REGION)
    logs.create_log_group(logGroupName="/aws/lambda/shop-api", tags={"project": "whitehats-proving-ground"})
    logs.create_log_stream(logGroupName="/aws/lambda/shop-api", logStreamName="s1")
    logs.put_log_events(logGroupName="/aws/lambda/shop-api", logStreamName="s1", logEvents=[
        {"timestamp": int(clock * 1000) - 60_000 + index, "message": json.dumps({"event": index})}
        for index in range(3)])
    boto3.client("cloudtrail", region_name=REGION).create_trail(
        Name="shop", S3BucketName="pg-trail", TagsList=[TAG])
    _seed_other_region()


def _seed_other_region() -> None:
    """A network, an instance in it, a database and a security group (no type yet) in the second region."""
    tags = [TAG]
    ec2 = boto3.client("ec2", region_name=OTHER_REGION)
    vpc = ec2.create_vpc(CidrBlock="10.20.0.0/16", TagSpecifications=[{"ResourceType": "vpc", "Tags": tags}])
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.20.1.0/24")["Subnet"]["SubnetId"]
    ec2.create_security_group(GroupName="pg-admin", Description="admin", VpcId=vpc_id,
                              TagSpecifications=[{"ResourceType": "security-group", "Tags": tags}])
    image = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, InstanceType="t4g.nano", SubnetId=subnet,
                      TagSpecifications=[{"ResourceType": "instance", "Tags": tags}])
    boto3.client("rds", region_name=OTHER_REGION).create_db_instance(
        DBInstanceIdentifier="orders-db", Engine="postgres", DBInstanceClass="db.t4g.micro", AllocatedStorage=20,
        MasterUsername="pgadmin", MasterUserPassword=secrets.token_hex(12), Tags=tags)


def _clients(events: list[dict]):
    def factory(service: str, region: str | None, credentials: dict | None = None):
        client = boto_clients(service, region, credentials)
        if service == "cloudtrail":
            return _Lookup(client, events if region == REGION else [])
        return client

    return factory


def _record(name: str, clock: float, events: list[dict], run, about: str, *, allow_error: bool = False) -> dict:
    recorder = Recorder(_clients(events), clock)
    role = AWS_CONFIG["role_arn"].replace(PLACEHOLDER_ACCOUNT, MOTO_ACCOUNT)
    source = RecordedAws({**AWS_CONFIG, "role_arn": role}, clients=recorder, clock=lambda: clock,
                         sleep=lambda _seconds: None)
    try:
        run(source)
    except SourceError:
        if not allow_error:
            raise
    path = recorder.save("aws", name, about, (MOTO_ACCOUNT,))
    _replayable(path)
    print(f"wrote {path.relative_to(RECORDINGS.parent.parent)} ({len(recorder.calls)} calls)")
    return json.loads(path.read_text())


def _replayable(path: Path) -> None:
    """moto returns a few values the replay checker rejects. The connector does not use them."""
    data = json.loads(path.read_text())
    for call in data["calls"]:
        body = call.get("response")
        if not isinstance(body, dict):
            continue
        role = body.get("Role")
        if isinstance(role, dict) and isinstance(role.get("AssumeRolePolicyDocument"), dict):
            role["AssumeRolePolicyDocument"] = json.dumps(role["AssumeRolePolicyDocument"])
        if isinstance(body.get("PolicyDocument"), dict):
            body["PolicyDocument"] = json.dumps(body["PolicyDocument"])
        throughput = (body.get("Table") or {}).get("ProvisionedThroughput") or {}
        if throughput.get("NumberOfDecreasesToday") == 0:
            throughput["NumberOfDecreasesToday"] = 1
    path.write_text(json.dumps(data, indent=2) + "\n")


def _read_twice(source: RecordedAws) -> None:
    first = source.read({})
    source.read(first.bookmark)


def _add_throttle(recording: dict, about: str) -> None:
    calls = recording["calls"]
    first_page = next(index for index, call in enumerate(calls) if call["method"] == "lookup_events")
    throttle = {key: calls[first_page][key] for key in ("service", "method", "params")}
    throttle["error"] = {"code": "ThrottlingException", "message": "Rate exceeded", "status": 400}
    path = RECORDINGS / "aws" / "throttled.json"
    data = {"about": about, "clock": recording["clock"], "calls": calls[:first_page] + [throttle] + calls[first_page:]}
    path.write_text(json.dumps(data, indent=2) + "\n")


def record_simulated() -> None:
    clock = float(int(time.time()))
    about = SIMULATED.format(day=time.strftime("%Y-%m-%d"))
    events = _events(clock)
    with mock_aws():
        _seed(clock)
        _record("test-ok", clock, events, lambda source: source.test(), about)
        _record("read-twice", clock, events, _read_twice, about)
        _record("discover-ok", clock, events, lambda source: source.discover(), about)
        read_once = _record("throttled", clock, events, lambda source: source.read({}), about)
    _add_throttle(read_once, about + " The throttling answer is added by hand: AWS cannot be made to throttle on demand.")


def record_real_refusal(profile: str) -> None:
    session = boto3.Session(profile_name=profile, region_name=REGION)
    account = session.client("sts").get_caller_identity()["Account"]

    def clients(service, region, credentials=None):
        if credentials:
            return boto_clients(service, region or REGION, credentials)
        return session.client(service, region_name=region or REGION)

    clock = float(int(time.time()))
    recorder = Recorder(clients, clock)
    source = RecordedAws(AWS_CONFIG, clients=recorder, clock=lambda: clock, sleep=lambda _seconds: None)
    try:
        source.test()
    except SourceError:
        pass
    about = ("Recorded from a real AWS account on "
             f"{time.strftime('%Y-%m-%d')}: asking to assume a role this caller may not assume. Account ids are replaced.")
    path = recorder.save("aws", "signin-refused", about, (account,))
    print(f"wrote {path.name} ({len(recorder.calls)} calls)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", help="a read-only AWS profile, for the real refusal")
    args = parser.parse_args()
    record_simulated()
    if args.profile:
        record_real_refusal(args.profile)

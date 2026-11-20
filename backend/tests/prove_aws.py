"""Live proof for CN.4. Not part of the test suite.

`discover` assumes pg-whitehats-readonly and compares discovery to the tag query in every region read.
The signer profile is used only for AssumeRole. Reads use the assumed role. That role was made before the
external id was required and its trust policy does not check one; AWS accepts an external id the trust
policy does not ask for, so the connector sends a fresh random one (never printed) and the role ignores it.

`simulate` asks the IAM policy simulator (read-only) what the template's policy allows.

`template` creates the CloudFormation role, checks it, and deletes the stack.
The caller must already hold .agents/locks/pg-aws. The external id is never printed.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

os.environ["WH_DATA_DIR"] = tempfile.mkdtemp(prefix="wh-prove-aws-")
os.environ["WH_LOAD_DOTENV"] = "0"
sys.path[:0] = [str(Path(__file__).parents[1])]

import boto3
import yaml
from botocore.exceptions import ClientError

from app.connectors.aws import TEMPLATE_PATH, Aws
from app.connectors.aws_guard import ALLOWED, APIGATEWAY_RESOURCES
from app.connectors.aws_resources import tagged_arns
from app.connectors.base import SourceError
from app.connectors.cloudwatch import boto_clients

ACCOUNT = "509594260364"
REGION = "ap-southeast-2"
OTHER_REGIONS = "us-east-1"  # the estate is in one region; a second one shows every configured region is read
READONLY_ROLE = f"arn:aws:iam::{ACCOUNT}:role/pg-whitehats-readonly"
SIGNER_PROFILE = "nativesense"
READONLY_PROFILE = "nativesense-readonly"
STACK = "pg-whitehats-cn4"
ROLE_NAME = "pg-whitehats-cn4"
TAG_KEY = "project"
TAG_VALUE = "whitehats-proving-ground"
LOCK = Path("/Users/devamacha/localdisk/E/WhiteHats/.agents/locks/pg-aws/owner.yaml")
ABSENT_KEY = "whitehats-cn4-absent-object"
ABSENT_TABLE = "whitehats-cn4-absent"


def main() -> None:
    if "--template" in sys.argv:
        prove_template()
    elif "--simulate" in sys.argv:
        prove_template_policy()
    else:
        prove_discovery()


def prove_discovery() -> None:
    try:
        Aws({**_readonly_config(), "external_id": ""}, clients=_assumed_clients(SIGNER_PROFILE)).test()
    except SourceError as exc:
        print(f"without an external id: {exc}")
    source = Aws(_readonly_config(), clients=_assumed_clients(SIGNER_PROFILE))
    found = source.discover()
    discovered = tagged_arns(found)
    queried: set[str] = set()
    for region in source._regions():
        in_region = _tag_query(source._credentials, region)
        print(f"tag query {region} {len(in_region)}")
        queried |= in_region
    print(f"identity {source._identity}")
    print(f"readonly profile role {_role_name(_profile_identity(READONLY_PROFILE))}")
    print(f"discovery {len(discovered)} tag query {len(queried)} match {discovered == queried}")
    print("types", sorted({node["type"] for node in found.nodes}))
    if discovered != queried:
        print("only in discovery", sorted(discovered - queried))
        print("only in tag query", sorted(queried - discovered))
    result = source.test()
    print(f"probes {sum(1 for probe in result.probes if probe.ok)}/{len(result.probes)}")
    for probe in result.probes:
        print(f"  {probe.permission} ok={probe.ok} {_safe(probe.detail)}")
    batch = source.read({})
    print(f"read lines {len(batch.lines)} bookmark regions {sorted(batch.bookmark.get('cloudtrail') or {})}")
    _data_codes(source._credentials, _one_bucket(discovered))


def prove_template() -> None:
    if "claude-1" not in LOCK.read_text():
        raise SystemExit("pg-aws lock is not held by claude-1")
    external_id = secrets.token_urlsafe(24)
    admin = boto3.Session(profile_name=SIGNER_PROFILE)
    caller = admin.client("sts").get_caller_identity()["Arn"]
    print(f"template signer {caller}")
    cfn = admin.client("cloudformation", region_name=REGION)
    _delete_quiet(cfn)
    try:
        cfn.create_stack(
            StackName=STACK,
            TemplateBody=TEMPLATE_PATH.read_text(),
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Parameters=[
                {"ParameterKey": "RoleName", "ParameterValue": ROLE_NAME},
                {"ParameterKey": "TrustedPrincipal", "ParameterValue": f"arn:aws:iam::{ACCOUNT}:user/dev"},
                {"ParameterKey": "ExternalId", "ParameterValue": external_id},
            ],
        )
        cfn.get_waiter("stack_create_complete").wait(StackName=STACK, WaiterConfig={"Delay": 5, "MaxAttempts": 36})
        role = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
        creds = _assume(admin, role, external_id)
        print(f"template role assumed account {_account(creds)}")
        print(f"template tag query {len(_tag_query(creds))}")
        _data_codes(creds, _one_bucket(_tag_query(creds)))
    except Exception as exc:
        print(f"template failed {_safe(str(exc), external_id)}")
        _print_stack(cfn, external_id)
        raise
    finally:
        _delete_quiet(cfn)
        try:
            cfn.get_waiter("stack_delete_complete").wait(StackName=STACK, WaiterConfig={"Delay": 5, "MaxAttempts": 36})
            print("stack deleted")
        except Exception as exc:  # noqa: BLE001 - redact the external id before the message is printed
            print(f"stack delete wait {_safe(str(exc), external_id)}")


def prove_template_policy() -> None:
    """What the template's permission policy allows, by the IAM simulator. Nothing is created."""
    document = yaml.load(TEMPLATE_PATH.read_text(), Loader=_TemplateLoader)
    policy = document["Resources"]["ReadOnlyRole"]["Properties"]["Policies"][0]["PolicyDocument"]
    iam = boto3.Session(profile_name=READONLY_PROFILE).client("iam")
    needed = sorted(set(ALLOWED.values()) - {"sts:AssumeRole", "apigateway:GET"})
    text = json.dumps(policy)
    answers = _simulate(iam, text, needed, ["*"])
    print(f"connector actions allowed {sum(1 for d in answers.values() if d == 'allowed')}/{len(needed)}")
    api = "arn:aws:apigateway:ap-southeast-2::"
    for resource in (f"{api}/apis", f"{api}/apis/abc123", f"{api}/restapis", f"{api}/apikeys",
                     f"{api}/restapis/abc123/stages/prod/exports/oas30"):
        print(f"apigateway:GET {resource.removeprefix(api)} {_simulate(iam, text, ['apigateway:GET'], [resource])['apigateway:GET']}")
    for action in ("s3:GetObject", "dynamodb:GetItem", "dynamodb:PartiQLSelect", "ssm:GetParameter", "s3:PutObject",
                   "ec2:RunInstances"):
        print(f"{action} {_simulate(iam, text, [action], ['*'])[action]}")
    print("template resources for apigateway:GET", list(APIGATEWAY_RESOURCES))


def _simulate(iam, policy: str, actions: list[str], resources: list[str]) -> dict[str, str]:
    answer = iam.simulate_custom_policy(PolicyInputList=[policy], ActionNames=actions, ResourceArns=resources)
    return {item["EvalActionName"]: item["EvalDecision"] for item in answer["EvaluationResults"]}


class _TemplateLoader(yaml.SafeLoader):
    """Reads CloudFormation's short tags (!Ref, !GetAtt) as plain values."""


for _tag in ("!Ref", "!GetAtt"):
    _TemplateLoader.add_constructor(_tag, lambda loader, node: loader.construct_scalar(node))


def _readonly_config() -> dict:
    return {"region": REGION, "regions": OTHER_REGIONS, "role_arn": READONLY_ROLE, "tag_key": TAG_KEY,
            "tag_value": TAG_VALUE, "external_id": secrets.token_urlsafe(24)}


def _assumed_clients(profile: str):
    signer = boto3.Session(profile_name=profile)

    def clients(service: str, region: str, credentials: dict | None = None):
        if credentials is None:
            if service != "sts":
                raise RuntimeError(f"refusing {service} without assumed credentials")
            return signer.client("sts", region_name=region or REGION)
        return _client(service, region or REGION, credentials)

    return clients


def _assume(session, role: str, external_id: str) -> dict:
    answer = session.client("sts").assume_role(
        RoleArn=role, RoleSessionName="whitehats-cn4", ExternalId=external_id)
    return answer["Credentials"]


def _client(service: str, region: str, credentials: dict):
    return boto_clients(service, region, credentials)


def _tag_query(credentials: dict, region: str = REGION) -> set[str]:
    client = _client("resourcegroupstaggingapi", region, credentials)
    found: set[str] = set()
    token = ""
    for _ in range(20):
        kwargs = {"TagFilters": [{"Key": TAG_KEY, "Values": [TAG_VALUE]}], "ResourcesPerPage": 50}
        if token:
            kwargs["PaginationToken"] = token
        page = client.get_resources(**kwargs)
        found.update(item["ResourceARN"] for item in page.get("ResourceTagMappingList") or [])
        token = page.get("PaginationToken") or ""
        if not token:
            return found
    raise RuntimeError("tag query still had another page")


def _data_codes(credentials: dict, bucket: str) -> None:
    s3 = _client("s3", REGION, credentials)
    dynamo = _client("dynamodb", REGION, credentials)
    print(f"get_object {_code(lambda: s3.get_object(Bucket=bucket, Key=ABSENT_KEY))}")
    print(f"get_item {_code(lambda: dynamo.get_item(TableName=ABSENT_TABLE, Key={'id': {'S': '1'}}))}")


def _code(call) -> str:
    try:
        call()
    except ClientError as exc:
        return exc.response.get("Error", {}).get("Code", "error")
    return "allowed"


def _one_bucket(arns: set[str]) -> str:
    buckets = sorted(arn.split(":::")[-1] for arn in arns if arn.startswith("arn:aws:s3:::"))
    return buckets[0] if buckets else "whitehats-probe-absent"


def _profile_identity(profile: str) -> str:
    return boto3.Session(profile_name=profile).client("sts").get_caller_identity()["Arn"]


def _account(credentials: dict) -> str:
    return _client("sts", REGION, credentials).get_caller_identity()["Account"]


def _role_name(arn: str) -> str:
    resource = arn.split(":")[5]
    parts = resource.split("/")
    return parts[1] if len(parts) > 1 else resource


def _delete_quiet(cfn) -> None:
    try:
        cfn.delete_stack(StackName=STACK)
    except ClientError as exc:
        print(f"delete_stack {_safe(exc.response.get('Error', {}).get('Code', 'error'))}")


def _print_stack(cfn, external_id: str) -> None:
    try:
        events = cfn.describe_stack_events(StackName=STACK).get("StackEvents") or []
    except ClientError:
        return
    for event in events[:8]:
        status = event.get("ResourceStatus", "")
        reason = _safe(event.get("ResourceStatusReason") or "", external_id)
        print(f"  {event.get('LogicalResourceId')} {status} {reason}")


def _safe(text: str, secret: str = "") -> str:
    if secret and secret in text:
        return "redacted"
    if "AKIA" in text:
        return "redacted"
    return text[:400]


if __name__ == "__main__":
    main()

"""A fake AWS account for the browser tests (CN.5): moto, the AWS simulator, over HTTP, with IAM enforced.

    uv run python tests/fake_aws_server.py

Prints one JSON line with the address, an admin key for the app to sign in with, and the read-only
role to connect. Then it reads commands on stdin, one per line:

    revoke <iam action>    remove one action from the read-only role's policy, as a customer might
    delete-queue <name>    remove an SQS queue from the account, as a customer might

Nothing here reaches real AWS: every key is moto's own, and the app is pointed at this server with
AWS_ENDPOINT_URL.
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import boto3
from moto import settings
from moto.cloudtrail.responses import CloudTrailResponse
from moto.iam.access_control import IAMRequest
from moto.server import ThreadedMotoServer

REGION = "ap-southeast-2"
TAGS = [{"Key": "project", "Value": "fintech-shop"}]
ROLE = "whitehats-readonly"
POLICY = "whitehats-read-only"
# The role's allow list, from the CloudFormation template customers use. moto names two actions
# differently from IAM (the tag search and the API list), so those names are granted beside the real ones.
TEMPLATE = Path(__file__).resolve().parents[2] / "docs" / "connectors" / "aws-readonly-role.yaml"
MOTO_NAMES = ["tagging:GetResources", "apigateway:GetApis"]


def _lookup_events(self: CloudTrailResponse) -> str:
    """moto does not implement LookupEvents. An account with no recent management events answers this."""
    return json.dumps({"Events": []})


def _allowed_actions() -> list[str]:
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    for tag in ("!Ref", "!GetAtt"):
        Loader.add_constructor(tag, lambda loader, node: loader.construct_scalar(node))
    document = yaml.load(TEMPLATE.read_text(), Loader=Loader)
    statements = document["Resources"]["ReadOnlyRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    actions: list[str] = []
    for statement in statements:
        if statement["Effect"] == "Allow":
            listed = statement["Action"]
            actions.extend(listed if isinstance(listed, list) else [listed])
    return actions + MOTO_NAMES


def _policy(actions: list[str]) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": actions, "Resource": "*"}]})


def _seed(endpoint: str) -> tuple[dict, str]:
    """An estate, an admin user for the app's own sign-in, and the read-only role. Runs before IAM is enforced."""
    session = boto3.Session(aws_access_key_id="seed", aws_secret_access_key="seed", region_name=REGION)

    def client(service: str):
        return session.client(service, endpoint_url=endpoint)

    s3 = client("s3")
    s3.create_bucket(Bucket="fintech-receipts", CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_bucket_tagging(Bucket="fintech-receipts", Tagging={"TagSet": TAGS})
    client("dynamodb").create_table(
        TableName="orders", BillingMode="PAY_PER_REQUEST", Tags=TAGS,
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}])
    client("sqs").create_queue(QueueName="payment-jobs", tags={"project": "fintech-shop"})
    ec2 = client("ec2")
    vpc = ec2.create_vpc(CidrBlock="10.20.0.0/16", TagSpecifications=[{"ResourceType": "vpc", "Tags": TAGS}])
    subnet = ec2.create_subnet(VpcId=vpc["Vpc"]["VpcId"], CidrBlock="10.20.1.0/24")["Subnet"]["SubnetId"]
    image = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, InstanceType="t4g.nano", SubnetId=subnet,
                      TagSpecifications=[{"ResourceType": "instance", "Tags": [*TAGS, {"Key": "Name", "Value": "admin-box"}]}])
    ec2.create_security_group(GroupName="admin-ssh", Description="admin", VpcId=vpc["Vpc"]["VpcId"],
                              TagSpecifications=[{"ResourceType": "security-group", "Tags": TAGS}])
    iam = client("iam")
    iam.create_user(UserName="whitehats-runner")
    iam.put_user_policy(UserName="whitehats-runner", PolicyName="assume", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": "*"}]}))
    key = iam.create_access_key(UserName="whitehats-runner")["AccessKey"]
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": "*"},
                        "Action": "sts:AssumeRole"}]})
    role = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=trust)["Role"]["Arn"]
    iam.put_role_policy(RoleName=ROLE, PolicyName=POLICY, PolicyDocument=_policy(_allowed_actions()))
    if os.environ.get("FAKE_AWS_FUNCTION") == "1":
        # One function, for a browser journey that draws an edge from a repository to a discovered function. Off by default so
        # the account every other test reads keeps the same eight resources.
        code = io.BytesIO()
        with zipfile.ZipFile(code, "w") as archive:
            archive.writestr("app.py", "def handler(event, context):\n    return {}\n")
        client("lambda").create_function(FunctionName="orders-api", Runtime="python3.12", Role=role, Handler="app.handler",
                                         Code={"ZipFile": code.getvalue()}, Tags={"project": "fintech-shop"})
    if os.environ.get("FAKE_AWS_ESTATE") == "proving-ground":
        _seed_proving_ground(client, role)
    return key, role


def _seed_proving_ground(client, role: str) -> None:
    """The demo company's account at about the size the demo shows (an API, three functions, tables, buckets, queues,
    secrets, logs and roles), for layout and screenshot work on the map. Names follow the proving ground's Terraform."""
    tags = {"project": "whitehats-proving-ground"}
    s3 = client("s3")
    for what in ("receipts", "statements", "cloudtrail", "artifacts"):
        name = f"whitehats-proving-ground-509594260364-{what}"
        s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": [{"Key": "project", "Value": tags["project"]}]})
    for table in ("customers", "pg-orders", "idempotency-keys"):
        client("dynamodb").create_table(
            TableName=table, BillingMode="PAY_PER_REQUEST", Tags=[{"Key": "project", "Value": tags["project"]}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}])
    sqs = client("sqs")
    for queue in ("pg-payment-jobs", "pg-payment-jobs-dlq"):
        sqs.create_queue(QueueName=queue, tags=tags)
    ssm = client("ssm")
    for name in ("/whitehats-proving-ground/webhook-signing-secret", "/whitehats-proving-ground/api-token"):
        ssm.put_parameter(Name=name, Value="dummy", Type="SecureString", Tags=[{"Key": "project", "Value": tags["project"]}])
    logs = client("logs")
    code = io.BytesIO()
    with zipfile.ZipFile(code, "w") as archive:
        archive.writestr("app.py", "def handler(event, context):\n    return {}\n")
    iam = client("iam")
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {
        "Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    for fn in ("shop-api", "payments-worker", "gatekeeper"):
        fn_role = iam.create_role(RoleName=f"{fn}-role", AssumeRolePolicyDocument=trust)["Role"]["Arn"]
        client("lambda").create_function(FunctionName=fn, Runtime="python3.12", Role=fn_role or role,
                                         Handler="app.handler", Code={"ZipFile": code.getvalue()}, Tags=tags)
        logs.create_log_group(logGroupName=f"/aws/lambda/{fn}", tags=tags)
    for extra in ("pg-deployer", "pg-whitehats-readonly"):
        iam.create_role(RoleName=extra, AssumeRolePolicyDocument=trust)
    api = client("apigateway").create_rest_api(name="shop-api", tags=tags)
    client("apigateway").create_authorizer(restApiId=api["id"], name="api-token", type="TOKEN",
                                           authorizerUri="arn:aws:apigateway:ap-southeast-2:lambda:path/2015-03-31/"
                                           "functions/arn:aws:lambda:ap-southeast-2:123456789012:function:gatekeeper/"
                                           "invocations", identitySource="method.request.header.Authorization")


def main() -> None:
    CloudTrailResponse.lookup_events = _lookup_events
    # moto rebuilds the signature of a REST GET with a query string differently from botocore and refuses it.
    # This fake checks who is calling and what they may do, not the request signature.
    IAMRequest.check_signature = lambda self: None
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    key, role = _seed(endpoint)
    settings.INITIAL_NO_AUTH_ACTION_COUNT = 0  # from now on every call is checked against IAM, as in AWS
    print(json.dumps({"endpoint": endpoint, "region": REGION, "access_key_id": key["AccessKeyId"],
                      "secret_access_key": key["SecretAccessKey"], "role_arn": role}), flush=True)
    actions = _allowed_actions()
    for line in sys.stdin:
        command, _, action = line.strip().partition(" ")
        if command == "revoke":
            actions = [name for name in actions if name != action]
            # Written straight to moto's backend: the runner key may not change IAM, and should not.
            from moto.iam.models import iam_backends

            iam_backends["123456789012"]["global"].put_role_policy(ROLE, POLICY, _policy(actions))
            print(json.dumps({"revoked": action}), flush=True)
        elif command == "delete-queue":
            # The customer removes a queue from the account (STUDIO.6), straight in moto's backend as above.
            from moto.sqs.models import sqs_backends

            sqs_backends["123456789012"][REGION].delete_queue(action)
            print(json.dumps({"deleted": action}), flush=True)
    server.stop()


if __name__ == "__main__":
    main()

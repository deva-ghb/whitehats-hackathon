"""AWS connector: refused data reads, discovery that matches the tag query, and the role template."""

from pathlib import Path

import boto3
import pytest
import yaml
from library.resolve import ROUTING_PROPERTIES, load_library, resolve_clone
from moto import mock_aws

from app.connectors.aws import TEMPLATE_PATH, Aws
from app.connectors.aws_guard import ALLOWED, APIGATEWAY_RESOURCES, IMAGE_PULL
from app.connectors.aws_resources import assemble
from app.connectors.base import SourceError

REGION = "ap-southeast-2"
TAG = {"Key": "project", "Value": "whitehats-proving-ground"}
ROUTING = set(ROUTING_PROPERTIES)
CONFIG = {
    "region": REGION,
    "role_arn": "arn:aws:iam::123456789012:role/whitehats-reader",
    "external_id": "recorded-external-id",
    "tag_key": "project",
    "tag_value": "whitehats-proving-ground",
}


def test_reading_s3_objects_and_dynamodb_items_is_refused():
    calls = []

    def clients(service, region, credentials=None):
        calls.append((service, region))
        raise AssertionError("the call was not refused before it reached AWS")

    source = Aws(CONFIG, clients=clients, sleep=lambda _seconds: None)
    with pytest.raises(SourceError, match="refuses to read S3 objects"):
        source._call("s3", "get_object", Bucket="receipts", Key="r/1")
    with pytest.raises(SourceError, match="refuses to read DynamoDB items"):
        source._call("dynamodb", "get_item", TableName="orders", Key={"id": {"S": "1"}})
    with pytest.raises(SourceError, match="only reads"):
        source._call("s3", "put_object", Bucket="receipts", Key="r/1", Body=b"no")
    assert calls == []


def test_an_unmapped_resource_becomes_a_generic_node_and_discovery_goes_on():
    account = "111122223333"
    items = [{"ResourceARN": f"arn:aws:ec2:{REGION}:{account}:security-group/sg-1", "Tags": [TAG]},
             {"ResourceARN": f"arn:aws:sns:{REGION}:{account}:alerts", "Tags": []},
             {"ResourceARN": "arn:aws:s3:::pg-receipts", "Tags": [TAG]}]
    found = assemble(account, REGION, items, {})
    by_arn = {node["properties"].get("arn"): node for node in found.nodes}
    group = by_arn[items[0]["ResourceARN"]]
    assert group["type"] == "generic.network@1"
    assert group["properties"]["what_is_this"] == "AWS ec2 security-group"
    assert group["properties"]["vendor"] == "Amazon Web Services"
    assert by_arn[items[1]["ResourceARN"]]["type"] == "generic.messaging@1"
    assert by_arn[items[2]["ResourceARN"]]["type"] == "data.s3@1"
    library = load_library()
    for node in (group, by_arn[items[1]["ResourceARN"]]):
        declared = {prop["key"] for prop in library[node["type"].split("@")[0]]["properties"]}
        assert set(node["properties"]) - ROUTING <= declared, node


def test_instances_vpcs_rules_and_databases_have_their_own_types():
    account = "111122223333"
    vpc = f"arn:aws:ec2:{REGION}:{account}:vpc/vpc-1"
    arns = {
        "instance": f"arn:aws:ec2:{REGION}:{account}:instance/i-1",
        "vpc": vpc,
        "rule": f"arn:aws:events:{REGION}:{account}:rule/orders/nightly-stop",
        "bus": f"arn:aws:events:{REGION}:{account}:event-bus/orders",
        "postgres": f"arn:aws:rds:{REGION}:{account}:db:orders",
        "aurora": f"arn:aws:rds:{REGION}:{account}:cluster:ledger",
        "db2": f"arn:aws:rds:{REGION}:{account}:db:legacy",
    }
    extras = {
        arns["instance"]: {"size": "t4g.micro", "public_ip": False, "vpc_arn": vpc},
        arns["vpc"]: {"cidr": ["10.20.0.0/16"]},
        arns["postgres"]: {"engine": "postgres", "storage_encrypted": True, "iam_auth": False,
                           "backup_retention_days": 7, "instance_class": "db.t4g.micro", "publicly_accessible": False},
        arns["aurora"]: {"engine": "aurora-postgresql", "storage_encrypted": True, "iam_auth": True,
                         "backup_retention_days": 1},
        arns["db2"]: {"engine": "db2-se"},
    }
    found = assemble(account, REGION, [{"ResourceARN": arn, "Tags": [TAG]} for arn in arns.values()], extras)
    by_arn = {node["properties"]["arn"]: node for node in found.nodes}
    types = {name: by_arn[arn]["type"] for name, arn in arns.items()}
    assert types == {"instance": "compute.vm@1", "vpc": "boundary.vpc@1", "rule": "messaging.eventbridge@1",
                     "bus": "messaging.eventbridge@1", "postgres": "data.rds@1",
                     "aurora": "data.aurora-postgres@1", "db2": "generic.data@1"}
    assert by_arn[arns["instance"]]["properties"]["size"] == "t4g.micro"
    assert by_arn[arns["vpc"]]["properties"]["cidr"] == ["10.20.0.0/16"]
    assert by_arn[arns["rule"]]["properties"]["bus_name"] == "orders"
    assert by_arn[arns["rule"]]["properties"]["rules"] == ["nightly-stop"]
    inside = {(edge["from"], edge["to"]) for edge in found.edges if edge["type"] == "contained_in"}
    assert (by_arn[arns["instance"]]["id"], by_arn[vpc]["id"]) in inside
    specific = [node for node in found.nodes if not node["type"].startswith("generic.")]
    _, problems = resolve_clone(_document(specific, found.edges), load_library())
    assert problems == []


def test_aws_refuses_to_sign_in_without_an_external_id():
    calls = []

    def clients(service, region, credentials=None):
        calls.append(service)
        raise AssertionError("no call should be made without an external id")

    for missing in ("", "   "):
        source = Aws({**CONFIG, "external_id": missing}, clients=clients, sleep=lambda _seconds: None)
        with pytest.raises(SourceError, match="external id"):
            source.test()
    assert calls == []


@pytest.mark.parametrize("key", ["aws", "cloudwatch"])
def test_an_aws_connection_without_an_external_id_is_not_saved(client, ceo, key):
    row = next(c for c in client.get("/api/connectors", headers=ceo).json() if c["key"] == key)
    values = {"region": REGION, "role_arn": CONFIG["role_arn"], "log_groups": "/aws/lambda/shop-api"}
    refused = client.put(f"/api/connectors/{row['id']}", headers=ceo, json={"values": values})
    assert refused.status_code == 400
    assert "External ID" in refused.json()["detail"]
    after = next(c for c in client.get("/api/connectors", headers=ceo).json() if c["key"] == key)
    assert after["status"] == row["status"] and after["config"] == row["config"]


def test_every_configured_region_is_searched_and_read():
    regions = []

    def clients(service, region, credentials=None):
        regions.append((service, region))
        return _Answers(service, region)

    source = Aws({**CONFIG, "regions": "us-west-2, ap-southeast-2"}, clients=clients, clock=lambda: 1_700_000_000,
                 sleep=lambda _seconds: None)
    found = source.discover()
    assert {(service, region) for service, region in regions if service != "sts"} == {
        ("resourcegroupstaggingapi", REGION), ("resourcegroupstaggingapi", "us-west-2"), ("ec2", "us-west-2")}
    assert {node["properties"]["region"] for node in found.nodes if node["type"] == "compute.vm@1"} == {"us-west-2"}
    batch = source.read({})
    assert sorted(batch.bookmark["cloudtrail"]) == [REGION, "us-east-1", "us-west-2"]  # us-east-1: global services
    assert ("cloudtrail", "us-west-2") in regions


def test_global_services_are_read_in_us_east_1_and_nothing_else_there():
    import json as _json

    source = Aws(CONFIG, clients=lambda *a, **k: None, clock=lambda: 1_700_000_000, sleep=lambda _s: None)
    asked = []

    def call(service, method, *, region="", **kwargs):
        asked.append(region)
        if region != "us-east-1":
            return {"Events": []}
        return {"Events": [
            {"EventId": "1", "EventName": "CreateUser", "EventSource": "iam.amazonaws.com", "EventTime": 1_699_999_990,
             "CloudTrailEvent": _json.dumps({"eventName": "CreateUser"})},
            {"EventId": "2", "EventName": "RunInstances", "EventSource": "ec2.amazonaws.com", "EventTime": 1_699_999_991,
             "CloudTrailEvent": _json.dumps({"eventName": "RunInstances"})}]}

    source._call = call
    source._groups = list
    batch = source.read({})
    assert sorted(set(asked)) == [REGION, "us-east-1"]
    assert [line["source"] for line in batch.lines] == ["cloudtrail:us-east-1/CreateUser"]
    assert set(batch.bookmark["cloudtrail_seen"]["us-east-1"]) == {"1", "2"}  # both read once, never again


def test_discovery_matches_the_tag_query():
    with mock_aws():
        _estate()
        source = Aws(CONFIG, clock=lambda: 1_700_000_000, sleep=lambda _seconds: None)
        found = {arn for arn in _tag_query()}
        assert {item.split(":::")[-1] for item in found if item.startswith("arn:aws:s3:::")} == {"pg-receipts", "pg-state"}
        assert "pg-other" not in {arn.split(":::")[-1] for arn in found}
        assert _discovered(source) == found


def test_template_denies_object_and_item_reads_and_requires_an_external_id():
    document = _template()
    trust = document["Resources"]["ReadOnlyRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Action"] == "sts:AssumeRole"
    assert trust["Condition"]["StringEquals"]["sts:ExternalId"] == {"Ref": "ExternalId"}
    allowed, denied = _template_actions(document)
    assert {"s3:GetObject", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:PartiQLSelect",
            "sqs:ReceiveMessage"} <= denied
    assert not denied & set(allowed)


def test_template_allows_exactly_what_the_connector_calls():
    allowed, _denied = _template_actions(_template())
    # The sign-in is on the trust policy, not this policy; Docker pulls an image with the token, so its reads are here.
    needed = (set(ALLOWED.values()) - {"sts:AssumeRole"}) | set(IMAGE_PULL)
    assert set(allowed) == needed
    assert {permission.name for permission in Aws.permissions} <= needed | {"sts:AssumeRole"}
    for action, resources in allowed.items():
        expected = set(APIGATEWAY_RESOURCES) if action == "apigateway:GET" else {"*"}
        assert resources == expected, action


def _template_actions(document: dict) -> tuple[dict[str, set[str]], set[str]]:
    """{allowed action: the resources it is allowed on}, and the denied actions."""
    statements = document["Resources"]["ReadOnlyRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    allowed: dict[str, set[str]] = {}
    denied: set[str] = set()
    for statement in statements:
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        resources = statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]]
        for action in actions:
            if statement["Effect"] == "Deny":
                denied.add(action)
            else:
                allowed.setdefault(action, set()).update(resources)
    return allowed, denied


class _Answers:
    """Answers for one region: a tagged instance lives only in us-west-2, and every list is one page."""

    def __init__(self, service: str, region: str):
        self.service, self.region = service, region

    def assume_role(self, **_params):
        return {"Credentials": {}, "AssumedRoleUser": {"Arn": "arn:aws:sts::111122223333:assumed-role/r/s"}}

    def get_resources(self, **_params):
        if self.region != "us-west-2":
            return {"ResourceTagMappingList": []}
        arn = "arn:aws:ec2:us-west-2:111122223333:instance/i-west"
        return {"ResourceTagMappingList": [{"ResourceARN": arn, "Tags": [TAG]}]}

    def describe_instances(self, **params):
        assert params == {"InstanceIds": ["i-west"]}
        return {"Reservations": [{"Instances": [{"InstanceType": "t4g.nano"}]}]}

    def lookup_events(self, **_params):
        return {"Events": []}

    def describe_vpcs(self, **_params):
        return {"Vpcs": []}

    def describe_flow_logs(self, **_params):
        return {"FlowLogs": []}


def _document(nodes: list[dict], edges: list[dict]) -> dict:
    kept = {node["id"] for node in nodes}
    return {"schema_version": 1, "clone": "aws-types", "environment": "shared", "library_version": "2026.10",
            "version": {"number": 1, "author": "test", "reason": "types", "created_at": "2026-10-24T00:00:00Z"},
            "nodes": nodes, "edges": [edge for edge in edges if edge["from"] in kept and edge["to"] in kept],
            "groups": [], "access_paths": [], "credential_refs": []}


def _discovered(source: Aws) -> set[str]:
    from app.connectors.aws_resources import tagged_arns

    return tagged_arns(source.discover())


def _tag_query() -> list[str]:
    client = boto3.client("resourcegroupstaggingapi", region_name=REGION)
    found, token = [], None
    while True:
        kwargs = {"TagFilters": [{"Key": "project", "Values": ["whitehats-proving-ground"]}]}
        if token:
            kwargs["PaginationToken"] = token
        page = client.get_resources(**kwargs)
        found.extend(item["ResourceARN"] for item in page.get("ResourceTagMappingList") or [])
        token = page.get("PaginationToken")
        if not token:
            return found


def _estate() -> None:
    s3 = boto3.client("s3", region_name=REGION)
    for name, project in (("pg-receipts", "whitehats-proving-ground"), ("pg-state", "whitehats-proving-ground"),
                          ("pg-other", None)):
        s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION})
        if project:
            s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": [{"Key": "project", "Value": project}]})
    trust = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    iam = boto3.client("iam", region_name=REGION)
    iam.create_role(RoleName="whitehats-reader", AssumeRolePolicyDocument=trust)
    iam.create_role(RoleName="pg-shop", AssumeRolePolicyDocument=trust, Tags=[TAG])
    role = iam.get_role(RoleName="pg-shop")["Role"]["Arn"]
    boto3.client("lambda", region_name=REGION).create_function(
        FunctionName="shop-api", Runtime="python3.12", Role=role, Handler="app.handler",
        Code={"ZipFile": b"placeholder"}, Tags={"project": "whitehats-proving-ground"})


def _template() -> dict:
    class _Loader(yaml.SafeLoader):
        pass

    def _as_is(loader, node):
        value = loader.construct_scalar(node)
        return {node.tag[1:]: value}

    for tag in ("Ref", "GetAtt"):
        _Loader.add_constructor("!" + tag, _as_is)
    return yaml.load(Path(TEMPLATE_PATH).read_text(), Loader=_Loader)


def test_an_action_recorded_late_is_read_once_and_never_twice():
    """AWS adds actions to the activity history minutes late and out of order. An action that happened before one
    already read, but arrived after it, must still be read; a reread of the overlap must not bring anything twice."""
    from app.connectors import aws as aws_module

    history: list[dict] = []

    def call(service, method, region=None, **kwargs):
        assert (service, method) == ("cloudtrail", "lookup_events")
        start = aws_module._epoch(kwargs["StartTime"])
        return {"Events": [e for e in history if aws_module._epoch(e["EventTime"]) >= start]}

    source = object.__new__(aws_module.Aws)
    source._call = call
    t = 1_800_000_000.0

    def event(key, at):
        return {"EventId": key, "EventName": key, "EventTime": aws_module._iso(at), "CloudTrailEvent": "{}"}

    history.append(event("PutBucketPolicy", t))
    lines: list[dict] = []
    bookmark, seen = source._read_trail("r", aws_module._iso(t - 60), lines, None, {})
    assert [line["source"] for line in lines] == ["cloudtrail:r/PutBucketPolicy"]

    history.append(event("StopLogging", t - 120))  # happened two minutes earlier, recorded only now
    lines = []
    bookmark, seen = source._read_trail("r", bookmark, lines, None, seen)
    assert [line["source"] for line in lines] == ["cloudtrail:r/StopLogging"]

    lines = []
    source._read_trail("r", bookmark, lines, None, seen)
    assert lines == []


def test_changes_are_handed_on_oldest_first_so_a_reopening_after_an_undo_stays_open():
    """AWS answers newest first. Handed on that way, an SSH rule closed and then opened again in one read was seen
    opened, then closed: the alert closed while the rule was open. They are handed on in the order they happened."""
    from app.connectors import aws as aws_module

    t = 1_800_000_000.0
    newest_first = [{"EventId": name, "EventName": name, "EventTime": aws_module._iso(at), "CloudTrailEvent": "{}"}
                    for name, at in (("AuthorizeSecurityGroupIngress", t), ("RevokeSecurityGroupIngress", t - 30),
                                     ("AuthorizeSecurityGroupIngress#first", t - 60))]
    source = object.__new__(aws_module.Aws)
    source.page_size = 50
    source._call = lambda *_a, **_k: {"Events": newest_first}
    lines: list[dict] = []
    source._read_trail("r", aws_module._iso(t - 120), lines, None, {})
    assert [line["source"] for line in lines] == ["cloudtrail:r/AuthorizeSecurityGroupIngress#first",
                                                  "cloudtrail:r/RevokeSecurityGroupIngress",
                                                  "cloudtrail:r/AuthorizeSecurityGroupIngress"]


def test_a_bookmark_from_before_the_overlap_is_not_reread():
    from app.connectors import aws as aws_module

    starts: list[str] = []

    def call(service, method, region=None, **kwargs):
        starts.append(kwargs["StartTime"])
        return {"Events": []}

    source = object.__new__(aws_module.Aws)
    source._call = call
    source._read_trail("r", "2027-01-15T08:00:00Z", [], None, None)
    assert starts == ["2027-01-15T08:00:00Z"]


def test_only_changes_are_read_and_a_window_too_busy_to_read_whole_keeps_its_newest_and_moves_on():
    """A busy account (WhiteHats' own reads among them) once made every read fail at the page limit, so the bookmark
    never moved and no change was ever seen. Now only changes are asked for, and a window still too busy is cut."""
    from app.connectors import aws as aws_module

    asked = []

    def call(service, method, region=None, **kwargs):
        asked.append(kwargs)
        page = int(kwargs.get("NextToken") or 0)
        at = aws_module._iso(1_800_000_000 - page)
        return {"Events": [{"EventId": f"e{page}", "EventName": "PutBucketPolicy", "EventTime": at,
                            "CloudTrailEvent": "{}"}], "NextToken": str(page + 1)}  # never ends

    source = object.__new__(aws_module.Aws)
    source._call = call
    source.page_size = 1
    lines: list[dict] = []
    bookmark, seen = source._read_trail("r", aws_module._iso(1_799_990_000), lines, None, {})
    assert asked[0]["LookupAttributes"] == [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}]
    assert len(lines) == aws_module.MAX_PAGES and bookmark == aws_module._iso(1_800_000_001)
    assert "e0" in seen


class _Flow(_Answers):
    """Three networks: one logs to the log service, one to a bucket, one not at all."""

    def describe_vpcs(self, **_params):
        return {"Vpcs": [{"VpcId": "vpc-logs", "Tags": [{"Key": "Name", "Value": "shop"}]}, {"VpcId": "vpc-bucket"},
                         {"VpcId": "vpc-none"}]}

    def describe_flow_logs(self, **_params):
        return {"FlowLogs": [
            {"ResourceId": "vpc-logs", "FlowLogStatus": "ACTIVE", "LogDestinationType": "cloud-watch-logs",
             "LogGroupName": "/vpc/shop"},
            {"ResourceId": "vpc-bucket", "FlowLogStatus": "ACTIVE", "LogDestinationType": "s3"}]}

    def filter_log_events(self, **params):
        assert params["logGroupName"] == "/vpc/shop"
        return {"events": [{"timestamp": 1_700_000_000_000, "logStreamName": "eni-1",
                            "message": "2 1 eni-1 10.0.1.5 203.0.113.7 40000 4444 6 10 900 1 2 ACCEPT OK"}]}


def test_each_networks_traffic_log_is_found_and_the_log_service_ones_are_read():
    source = Aws(CONFIG, clients=lambda service, region, creds=None: _Flow(service, region),
                 clock=lambda: 1_700_000_100, sleep=lambda _s: None)
    batch = source.read({})
    assert [(f["vpc"], f["name"], f["destination"]) for f in source.flow_logs] == [
        ("vpc-logs", "shop", "logs"), ("vpc-bucket", "vpc-bucket", "s3"), ("vpc-none", "vpc-none", "none")]
    assert [line["source"] for line in batch.lines] == ["vpcflow:vpc-logs"]
    assert batch.bookmark["logs"]["/vpc/shop"] == 1_700_000_000_001
    # Read again a minute later: where each log goes is remembered, not asked again until FLOW_EVERY_S has passed.
    class _NotAskedAgain(_Flow):
        def describe_vpcs(self, **_params):
            raise AssertionError("asked again too soon")

    again = Aws(CONFIG, clients=lambda service, region, creds=None: _NotAskedAgain(service, region),
                clock=lambda: 1_700_000_160, sleep=lambda _s: None)
    again.read(batch.bookmark)
    assert [f["vpc"] for f in again.flow_logs] == ["vpc-logs", "vpc-bucket", "vpc-none"]

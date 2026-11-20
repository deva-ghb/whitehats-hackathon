"""Turn tagged AWS resources into clone nodes. Pure: the connector has already read AWS."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from .base import Discovery

ACCOUNT_TYPE = "boundary.aws-account@1"
REGION_TYPE = "boundary.aws-region@1"
# CloudWatch says nothing about the environment; a person sets it when the node is placed.
UNKNOWN_ENVIRONMENT = "shared"
STRUCTURAL_TYPES = frozenset({ACCOUNT_TYPE, REGION_TYPE})
AWS_VENDOR = "Amazon Web Services"

# Services whose tagged resources are all one kind.
_TYPES = {
    "s3": "data.s3@1",
    "dynamodb": "data.dynamodb@1",
    "sqs": "messaging.sqs@1",
    "logs": "observability.cloudwatch@1",
    "cloudtrail": "observability.cloudtrail@1",
    "apigateway": "edge.api-gateway@1",
    "ecr": "delivery.ecr@1",
    "ssm": "identity.ssm-parameter-store@1",
}
# Services that tag many kinds of resource, keyed by (service, the resource kind in the ARN).
# RDS is decided by its engine instead (_database_type).
_KINDS = {
    ("lambda", "function"): "service.function@2",
    ("iam", "role"): "identity.iam-role@1",
    ("ec2", "instance"): "compute.vm@1",
    ("ec2", "vpc"): "boundary.vpc@1",
    ("events", "rule"): "messaging.eventbridge@1",
    ("events", "event-bus"): "messaging.eventbridge@1",
}
RDS_TYPE = "data.rds@1"
AURORA_TYPE = "data.aurora-postgres@1"
# A resource with no type of its own still appears, as the Generic node of the closest family,
# so a person can see it and ask for a proper type. The default is a Generic service.
_GENERIC = {
    "ec2": "generic.network@1",
    "iam": "generic.identity@1",
    "kms": "generic.identity@1",
    "secretsmanager": "generic.identity@1",
    "sns": "generic.messaging@1",
    "events": "generic.messaging@1",
    "rds": "generic.data@1",
}
GENERIC_SERVICE = "generic.services@1"
# The library's RDS engine choices, matched as prefixes of AWS's names (sqlserver-ex, oracle-ee).
_RDS_ENGINES = ("postgres", "mysql", "mariadb", "sqlserver", "oracle")
_AURORA_ENGINES = ("aurora-postgresql", "aurora-mysql")


def tagged_arns(found: Discovery) -> set[str]:
    """Resource ARNs in a discovery, without the account and region nodes we add ourselves."""
    return {node["properties"]["arn"] for node in found.nodes if node["type"] not in STRUCTURAL_TYPES}


def assemble(account: str, home: str, mappings: list[dict], extras: dict[str, dict]) -> Discovery:
    """Nodes and edges for one account. `extras` is enrichment keyed by ARN."""
    nodes = [_account_node(account)]
    built = [_resource(item, account, home, extras.get(item["ResourceARN"], {})) for item in mappings]
    regions = list(dict.fromkeys(node["properties"]["region"] for node in built if node["properties"].get("region")))
    region_ids = {}
    for region in regions or [home]:
        region_ids[region] = _region_id(account, region)
        nodes.append(_region_node(region_ids[region], account, region))
    edges = [{"from": region_id, "to": nodes[0]["id"], "type": "contained_in"} for region_id in region_ids.values()]
    by_arn = {node["properties"]["arn"]: node["id"] for node in built}
    for node in built:
        nodes.append(node)
        extra = extras.get(node["properties"]["arn"], {})
        parent = by_arn.get(extra.get("vpc_arn", "")) or region_ids.get(node["properties"].get("region") or "")
        edges.append({"from": node["id"], "to": parent or nodes[0]["id"], "type": "contained_in"})
        role_arn = extra.get("execution_role", "")
        if role_arn in by_arn:
            edges.append({"from": node["id"], "to": by_arn[role_arn], "type": "runs_as"})
    return Discovery(nodes, edges)


def type_of(arn: str, extra: dict | None = None) -> str:
    """The clone type for an ARN, or a Generic type when the library has none for it yet."""
    service, _region, _owner, resource = split_arn(arn)
    if service == "rds":
        return _database_type(kind_of(resource), (extra or {}).get("engine", ""))
    known = _TYPES.get(service) or _KINDS.get((service, kind_of(resource)))
    return known or _GENERIC.get(service, GENERIC_SERVICE)


def _database_type(kind: str, engine: str) -> str:
    """RDS ARNs do not say the engine, so the describe call decides. An Aurora member instance is its cluster."""
    if kind in ("db", "cluster") and engine in _AURORA_ENGINES:
        return AURORA_TYPE
    if kind == "db" and engine.startswith(_RDS_ENGINES):
        return RDS_TYPE
    return _GENERIC["rds"]


def _resource(item: dict, account: str, home: str, extra: dict) -> dict:
    arn = item["ResourceARN"]
    service, region, _owner, resource = split_arn(arn)
    tags = {tag["Key"]: tag["Value"] for tag in item.get("Tags") or [] if tag.get("Key")}
    region = extra.get("region") or region or home
    type_ref = type_of(arn, extra)
    properties: dict[str, Any] = {"arn": arn, "account": account, "tags": tags}
    if region:
        properties["region"] = region
    if type_ref.startswith("generic."):
        properties.update({"what_is_this": f"AWS {service} {kind_of(resource) or 'resource'}", "vendor": AWS_VENDOR})
    else:
        properties.update(_fields(type_ref, service, resource, region, extra))
    # A Name tag is what the customer calls it (EC2 shows it the same way); the ARN's id is the fallback.
    return _node(_resource_id(arn), type_ref, tags.get("Name") or _name(service, resource, extra), properties)


def _fields(type_ref: str, service: str, resource: str, region: str, extra: dict) -> dict:
    """The type's own properties, read from the ARN and from what the describe calls returned."""
    if service == "s3":
        return {"bucket_name": resource, "region": region}
    if service == "dynamodb":
        return _with({"table_name": resource.split("/", 1)[-1], "region": region}, extra, "encryption")
    if service == "sqs":
        return _queue_fields(resource, region, extra)
    if service == "logs":
        name = _log_group(resource)
        signal = "cloudtrail" if "cloudtrail" in name else "application"
        return {"log_groups": [name], "region": region, "signals": [signal]}
    if service == "cloudtrail":
        return {"trail_name": resource.split("/")[-1], "region": region}
    if service == "apigateway":
        return _with({"api_id": resource.strip("/").split("/")[-1], "api_type": _api_type(resource), "region": region},
                     extra, "public", "rate_limit", "throttling_rps", "throttling_burst")
    if service == "ecr":
        return {"repository_name": resource.split("/", 1)[-1], "region": region}
    if service == "ssm":
        return {"parameter_path": "/" + resource.split("/", 1)[-1].lstrip("/"), "region": region}
    if type_ref == RDS_TYPE:
        return _database_fields(resource, region, extra)
    if type_ref == AURORA_TYPE:
        return _cluster_fields(resource, region, extra)
    return _KIND_FIELDS[(service, kind_of(resource))](resource, region, extra)


def _lambda_fields(_resource: str, region: str, extra: dict) -> dict:
    fields = {"platform": "aws_lambda", "runtime": extra.get("runtime") or "unknown", "region": region}
    return _with(fields, extra, "package_type", "memory_mb", "environment_variables", "image")


def _role_fields(resource: str, _region: str, extra: dict) -> dict:
    return _with({"role_name": resource.split("/")[-1]}, extra, "trusted_by", "attached_policies")


def _instance_fields(_resource: str, region: str, extra: dict) -> dict:
    fields = {"cloud": "aws", "size": extra.get("size") or "unknown", "region": region}
    return _with(fields, extra, "os", "public_ip", "instance_profile")


def _vpc_fields(_resource: str, region: str, extra: dict) -> dict:
    return {"cloud": "aws", "cidr": list(extra.get("cidr") or []), "region": region}


def _rule_fields(resource: str, region: str, _extra: dict) -> dict:
    parts = resource.split("/")[1:]  # rule/<name> on the default bus, rule/<bus>/<name> on another
    bus = parts[0] if len(parts) > 1 else "default"
    return {"bus_name": bus, "region": region, "rules": [parts[-1]]}


def _bus_fields(resource: str, region: str, _extra: dict) -> dict:
    return {"bus_name": resource.split("/", 1)[-1], "region": region}


_KIND_FIELDS: dict[tuple[str, str], Callable[[str, str, dict], dict]] = {
    ("lambda", "function"): _lambda_fields,
    ("iam", "role"): _role_fields,
    ("ec2", "instance"): _instance_fields,
    ("ec2", "vpc"): _vpc_fields,
    ("events", "rule"): _rule_fields,
    ("events", "event-bus"): _bus_fields,
}


def _database_fields(resource: str, region: str, extra: dict) -> dict:
    engine = next(name for name in _RDS_ENGINES if extra["engine"].startswith(name))
    fields = {"db_instance_identifier": resource.split(":", 1)[-1], "region": region, "engine": engine}
    return _with(fields, extra, "storage_encrypted", "publicly_accessible", "iam_auth", "backup_retention_days",
                 "instance_class")


def _cluster_fields(resource: str, region: str, extra: dict) -> dict:
    # A member instance names its cluster; a cluster ARN is the cluster itself.
    cluster = extra.get("cluster") or resource.split(":", 1)[-1]
    fields = {"cluster_identifier": cluster, "region": region, "engine": extra["engine"]}
    return _with(fields, extra, "storage_encrypted", "publicly_accessible", "iam_auth", "backup_retention_days")


def _with(fields: dict, extra: dict, *keys: str) -> dict:
    """Add the optional properties the describe calls found. Missing or empty ones are left out."""
    for key in keys:
        value = extra.get(key)
        if value is not None and value != [] and value != "":
            fields[key] = value
    return fields


def _queue_fields(resource: str, region: str, extra: dict) -> dict:
    name = resource.split("/")[-1]
    return _with({"queue_name": name, "region": region, "fifo": name.endswith(".fifo")}, extra, "encryption")


def queue_url(arn: str) -> str:
    """The queue URL the attributes call needs. The ARN does not include the URL."""
    _service, region, owner, name = split_arn(arn)
    return f"https://sqs.{region}.amazonaws.com/{owner}/{name}"


def _name(service: str, resource: str, extra: dict) -> str:
    if service == "s3":
        return resource
    if service == "logs":
        return _log_group(resource)
    if service == "lambda":
        return resource.split(":")[1] if resource.startswith("function:") else resource
    if extra.get("display_name"):
        return extra["display_name"]
    return resource.split("/")[-1].split(":")[-1] or resource


def _account_node(account: str) -> dict:
    return _node(f"awsacct_{account}", ACCOUNT_TYPE, f"AWS account {account}", {
        "account_id": account, "arn": f"arn:aws:iam::{account}:root", "account": account})


def _region_node(node_id: str, account: str, region: str) -> dict:
    return _node(node_id, REGION_TYPE, region, {
        "region": region, "arn": f"arn:aws:ec2:{region}:{account}:region/{region}", "account": account})


def _node(node_id: str, type_ref: str, name: str, properties: dict) -> dict:
    return {"id": node_id, "type": type_ref, "name": name, "environment": UNKNOWN_ENVIRONMENT,
            "provenance": {"kind": "discovered", "source": "aws"}, "properties": properties}


def _region_id(account: str, region: str) -> str:
    digest = hashlib.sha256(f"{account}/{region}".encode()).hexdigest()[:16]
    return f"awsreg_{digest}"


def _resource_id(arn: str) -> str:
    return "awsres_" + hashlib.sha256(arn.encode()).hexdigest()[:16]


def split_arn(arn: str) -> tuple[str, str, str, str]:
    """(service, region, account, resource) of an ARN."""
    bits = arn.split(":")
    service = bits[2] if len(bits) > 2 else ""
    region = bits[3] if len(bits) > 3 else ""
    owner = bits[4] if len(bits) > 4 else ""
    resource = ":".join(bits[5:]) if len(bits) > 5 else ""
    return service, region, owner, resource


def kind_of(resource: str) -> str:
    """The resource kind at the front of an ARN's resource part: instance/i-1 is instance, db:orders is db."""
    cuts = [at for at in (resource.find("/"), resource.find(":")) if at >= 0]
    return resource[:min(cuts)] if cuts else ""



def _log_group(resource: str) -> str:
    return resource.removeprefix("log-group:").removesuffix(":*")


def _api_type(resource: str) -> str:
    if resource.startswith("/restapis/"):
        return "rest"
    if "websocket" in resource:
        return "websocket"
    return "http"

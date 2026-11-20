"""The things a repository's Terraform declares, as clone nodes: functions, queues, tables, buckets and image repositories.

Each node carries only what the files say. A name or a value the files leave to the provider (an ARN, a generated id)
is left out, and a resource whose name cannot be worked out is reported and not drawn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .repo_terraform import Declared, references
from .terraform_state import ident, node_name, node_properties
from .terraform_types import mapping_for

# The attributes of a function that say where its code comes from. The folders they name are where the code is.
CODE_ATTRIBUTES = ("filename", "s3_bucket", "s3_key", "image_uri", "source_code_hash")
# Resources that configure a bucket and name it in their `bucket` attribute, besides the bucket resource itself.
_BUCKET_USERS = ("aws_s3_bucket", "aws_s3_bucket_public_access_block", "aws_s3_bucket_server_side_encryption_configuration",
                 "aws_s3_bucket_versioning", "aws_s3_bucket_ownership_controls", "aws_s3_bucket_lifecycle_configuration",
                 "aws_s3_bucket_policy", "aws_s3_bucket_logging", "aws_s3_object")


@dataclass
class Placed:
    """A node made from a declaration, and the declaration it came from."""

    body: dict
    declared: Declared | None = None
    file: str = ""  # the file that says it, for the node's provenance


@dataclass
class Estate:
    nodes: list[Placed] = field(default_factory=list)
    not_named: list[str] = field(default_factory=list)  # addresses whose name the files do not give

    def of_type(self, type_ref: str) -> list[Placed]:
        return [placed for placed in self.nodes if placed.body["type"] == type_ref]


def declared_estate(declared: list[Declared], *, environment: str, region: str | None, source: str) -> Estate:
    """Nodes for every function, queue, table and image repository, and for every bucket any resource names."""
    estate = Estate()
    for item in declared:
        if item.kind not in _DRAWN:
            continue
        attributes = _attributes(item, region)
        mapping = mapping_for(item.kind, attributes)
        name = node_name(attributes, item.address, item.kind)
        if mapping is None or name == item.address:
            estate.not_named.append(item.address)
            continue
        body = _body(item.address, name, mapping.type_ref, environment, f"{source}:{item.file}",
                     node_properties(mapping, attributes))
        _DRAWN[item.kind](item, body)
        estate.nodes.append(Placed(body, item, item.file))
    buckets, unnamed = _buckets(declared, environment, region, source)
    estate.nodes.extend(buckets)
    estate.not_named.extend(unnamed)
    return estate


def _attributes(item: Declared, region: str | None) -> dict:
    """The attributes whose value the files give. A block that holds nested blocks is not one."""
    values = {key: item.value(key) for key in item.body if key not in ("__is_block__", "__comments__")}
    values = {key: value for key, value in values.items() if value is not None and value != []}
    if region:
        values.setdefault("region", region)
    return values


def _body(address: str, name: str, type_ref: str, environment: str, source: str, properties: dict) -> dict:
    return {"id": ident("n", address), "type": type_ref, "name": name, "environment": environment,
            "provenance": {"kind": "imported", "source": source}, "properties": properties}


# -- what each kind adds to the properties the state importer would also read ---------------


def _function(item: Declared, body: dict) -> None:
    properties = body["properties"]
    if "package_type" not in properties and any(item.has(key) for key in ("filename", "s3_bucket")):
        properties["package_type"] = "zip"
    variables = [block.body for block in item.blocks("environment")]
    names = sorted(name for entry in variables for name in (entry.get("variables") or {}) if name not in ("__is_block__",))
    if names:
        properties["environment_variables"] = names  # names only: a value is not read


def _queue(item: Declared, body: dict) -> None:
    properties = body["properties"]
    dead = _dead_letter(item)
    if dead:
        properties["dlq"] = dead
    if item.has("kms_master_key_id"):
        properties["encryption"] = "sse_kms"
    elif item.value("sqs_managed_sse_enabled") is True:
        properties["encryption"] = "sse_sqs"


def _dead_letter(item: Declared) -> str:
    """The name of the queue this queue's redrive policy hands failed messages to."""
    policy = item.body.get("redrive_policy")
    for name in references(policy if isinstance(policy, str) else ""):
        kind, _dot, rest = name.partition(".")
        label = rest.partition(".")[0]
        if kind == "aws_sqs_queue" and (kind, label) in item.scope.resources:
            value = item.scope.resource_attribute(kind, label, "name")
            return value if isinstance(value, str) else ""
    return ""


def _table(item: Declared, body: dict) -> None:
    properties = body["properties"]
    recovery = [block.value("enabled") for block in item.blocks("point_in_time_recovery")]
    if recovery:
        properties["point_in_time_recovery"] = recovery[0] is True
    encryption = item.blocks("server_side_encryption")
    if encryption and encryption[0].value("enabled") is not False:
        properties["encryption"] = "customer_managed" if encryption[0].has("kms_key_arn") else "aws_managed"


def _repository(item: Declared, body: dict) -> None:
    properties = body["properties"]
    scanning = item.blocks("image_scanning_configuration")
    if scanning:
        properties["scan_on_push"] = scanning[0].value("scan_on_push") is True
    if item.has("image_tag_mutability"):
        properties["tag_immutability"] = item.value("image_tag_mutability") == "IMMUTABLE"


def _role(item: Declared, body: dict) -> None:
    """A role has nothing to add. It is drawn only when a workflow assumes it, which the scan decides."""


_DRAWN = {"aws_lambda_function": _function, "aws_sqs_queue": _queue, "aws_dynamodb_table": _table,
          "aws_ecr_repository": _repository, "aws_iam_role": _role}


# -- buckets: named by whatever configures them, never created by Terraform here ------------


def _buckets(declared: list[Declared], environment: str, region: str | None, source: str) -> tuple[list[Placed], list[str]]:
    """One node per bucket the files name, with what the files say about its protection. A bucket whose name they do not
    fully give (it holds the account id, and no account is known) is returned as an address, not drawn."""
    by_name: dict[str, list[Declared]] = {}
    unnamed: list[str] = []
    for item in declared:
        name = _bucket_name(item)
        if name:
            by_name.setdefault(name, []).append(item)
        elif _names_a_bucket(item):
            unnamed.append(item.address)
    placed = []
    for name, users in sorted(by_name.items()):
        properties = {"bucket_name": name, "arn": f"arn:aws:s3:::{name}", **({"region": region} if region else {})}
        properties.update(_protection(users))
        file = users[0].file
        body = _body(f"bucket.{name}", name, "data.s3@1", environment, f"{source}:{file}", properties)
        placed.append(Placed(body, None, file))
    return placed, unnamed


def _names_a_bucket(item: Declared) -> bool:
    return (item.kind in _BUCKET_USERS and item.has("bucket")) or (item.kind == "aws_cloudtrail" and item.has("s3_bucket_name"))


def _bucket_name(item: Declared) -> str:
    value = item.value("bucket") if item.kind in _BUCKET_USERS else item.value("s3_bucket_name") if item.kind == "aws_cloudtrail" else None
    return value if isinstance(value, str) and value else ""


def _protection(users: list[Declared]) -> dict:
    found: dict = {}
    for item in users:
        if item.kind == "aws_s3_bucket_public_access_block":
            checks = ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")
            found["public_access_block"] = all(item.value(key) is True for key in checks)
        elif item.kind == "aws_s3_bucket_versioning":
            found["versioning"] = any(block.value("status") == "Enabled" for block in item.blocks("versioning_configuration"))
        elif item.kind == "aws_s3_bucket_server_side_encryption_configuration":
            found.update(_encryption(item))
    return found


def _encryption(item: Declared) -> dict:
    for rule in item.blocks("rule"):
        for default in rule.blocks("apply_server_side_encryption_by_default"):
            algorithm = default.value("sse_algorithm")
            if algorithm in ("AES256", "aws:kms"):
                return {"encryption": "sse_s3" if algorithm == "AES256" else "sse_kms"}
    return {}

"""Connections a Terraform state spells out as attributes: a queue's dead-letter queue, a function's image, a resource's key.

Each rule reads one attribute that names another resource and draws one edge. A reference that names nothing in
the state (a key in another account, an image in a registry Terraform did not create) makes no edge.
"""

from __future__ import annotations

import json
from collections.abc import Callable

EdgeMaker = Callable[[str, str, str, str], dict]

# Resource type -> the attribute (a path through nested blocks) that holds the ARN or id of its customer key.
_KEY_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "aws_dynamodb_table": ("server_side_encryption", "kms_key_arn"),
    "aws_ssm_parameter": ("key_id",),
    "aws_sqs_queue": ("kms_master_key_id",),
    "aws_cloudwatch_log_group": ("kms_key_id",),
    "aws_lambda_function": ("kms_key_arn",),
    "aws_cloudtrail": ("kms_key_id",),
    "aws_db_instance": ("kms_key_id",),
}


def reference_edges(drafts: list[dict], by_arn: dict[str, dict], by_cloud: dict[str, dict], make_edge: EdgeMaker,
                    pending: list[dict] | None = None) -> list[dict]:
    """The dead-letter, image, key and trail-bucket connections of every node draft. A trail's bucket the state does not
    hold (the bucket was made outside Terraform) is added to `pending`."""
    by_url = {item["attributes"]["repository_url"]: item for item in drafts
              if item.get("resource_type") == "aws_ecr_repository" and item["attributes"].get("repository_url")}
    edges: list[dict] = []
    for item in drafts:
        if item.get("kind") != "node":
            continue
        for edge_type, target in _targets(item, by_arn, by_cloud, by_url):
            edges.append(make_edge(edge_type, item["body"]["id"], target["body"]["id"], item["address"]))
        bucket = _trail_bucket_arn(item)
        if bucket and bucket in by_arn:
            edges.append(make_edge("writes", item["body"]["id"], by_arn[bucket]["body"]["id"], item["address"]))
        elif bucket and pending is not None:
            pending.append({"from": item["body"]["id"], "type": "writes", "arn": bucket, "address": item["address"]})
    return edges


def _trail_bucket_arn(item: dict) -> str:
    """The ARN of the bucket a trail delivers to, which is built from the name the trail gives."""
    name = item["attributes"].get("s3_bucket_name") if item.get("resource_type") == "aws_cloudtrail" else None
    return f"arn:aws:s3:::{name}" if isinstance(name, str) and name else ""


def dead_letter_target(item: dict, by_arn: dict[str, dict]) -> dict | None:
    """The queue a queue hands its failed messages to, from its redrive policy."""
    if item.get("resource_type") != "aws_sqs_queue":
        return None
    try:
        policy = json.loads(item["attributes"].get("redrive_policy") or "{}")
    except ValueError:
        return None
    arn = policy.get("deadLetterTargetArn") if isinstance(policy, dict) else None
    return by_arn.get(arn) if isinstance(arn, str) else None


def dead_letter_arns(drafts: list[dict], by_arn: dict[str, dict]) -> frozenset[str]:
    """Every queue some other queue hands failed messages to. Nothing is meant to consume from these."""
    return frozenset(target["arn"] for item in drafts if (target := dead_letter_target(item, by_arn)) is not None)


def image_repository(uri: str) -> str:
    """The repository part of an image address: no tag and no digest, and a registry port is not a tag."""
    head, slash, tail = uri.partition("@")[0].rpartition("/")
    return head + slash + tail.partition(":")[0]


def _targets(item: dict, by_arn: dict, by_cloud: dict, by_url: dict) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    queue = dead_letter_target(item, by_arn)
    if queue is not None:
        found.append(("depends_on", queue))
    image = item["attributes"].get("image_uri")
    if isinstance(image, str) and image_repository(image) in by_url:
        found.append(("reads", by_url[image_repository(image)]))
    key = _key(item, by_arn, by_cloud)
    if key is not None:
        found.append(("depends_on", key))
    return [(kind, target) for kind, target in found if target is not item]


def _key(item: dict, by_arn: dict, by_cloud: dict) -> dict | None:
    path = _KEY_ATTRIBUTES.get(item.get("resource_type", ""))
    if path is None:
        return None
    for value in _values(item["attributes"], path):
        target = by_arn.get(value) or by_cloud.get(value)
        if target is not None and target.get("resource_type") == "aws_kms_key":
            return target
    return None


def _values(attributes: object, path: tuple[str, ...]) -> list[str]:
    """The text found by following `path` through nested blocks, which Terraform records as lists of objects."""
    if not path:
        return [attributes] if isinstance(attributes, str) else []
    blocks = attributes if isinstance(attributes, list) else [attributes]
    found: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            found.extend(_values(block.get(path[0]), path[1:]))
    return found

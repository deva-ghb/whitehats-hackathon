"""Who reads, writes and publishes to what, read from the IAM policies in a Terraform state.

A role's policy names the actions it allows and the resources they apply to. Every service that runs
as that role then reads, writes or publishes to those resources. These are the edges that answer
"who can reach the data", so they matter more than the policies drawn as boxes of their own.
"""

from __future__ import annotations

import json
from collections.abc import Callable

# Action verb prefix -> the edge it makes, per service. A verb the service does not list makes no edge.
_VERBS: dict[str, dict[str, str]] = {
    "s3": {"Get": "reads", "List": "reads", "Put": "writes", "Delete": "writes"},
    "dynamodb": {"Get": "reads", "BatchGet": "reads", "Query": "reads", "Scan": "reads", "Put": "writes",
                 "Update": "writes", "Delete": "writes", "BatchWrite": "writes"},
    "sqs": {"Send": "publishes_to", "Receive": "consumes_from"},
    "ssm": {"Get": "reads"},
    "kms": {"Decrypt": "reads", "GenerateDataKey": "reads"},
    "logs": {"Put": "emits_telemetry_to", "Create": "emits_telemetry_to"},
}
EdgeMaker = Callable[[str, str, str, str], dict]


def access_edges(policies: list[dict], drafts: list[dict], by_arn: dict[str, dict], make_edge: EdgeMaker,
                 dead_letters: frozenset[str] = frozenset(), pending: list[dict] | None = None) -> list[dict]:
    """One edge per service running as a role, per resource its policies allow it to touch.

    A resource the state does not hold (a bucket made outside Terraform) cannot be an end of an edge here. It is
    added to `pending` instead, to be connected when another source has put that resource in the clone.

    A policy lets a worker receive from its queue's dead-letter queue so a person can look at stuck messages. That
    is not consuming from it, so a dead-letter queue makes no `consumes_from` edge unless a trigger says so.
    """
    roles = {_role_key(item): item for item in drafts if item.get("resource_type") == "aws_iam_role"}
    found: list[dict] = []
    for resource in policies:
        attributes = resource["instance"].get("attributes") or {}
        role = roles.get(attributes.get("role"))
        if role is None:
            continue
        users = [item for item in drafts if item.get("kind") == "node" and role["arn"]
                 and item["attributes"].get("role") == role["arn"]]
        for edge_type, arn in _allowed(attributes.get("policy")):
            target = by_arn.get(arn) or by_arn.get(f"{arn}:*")  # older providers end a log group's ARN with :*
            if edge_type == "consumes_from" and arn in dead_letters:
                continue
            if target is None and pending is not None:
                pending.extend({"from": user["body"]["id"], "type": edge_type, "arn": arn, "address": resource["address"]}
                               for user in users)
            found.extend(make_edge(edge_type, user["body"]["id"], target["body"]["id"], resource["address"])
                         for user in users if target is not None and target is not user)
    return found


def _role_key(item: dict) -> str | None:
    return item["attributes"].get("name")


def _allowed(policy: object) -> list[tuple[str, str]]:
    """(edge type, resource ARN) for each allowed action on a named resource. A wildcard resource names nothing."""
    try:
        document = json.loads(policy) if isinstance(policy, str) else {}
    except ValueError:
        return []
    statements = document.get("Statement") if isinstance(document, dict) else None
    out: list[tuple[str, str]] = []
    for statement in statements if isinstance(statements, list) else [statements]:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue
        kinds = {kind for action in _as_list(statement.get("Action")) for kind in _edge_types(action)}
        for resource in _as_list(statement.get("Resource")):
            out.extend((kind, _thing(resource)) for kind in sorted(kinds) if resource != "*")
    return out


def _as_list(value: object) -> list[str]:
    return [value] if isinstance(value, str) else [item for item in value or [] if isinstance(item, str)]


def _edge_types(action: str) -> set[str]:
    service, _, verb = action.partition(":")
    rules = _VERBS.get(service.lower(), {})
    if verb == "*":
        return set(rules.values())
    wildcard = verb.endswith("*")
    stem = verb[:-1] if wildcard else verb
    return {edge for prefix, edge in rules.items()
            if stem.startswith(prefix) or (wildcard and prefix.startswith(stem))}


def _thing(arn: str) -> str:
    """The resource itself: a bucket and not its objects, a table and not its indexes, a log group and not a stream."""
    parts = arn.split(":")
    service = parts[2] if arn.startswith("arn:") and len(parts) > 2 else ""
    if service == "s3":
        return arn.split("/")[0]
    if service == "dynamodb":
        return arn.split("/index")[0]
    if service == "logs":
        return arn.removesuffix(":*").split(":log-stream")[0]
    return arn

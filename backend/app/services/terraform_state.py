"""Read a Terraform state file into clone nodes, groups and edges.

Only managed AWS and Azure resources are kept. A resource that exists only to
connect two others becomes an edge, not a node. Values that look like secrets
are left out. Reading the same state twice is an empty change set.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from library.secrets import secret_kinds

from .terraform_access import access_edges
from .terraform_links import dead_letter_arns, dead_letter_target, reference_edges
from .terraform_types import GENERIC, SETTINGS, Mapping, mapping_for, nameless_name

_ID = re.compile(r"^[a-z][a-z0-9_]{1,80}$")
_ACCEPTED_VERSIONS = {3, 4}

# These records are relationships. They are not drawn as their own nodes.
_LINKS = {"aws_lambda_event_source_mapping", "aws_apigatewayv2_integration", "aws_apigatewayv2_authorizer"}
# An API calls the function its integration or its authorizer points at: the attribute that holds the function's address.
_API_TO_FUNCTION = {"aws_apigatewayv2_integration": "integration_uri", "aws_apigatewayv2_authorizer": "authorizer_uri"}

def import_state(state: object, *, environment: str = "prod") -> dict[str, list[dict]]:
    """Nodes, groups and edges for one state. `environment` is the clone environment."""
    return _read(state, environment)[0]


def import_state_with_pending(state: object, *, environment: str = "prod") -> dict[str, list[dict]]:
    """The same, plus `pending`: connections to things the state does not hold (a bucket made outside Terraform).

    A pending connection names the ARN it is waiting for. `with_pending_resolved` makes it once another source has
    put that thing in the clone.
    """
    found, pending = _read(state, environment)
    return {**found, "pending": pending}


def _read(state: object, environment: str) -> tuple[dict[str, list[dict]], list[dict]]:
    drafts = []
    resources = _resources(_checked(state))
    for resource in resources:
        draft = _draft(resource, environment)
        if draft:
            drafts.append(draft)
    by_arn = {item["arn"]: item for item in drafts if item.get("arn")}
    by_cloud = {item["cloud_id"]: item for item in drafts if item.get("cloud_id")}
    pending: list[dict] = []
    edges = _edges(drafts, by_arn, by_cloud) + reference_edges(drafts, by_arn, by_cloud, _edge, pending)
    policies = [{**item, "address": _address(item)} for item in resources if item["type"] == "aws_iam_role_policy"]
    access = access_edges(policies, drafts, by_arn, _edge, dead_letter_arns(drafts, by_arn), pending)
    edges = list({edge["id"]: edge for edge in edges + access}.values())
    _parents(drafts)
    _name_dead_letters(drafts, by_arn)
    _alias_keys([{**item, "address": _address(item)} for item in resources if item["type"] == "aws_kms_alias"],
                by_arn, by_cloud)
    found = {
        "nodes": _bodies(drafts, "node"),
        "groups": _bodies(drafts, "group"),
        "edges": sorted(edges, key=lambda item: item["id"]),
    }
    return found, sorted(pending, key=lambda item: (item["from"], item["type"], item["arn"]))


def with_pending_resolved(incoming: dict, document: dict) -> dict:
    """The import with the connections to things another source has since put in the clone.

    The state names a bucket made outside Terraform only inside a policy or a trail. Once a repository scan or a connection
    has put that bucket in the clone (it is found by its ARN), the next read of the state connects it.
    """
    known = {}
    for field in ("groups", "nodes"):
        for body in document.get(field, []):
            arn = (body.get("properties") or {}).get("arn")
            if arn:
                known[arn] = body["id"]
    resolved = [_edge(item["type"], item["from"], known[item["arn"]], item["address"])
                for item in incoming.get("pending", []) if item["arn"] in known]
    rest = {key: value for key, value in incoming.items() if key != "pending"}
    return {**rest, "edges": sorted({edge["id"]: edge for edge in [*rest["edges"], *resolved]}.values(), key=lambda e: e["id"])}


def left_out(state: object) -> dict[str, int]:
    """Resource types read but not drawn, with how many of each: they configure another resource."""
    return dict(sorted(Counter(item["type"] for item in _resources(_checked(state)) if item["type"] in SETTINGS).items()))


def change_set(previous: dict[str, list[dict]], current: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Adds, removes and updates. Empty lists mean the two imports are the same estate."""
    return {key: _diff(previous.get(key, []), current.get(key, [])) for key in ("nodes", "groups", "edges")}


def _checked(state: object) -> list[dict]:
    if not isinstance(state, dict):
        raise TypeError("Terraform state must be an object")
    if state.get("version") not in _ACCEPTED_VERSIONS:
        raise ValueError("Terraform state version must be 3 or 4")
    resources = state.get("resources", [])
    if not isinstance(resources, list):
        raise TypeError("Terraform state resources must be a list")
    return resources


def _resources(resources: list[dict]) -> list[dict]:
    found = []
    for resource in resources:
        if not isinstance(resource, dict) or resource.get("mode") != "managed":
            continue
        kind = str(resource.get("type") or "")
        if not kind.startswith(("aws_", "azurerm_")):
            continue
        for instance in resource.get("instances") or []:
            if isinstance(instance, dict):
                found.append({**resource, "instance": instance})
    return found


def _draft(resource: dict, environment: str) -> dict | None:
    kind = resource["type"]
    if kind in _LINKS:
        return _link_draft(resource)
    if kind in SETTINGS:
        return None
    attributes = resource["instance"].get("attributes") or {}
    if not isinstance(attributes, dict):
        return None
    address = _address(resource)
    mapping = mapping_for(kind, attributes)
    if mapping is None:
        mapping = Mapping(GENERIC, fixed={"what_is_this": f"Terraform resource {kind}"})
    return _item("group" if mapping.group_kind else "node", address, environment, attributes, mapping, kind)


def _link_draft(resource: dict) -> dict:
    attributes = resource["instance"].get("attributes") or {}
    return {"link": True, "type_name": resource["type"], "address": _address(resource),
            "attributes": attributes if isinstance(attributes, dict) else {}}


def _item(kind: str, address: str, environment: str, attributes: dict, mapping: Mapping, resource_type: str) -> dict:
    body: dict[str, Any] = {
        "id": ident("g" if kind == "group" else "n", address),
        "type": mapping.type_ref,
        "name": node_name(attributes, address, resource_type),
        "environment": environment,
        "provenance": {"kind": "imported", "source": f"terraform:{address}"},
        "properties": node_properties(mapping, attributes),
    }
    if kind == "group":
        body["kind"] = mapping.group_kind
    return {
        "kind": kind,
        "body": body,
        "arn": _text(attributes.get("arn")),
        "cloud_id": _text(attributes.get("id")),
        "name": body["name"],
        "address": address,
        "resource_type": resource_type,
        "attributes": attributes,
    }


def _bodies(drafts: list[dict], kind: str) -> list[dict]:
    bodies = [item["body"] for item in drafts if item.get("kind") == kind]
    return sorted(bodies, key=lambda item: item["id"])


def node_properties(mapping: Mapping, attributes: dict) -> dict:
    """Only properties the library type declares, so the node resolves; secret-looking values are dropped."""
    props: dict[str, Any] = dict(mapping.fixed)
    region = _region(attributes)
    if mapping.region_key and region:
        props[mapping.region_key] = region
    for attribute, key, convert in mapping.fields:
        value = _plain(attributes.get(attribute))
        if value is not None and convert is not None:
            value = convert(value)
        if value is not None and value != "":  # an empty text is the state saying there is no value
            props[key] = value
    return props


def _plain(value: object) -> object | None:
    if isinstance(value, str):
        return None if secret_kinds(value) else value
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, list) and value and all(isinstance(item, str) and not secret_kinds(item) for item in value):
        return value
    return None


def node_name(attributes: dict, address: str, resource_type: str) -> str:
    """What a resource is called: its own name, else a tag, else what its Terraform label and kind say."""
    tags = attributes.get("tags") if isinstance(attributes.get("tags"), dict) else {}
    for key in ("function_name", "bucket", "name"):
        value = _text(attributes.get(key))
        if value and not secret_kinds(value):
            return value
    label = _text(tags.get("Name")) or _text(tags.get("name"))
    if label and not secret_kinds(label):
        return label
    return nameless_name(resource_type, address.rsplit(".", 1)[-1]) or address


def _region(attributes: dict) -> str:
    for key in ("region", "location"):
        value = _text(attributes.get(key))
        if value:
            return value
    arn = _text(attributes.get("arn"))
    parts = arn.split(":") if arn.startswith("arn:") else []
    if len(parts) > 3 and parts[3]:
        return parts[3]
    zone = _text(attributes.get("availability_zone"))
    return zone[:-1] if len(zone) > 1 else ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _address(resource: dict) -> str:
    parts = [part for part in (resource.get("module"), resource.get("type"), resource.get("name")) if part]
    address = ".".join(str(part) for part in parts)
    index = resource["instance"].get("index_key")
    return address if index is None else f"{address}[{index}]"


def ident(prefix: str, address: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "_", address.lower()).strip("_") or "item"
    # A dot is the address separator. Any other punctuation folds into the same underscore,
    # so "web-api" and "web_api" would share an id unless the original address is mixed in.
    if address.lower().replace(".", "_") != folded:
        folded = f"{folded}_{hashlib.sha256(address.encode()).hexdigest()[:8]}"
    made = f"{prefix}_{folded}"
    if _ID.fullmatch(made):
        return made
    digest = hashlib.sha256(address.encode()).hexdigest()[:10]
    head = re.sub(r"[^a-z0-9]+", "_", address.lower())[:40].strip("_")
    shortened = f"{prefix}_{head}_{digest}"
    return shortened if _ID.fullmatch(shortened) else f"{prefix}_{digest}"


def _edges(drafts: list[dict], by_arn: dict, by_cloud: dict) -> list[dict]:
    edges = []
    for item in drafts:
        if item.get("link"):
            edges.extend(_link_edges(item, by_arn, drafts))
            continue
        edges.extend(_direct_edges(item, by_arn, by_cloud, drafts))
    unique = {edge["id"]: edge for edge in edges}
    return list(unique.values())


def _direct_edges(item: dict, by_arn: dict, by_cloud: dict, drafts: list[dict]) -> list[dict]:
    attributes = item["attributes"]
    edges = []
    role = by_arn.get(_text(attributes.get("role")))
    if role and item.get("kind") == "node":
        edges.append(_edge("runs_as", item["body"]["id"], role["body"]["id"], item["address"]))
    for subnet_id in _subnet_ids(attributes):
        subnet = by_cloud.get(subnet_id)
        if subnet and subnet.get("kind") == "group":
            edges.append(_edge("contained_in", item["body"]["id"], subnet["body"]["id"], item["address"]))
    group = _named_group(drafts, "resource-group", _text(attributes.get("resource_group_name")))
    if group and item.get("kind") == "node":
        edges.append(_edge("contained_in", item["body"]["id"], group["body"]["id"], item["address"]))
    return edges


def _link_edges(item: dict, by_arn: dict, drafts: list[dict]) -> list[dict]:
    attributes = item["attributes"]
    if item["type_name"] == "aws_lambda_event_source_mapping":
        source = _find(drafts, by_arn, attributes.get("function_name"))
        target = by_arn.get(_text(attributes.get("event_source_arn")))
        if source and target:
            return [_edge("consumes_from", source["body"]["id"], target["body"]["id"], item["address"])]
    if item["type_name"] in _API_TO_FUNCTION:
        api = _by_cloud_id(drafts, _text(attributes.get("api_id")))
        function = _invoked(drafts, by_arn, _text(attributes.get(_API_TO_FUNCTION[item["type_name"]])))
        if api and function:
            return [_edge("calls", api["body"]["id"], function["body"]["id"], item["address"])]
    return []


def _invoked(drafts: list[dict], by_arn: dict, address: str) -> dict | None:
    """The function an API integration or authorizer points at: by its ARN, or by the invoke ARN API Gateway is given."""
    if address in by_arn:
        return by_arn[address]
    return next((item for item in drafts if item.get("kind") == "node" and address
                 and item["attributes"].get("invoke_arn") == address), None)


def _name_dead_letters(drafts: list[dict], by_arn: dict) -> None:
    """A queue says which queue takes its failed messages."""
    for item in drafts:
        target = dead_letter_target(item, by_arn)
        if target is not None:
            item["body"]["properties"]["dlq"] = target["name"]


def _alias_keys(aliases: list[dict], by_arn: dict, by_cloud: dict) -> None:
    """A key's alias is its name. A key the state gives no alias keeps the name its address gives it."""
    for alias in aliases:
        attributes = alias["instance"].get("attributes") or {}
        key = by_arn.get(_text(attributes.get("target_key_arn"))) or by_cloud.get(_text(attributes.get("target_key_id")))
        name = _text(attributes.get("name")).removeprefix("alias/")
        if key is not None and key.get("resource_type") == "aws_kms_key" and name and not secret_kinds(name):
            key["body"]["name"], key["name"] = name, name
            key["body"]["properties"]["key_alias"] = name


def _parents(drafts: list[dict]) -> None:
    by_cloud = {item["cloud_id"]: item for item in drafts if item.get("cloud_id") and item.get("kind") == "group"}
    for item in drafts:
        if item.get("kind") != "group":
            continue
        attributes = item["attributes"]
        parent = by_cloud.get(_text(attributes.get("vpc_id")))
        if parent is None:
            parent = _named_group(drafts, "vpc", _text(attributes.get("virtual_network_name")))
        if parent is None and item["body"]["kind"] != "resource-group":
            parent = _named_group(drafts, "resource-group", _text(attributes.get("resource_group_name")))
        if parent and parent["body"]["id"] != item["body"]["id"]:
            item["body"]["parent"] = parent["body"]["id"]


def _subnet_ids(attributes: dict) -> list[str]:
    found = []
    for block in _blocks(attributes.get("vpc_config")):
        found.extend(item for item in block.get("subnet_ids") or [] if isinstance(item, str))
    return found


def _blocks(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _find(drafts: list[dict], by_arn: dict, value: object) -> dict | None:
    text = _text(value)
    if text in by_arn:
        return by_arn[text]
    for item in drafts:
        if item.get("kind") == "node" and item.get("name") == text:
            return item
    return None


def _by_cloud_id(drafts: list[dict], cloud_id: str) -> dict | None:
    for item in drafts:
        if item.get("cloud_id") == cloud_id and item.get("kind"):
            return item
    return None


def _named_group(drafts: list[dict], group_kind: str, name: str) -> dict | None:
    if not name:
        return None
    for item in drafts:
        if item.get("kind") == "group" and item["body"].get("kind") == group_kind and item.get("name") == name:
            return item
    return None


def _edge(kind: str, source: str, target: str, address: str) -> dict:
    """An edge says which resource of the state it was read from, the same way a node does."""
    return {"id": ident("e", f"{kind}_{source}_{target}"), "from": source, "to": target, "type": kind,
            "provenance": {"kind": "imported", "source": f"terraform:{address}"}}


def _diff(previous: list[dict], current: list[dict]) -> list[dict]:
    before = {item["id"]: item for item in previous}
    after = {item["id"]: item for item in current}
    changes = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old is None:
            changes.append({"action": "add", "item": new})
        elif new is None:
            changes.append({"action": "remove", "item": old})
        elif old != new:
            changes.append({"action": "update", "before": old, "after": new})
    return changes

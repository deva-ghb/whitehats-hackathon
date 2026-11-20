"""Resolve every node and group of a clone document against the node library.

This is the P1 exit check: each item must name a type the library holds, at the
version it pins, and not a Generic type. Property values must fit the type, and
every edge must be one both of its ends allow.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from .clone_document import validate_clone
from .validate import library_root

DEFAULT_EXAMPLES = "examples"


@dataclass(frozen=True)
class Resolved:
    """One node or group and the library type it resolved to."""

    kind: str
    item_id: str
    name: str
    type_ref: str
    display_name: str
    family: str


def load_library(root: Path | None = None) -> dict[str, dict]:
    """Every type in the library, by id."""
    types_dir = (root or library_root()) / "types"
    library = {}
    for path in sorted(types_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        library[document["id"]] = document
    return library


def resolve_clone(document: dict, library: dict[str, dict]) -> tuple[list[Resolved], list[str]]:
    """Return the resolved items and one message per problem. A clone that fails its own schema is not resolved."""
    problems = validate_clone(document)
    if problems:
        return [], problems
    resolved: list[Resolved] = []
    types_by_item: dict[str, dict] = {}
    for kind, item in _items(document):
        type_doc, item_problems = _resolve_item(item, library)
        problems.extend(item_problems)
        if type_doc is None:
            continue
        types_by_item[item["id"]] = type_doc
        resolved.append(Resolved(kind, item["id"], item["name"], item["type"], type_doc["display_name"], type_doc["family"]))
    problems.extend(_edge_problems(document, types_by_item))
    return resolved, problems


def _items(document: dict):
    for group in document.get("groups", []):
        yield "group", group
    for node in document.get("nodes", []):
        yield "node", node


def _resolve_item(item: dict, library: dict[str, dict]) -> tuple[dict | None, list[str]]:
    label = f"{item['id']} ({item['name']})"
    type_id, version = item["type"].rsplit("@", 1)
    type_doc = library.get(type_id)
    if type_doc is None:
        return None, [f"{label}: type '{type_id}' is not in the library"]
    if int(version) != type_doc["version"]:
        return None, [f"{label}: pins {type_id} version {version}, the library has version {type_doc['version']}"]
    if type_doc.get("generic"):
        return None, [f"{label}: resolves only to the Generic type {type_id}"]
    return type_doc, _property_problems(label, item.get("properties", {}), type_doc)


# Where a thing is, not what it is: any node may carry these, so a connection can route it to the
# projects whose scope holds it (platform/backend/app/connectors/scope.py). A type's own key wins.
ROUTING_PROPERTIES = {
    key: {"key": key, "tier": "routing", "type": kind}
    for key, kind in (("arn", "string"), ("url", "url"), ("account", "string"), ("region", "string"),
                      ("tags", "map"), ("resource_group", "string"), ("repo", "string"), ("pipeline", "string"))
}


def _property_problems(label: str, values: dict, type_doc: dict) -> list[str]:
    declared = {**ROUTING_PROPERTIES, **{prop["key"]: prop for prop in type_doc["properties"]}}
    problems = []
    for key, value in values.items():
        prop = declared.get(key)
        if prop is None:
            problems.append(f"{label}: {type_doc['id']} has no property '{key}'")
        elif prop["tier"] == "secret":
            problems.append(f"{label}: '{key}' is a secret; bind it as the node's credential, not as a property")
        elif not _fits(value, prop):
            problems.append(f"{label}: '{key}' = {json.dumps(value)} is not a valid {_describe(prop)}")
    return problems


def _fits(value: object, prop: dict) -> bool:
    kind = prop["type"]
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "list":
        return isinstance(value, list)
    if kind == "enum":
        return value in prop["values"]
    if kind == "url":
        return isinstance(value, str) and value.startswith(("https://", "http://"))
    if kind == "map":
        return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
    return isinstance(value, str)


def _describe(prop: dict) -> str:
    if prop["type"] == "enum":
        return "choice of " + ", ".join(prop["values"])
    if prop["type"] == "map":
        return "map of names to text"
    return prop["type"]


def _edge_problems(document: dict, types_by_item: dict[str, dict]) -> list[str]:
    problems = []
    for edge in document.get("edges", []):
        source, target = types_by_item.get(edge["from"]), types_by_item.get(edge["to"])
        if source is None or target is None:
            continue
        # Design B4a: a Generic relation is the escape hatch, so it is always allowed.
        if edge["type"] == "generic_relation":
            continue
        name = f"{edge['from']} -{edge['type']}-> {edge['to']}"
        if edge["type"] not in source["edges"]["allowed_out"]:
            problems.append(f"{name}: {source['id']} cannot start a '{edge['type']}' edge")
        if edge["type"] not in target["edges"]["allowed_in"]:
            problems.append(f"{name}: {target['id']} cannot receive a '{edge['type']}' edge")
    return problems


def library_summary(library: dict[str, dict]) -> str:
    """Type counts by family, Generic types counted apart."""
    specific = Counter(doc["family"] for doc in library.values() if not doc.get("generic"))
    generic = sum(1 for doc in library.values() if doc.get("generic"))
    families = ", ".join(f"{family} {count}" for family, count in sorted(specific.items()))
    return f"library: {sum(specific.values())} types and {generic} Generic types; by family: {families}"


def main(argv: list[str] | None = None) -> int:
    """Print the resolution report for the named clone files (default: every example). Exit 1 on any problem."""
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [Path(arg) for arg in args] or sorted((library_root() / DEFAULT_EXAMPLES).glob("*.clone.json"))
    library = load_library()
    print(library_summary(library))
    failed = False
    for path in paths:
        resolved, problems = resolve_clone(json.loads(path.read_text()), library)
        failed = failed or bool(problems)
        _print_report(path, resolved, problems)
    return 1 if failed else 0


def _print_report(path: Path, resolved: list[Resolved], problems: list[str]) -> None:
    print(f"\n{path.name}: {len(resolved)} resolved, {len(problems)} problem(s)")
    for row in resolved:
        print(f"  {row.kind:5} {row.item_id:22} {row.name:34} {row.type_ref:34} {row.family}")
    for problem in problems:
        print(f"  PROBLEM {problem}")


if __name__ == "__main__":
    raise SystemExit(main())

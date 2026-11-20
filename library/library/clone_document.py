"""Validate a clone document against the Clone Studio schema.

The schema rejects shape. Secret-shaped values are rejected here. This module
also rejects duplicate ids and references that do not point at anything.
"""

from __future__ import annotations

import ipaddress
import json
import re
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .secrets import find_secret_values

_COLLECTIONS = ("nodes", "edges", "groups", "access_paths", "credential_refs")
_SCHEMA_NAME = "clone.schema.json"
_SECRET_NAME = re.compile(
    r"(?i)(^|_)(password|passwd|passphrase|secret_key|secret|api_key|apikey|token|private_key|access_key|connection_string|credentials)$"
)


def validate_clone(document: object) -> list[str]:
    """Return every problem with the document. An empty list means it is valid."""
    if not isinstance(document, dict):
        return ["clone document must be a JSON object"]
    errors = _schema_errors(document)
    if errors:
        return errors
    return (
        _duplicate_ids(document)
        + _unresolved_refs(document)
        + _group_loops(document)
        + find_secret_values(document)
        + _vault_refs(document)
        + _routes(document)
    )


def _schema_path() -> Path:
    """Repo layout keeps the schema next to the package; a wheel copies it inside."""
    packaged = Path(__file__).resolve().parent / "schemas" / _SCHEMA_NAME
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[1] / "schemas" / _SCHEMA_NAME


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(_schema_path().read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_errors(document: dict) -> list[str]:
    found = []
    for error in sorted(_validator().iter_errors(document), key=_error_path):
        where = "/".join(_error_path(error))
        prefix = f"{where}: " if where else ""
        found.append(f"{prefix}{_public_message(error)}")
    return found


def _error_path(error: ValidationError) -> list[str]:
    return [str(part) for part in error.absolute_path]


def _public_message(error: ValidationError) -> str:
    """Schema messages quote the bad value. A secret-named field must not be quoted."""
    path = _error_path(error)
    if path and _SECRET_NAME.search(path[-1]):
        return "must be a vault:// reference"
    return error.message


def _duplicate_ids(document: dict) -> list[str]:
    seen: dict[str, str] = {}
    errors = []
    for collection in _COLLECTIONS:
        for item in document[collection]:
            item_id = item.get("id")
            if not isinstance(item_id, str):
                continue
            previous = seen.get(item_id)
            if previous is not None:
                errors.append(f"duplicate id '{item_id}' on {previous} and {collection}")
            else:
                seen[item_id] = collection
    return errors


def _unresolved_refs(document: dict) -> list[str]:
    node_ids = _ids(document["nodes"])
    group_ids = _ids(document["groups"])
    path_ids = _ids(document["access_paths"])
    cred_ids = _ids(document["credential_refs"])
    endpoints = node_ids | group_ids
    errors: list[str] = []
    for index, node in enumerate(document["nodes"]):
        _expect(errors, node.get("location"), group_ids, f"nodes/{index}/location", "group")
        _expect(errors, node.get("access_path"), path_ids, f"nodes/{index}/access_path", "access path")
        credential = node.get("credential")
        if isinstance(credential, str) and not credential.startswith("vault://"):
            _expect(errors, credential, cred_ids, f"nodes/{index}/credential", "credential ref")
    for index, edge in enumerate(document["edges"]):
        _expect(errors, edge.get("from"), endpoints, f"edges/{index}/from", "node or group")
        _expect(errors, edge.get("to"), endpoints, f"edges/{index}/to", "node or group")
    for index, group in enumerate(document["groups"]):
        _expect(errors, group.get("parent"), group_ids, f"groups/{index}/parent", "group")
    return errors


def _group_loops(document: dict) -> list[str]:
    """A group cannot sit inside itself, however many groups apart: the Studio could not draw it."""
    parent = {g["id"]: g.get("parent") for g in document["groups"] if isinstance(g.get("id"), str)}
    errors = []
    for start, here in parent.items():
        seen = {start}
        while here in parent:
            if here in seen:
                errors.append(f"groups: '{start}' sits inside itself")
                break
            seen.add(here)
            here = parent[here]
    return errors


def _expect(errors: list[str], value: object, known: set[str], where: str, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or value not in known:
        errors.append(f"{where}: unknown {label} '{value}'")


def _routes(document: dict) -> list[str]:
    errors = []
    for index, path in enumerate(document.get("access_paths") or []):
        for route in path.get("routes") or []:
            if not isinstance(route, str):
                continue
            try:
                ipaddress.ip_network(route, strict=False)
            except ValueError:
                errors.append(f"access_paths/{index}/routes: '{route}' is not an IP network")
    return errors


def _ids(items: list[dict]) -> set[str]:
    return {item["id"] for item in items if isinstance(item.get("id"), str)}


def vault_problem(value: str) -> str | None:
    """Why a vault reference is unsafe, or None when it stays inside one company.

    The company slug is the second segment. Callers compare it to the company
    that submitted the document. P0.5 enforces that on the API.
    """
    if not value.startswith("vault://tenant/"):
        return "must start with vault://tenant/<company>/"
    parts = value[len("vault://") :].split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "has an empty or parent segment"
    company = parts[1] if len(parts) > 1 else ""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", company) or len(parts) < 3:
        return "must start with vault://tenant/<company>/"
    for part in parts[2:]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", part):
            return "has a segment that is not a path name"
    return None


def _vault_refs(document: dict) -> list[str]:
    errors = []
    for path, text in _walk_strings(document, ""):
        if not text.startswith("vault://"):
            continue
        problem = vault_problem(text)
        if problem:
            errors.append(f"{path}: vault reference {problem}")
    return errors


def _walk_strings(value: object, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path or "document", value)]
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            found.extend(_walk_strings(item, f"{path}/{key}" if path else str(key)))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(_walk_strings(item, f"{path}/{index}"))
        return found
    return []

"""Check node type YAML files against the node type schema, and the library as a whole.

Edge names are not listed in the node type schema. They have to be edge types
the clone document already allows, so the two cannot drift apart. ATT&CK ids are
checked against MITRE's published list (data/attack-enterprise.json, built by
library.attack), and every type must ship the deploy guide it names.
"""

from __future__ import annotations

import json
import sys
from functools import cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_SCHEMA_NAME = "node-type.schema.json"
_ATTACK_NAME = "attack-enterprise.json"
GUIDE_SECTIONS = ("## Prerequisites", "## Customer-side steps", "## Runner-side steps", "## Verification", "## Rollback")


def main(argv: list[str] | None = None) -> int:
    """Validate the library, or the files named on the command line. Exit 1 on any problem."""
    args = list(sys.argv[1:] if argv is None else argv)
    paths = _select(args)
    if not paths:
        print("no node types to check", file=sys.stderr)
        return 1
    errors = validate_paths(paths, whole_library=not args)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"{len(paths)} type(s) ok")
    return 0


def validate_paths(paths: list[Path], *, whole_library: bool = False) -> list[str]:
    """Return one message per problem. An empty list means every file is a valid type.

    whole_library adds the checks that need every type at once (one Generic type per family).
    """
    validator = _type_validator()
    known_edges = _edge_types()
    errors: list[str] = []
    documents: list[dict] = []
    for path in paths:
        document, problems = _load(path)
        errors.extend(problems)
        if document is None:
            continue
        errors.extend(_document_errors(path, document, validator, known_edges))
        documents.append(document)
    if whole_library:
        errors.extend(_generic_errors(documents))
    return errors


def library_root() -> Path:
    """The folder holding schemas/, types/, guides/ and data/: the repo's library/, or the package inside a wheel."""
    package_dir = Path(__file__).resolve().parent
    if (package_dir / "schemas" / _SCHEMA_NAME).is_file():
        return package_dir
    return package_dir.parent


def _select(args: list[str]) -> list[Path]:
    if args:
        return [Path(arg) for arg in args]
    return sorted(library_root().joinpath("types").glob("*.yaml"))


@cache
def _type_validator() -> Draft202012Validator:
    schema = json.loads((library_root() / "schemas" / _SCHEMA_NAME).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@cache
def _edge_types() -> set[str]:
    clone = json.loads((library_root() / "schemas" / "clone.schema.json").read_text())
    return set(clone["$defs"]["edge"]["properties"]["type"]["enum"])


@cache
def _families() -> tuple[str, ...]:
    schema = json.loads((library_root() / "schemas" / _SCHEMA_NAME).read_text())
    return tuple(schema["properties"]["family"]["enum"])


@cache
def attack_reference() -> dict:
    """MITRE's technique list: {"attack_version": ..., "techniques": {id: {name, retired, replaced_by}}}."""
    return json.loads((library_root() / "data" / _ATTACK_NAME).read_text())


def _load(path: Path) -> tuple[dict | None, list[str]]:
    if not path.is_file():
        return None, [f"{path}: file not found"]
    try:
        document = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return None, [f"{path}: {exc}"]
    if not isinstance(document, dict):
        return None, [f"{path}: a node type must be a YAML mapping"]
    return document, []


def _document_errors(path: Path, document: dict, validator: Draft202012Validator, known_edges: set[str]) -> list[str]:
    errors = _schema_errors(path, validator, document)
    errors.extend(_unknown_edges(path, document, known_edges))
    errors.extend(_file_name_errors(path, document))
    errors.extend(_property_key_errors(path, document))
    errors.extend(_attack_errors(path, document))
    errors.extend(_guide_errors(path, document))
    return errors


def _schema_errors(path: Path, validator: Draft202012Validator, document: dict) -> list[str]:
    found = []
    for error in sorted(validator.iter_errors(document), key=_error_path):
        where = "/".join(_error_path(error))
        prefix = f"{where}: " if where else ""
        found.append(f"{path}: {prefix}{error.message}")
    return found


def _error_path(error: ValidationError) -> list[str]:
    return [str(part) for part in error.absolute_path]


def _unknown_edges(path: Path, document: dict, known_edges: set[str]) -> list[str]:
    edges = document.get("edges")
    if not isinstance(edges, dict):
        return []
    errors = []
    for direction in ("allowed_out", "allowed_in"):
        names = edges.get(direction)
        if not isinstance(names, list):
            continue
        errors.extend(_unknown_in(path, direction, names, known_edges))
    return errors


def _unknown_in(path: Path, direction: str, names: list, known_edges: set[str]) -> list[str]:
    errors = []
    for name in names:
        if name not in known_edges:
            errors.append(f"{path}: edges.{direction}: unknown edge type '{name}'")
    return errors


def _file_name_errors(path: Path, document: dict) -> list[str]:
    type_id = document.get("id")
    if isinstance(type_id, str) and path.stem != type_id:
        return [f"{path}: the file must be named {type_id}.yaml"]
    return []


def _property_key_errors(path: Path, document: dict) -> list[str]:
    properties = document.get("properties")
    if not isinstance(properties, list):
        return []
    keys = [prop.get("key") for prop in properties if isinstance(prop, dict)]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    return [f"{path}: properties: key '{key}' appears more than once" for key in repeated]


def _attack_errors(path: Path, document: dict) -> list[str]:
    ids = document.get("attack_techniques")
    if not isinstance(ids, list):
        return []
    reference = attack_reference()
    errors = []
    for technique in ids:
        entry = reference["techniques"].get(technique)
        if entry is None:
            errors.append(f"{path}: attack_techniques: '{technique}' is not in ATT&CK {reference['attack_version']}")
        elif entry["retired"]:
            instead = f"; use {entry['replaced_by']}" if entry["replaced_by"] else ""
            errors.append(f"{path}: attack_techniques: '{technique}' ({entry['name']}) is retired{instead}")
    return errors


def _guide_errors(path: Path, document: dict) -> list[str]:
    name = document.get("deploy_guide_template")
    if not isinstance(name, str):
        return []
    guides = (library_root() / "guides").resolve()
    guide = (library_root() / name).resolve()
    if not guide.is_relative_to(guides):
        return [f"{path}: deploy_guide_template must point inside guides/"]
    if not guide.is_file():
        return [f"{path}: deploy guide {name} does not exist"]
    text = guide.read_text()
    return [f"{path}: deploy guide {name} has no '{section}' section" for section in GUIDE_SECTIONS if section not in text]


def _generic_errors(documents: list[dict]) -> list[str]:
    """Design 9.4: every family has exactly one Generic type, named generic.<family>, so nobody is ever blocked."""
    errors = []
    for family in _families():
        generic = [doc.get("id") for doc in documents if doc.get("generic") and doc.get("family") == family]
        expected = "generic." + family.replace("_", "-")
        if generic != [expected]:
            errors.append(f"family {family} needs exactly one Generic type named {expected}, found {generic or 'none'}")
    misnamed = [doc.get("id") for doc in documents if not doc.get("generic") and str(doc.get("id")).startswith("generic.")]
    errors.extend(f"{type_id}: only a Generic type may use the generic. prefix" for type_id in misnamed)
    return errors


if __name__ == "__main__":
    raise SystemExit(main())

"""Read a Terraform state file with the importer and check what comes out. Nothing is written anywhere.

Usage (from platform/backend):

    uv run python scripts/check_state_import.py ../../secrets/proving-ground.tfstate

Prints how many nodes, groups and edges the state gives, each connection by the names of its two ends, which
resources are held (no library type) or not drawn (settings), which required properties the state gives no value
for, whether the result resolves against the node library, and the provenance counts of a clone made by accepting
everything that is not held. Then the secret check: every value Terraform marks sensitive
in the state, and every string that looks like a secret, is searched for in the import. A reference to a secret
(an SSM parameter's name, an ARN) is not a secret and is kept. Only the paths of a hit are printed, never a value. Exit 1 when a secret value reaches the import or the import does not resolve.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.resolve import load_library, resolve_clone

from app.services import changesets
from app.services.terraform_secrets import leaks, secret_values
from app.services.terraform_state import import_state, left_out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("state", type=Path)
    parser.add_argument("--environment", default="prod")
    args = parser.parse_args()
    state = json.loads(args.state.read_text())
    imported = import_state(state, environment=args.environment)
    library = load_library()

    def generic(ref: str) -> bool:
        return bool(library.get(ref.rsplit("@", 1)[0], {}).get("generic"))

    def declared(ref: str) -> set[str]:
        return {prop["key"] for prop in library[ref.rsplit("@", 1)[0]]["properties"]}

    print(f"nodes {len(imported['nodes'])}, groups {len(imported['groups'])}, edges {len(imported['edges'])}")
    print("node types:", dict(Counter(node["type"] for node in imported["nodes"])))
    print("edge types:", dict(Counter(edge["type"] for edge in imported["edges"])))
    names = {item["id"]: item["name"] for item in imported["nodes"] + imported["groups"]}
    for edge in imported["edges"]:
        print(f"  {names[edge['from']]} {edge['type']} {names[edge['to']]}  ({edge['provenance']['source']})")
    held = [node["name"] for node in imported["nodes"] if generic(node["type"])]
    print(f"held (no library type): {len(held)}", held)
    print("read but not drawn (settings):", left_out(state))
    for name, key in unfilled(imported["nodes"], library):
        print(f"  the state gives no value for {name}: {key} (required, so the node stays Draft until a person or a scan fills it)")
    items = changesets.build(imported, _blank(args.environment), owners=("terraform",), is_generic=generic)
    ready = [item["key"] for item in items if not item["held"]]
    accepted = changesets.decide(items, ready, "accept")
    clone, report = changesets.apply_accepted(_blank(args.environment), accepted, declared=declared, is_generic=generic)
    _resolved, problems = resolve_clone(clone, library)
    print("clone after accepting everything not held:", report["added"], "added,", report["left_out_edges"], "edges left out")
    print("provenance counts:", changesets.provenance_counts(clone))
    print(f"resolves against the node library: {not problems}", problems[:5])
    text = json.dumps(imported) + json.dumps(items) + json.dumps(clone)
    leaked = leaks(state, text)
    secrets, short = secret_values(state)
    print(f"real secret values in the state (Terraform's sensitive values, and strings that look like secrets): {len(secrets)}"
          f" (and {short} too short to search for); reaching the import: {len(leaked)}", leaked)
    return 1 if problems or leaked else 0


def unfilled(nodes: list[dict], library: dict) -> list[tuple[str, str]]:
    """(node name, property) for each required property of a library type that the import leaves empty."""
    out = []
    for node in nodes:
        declared = library.get(node["type"].rsplit("@", 1)[0], {})
        if declared.get("generic"):
            continue
        out.extend((node["name"], prop["key"]) for prop in declared.get("properties", [])
                   if prop.get("tier") == "required" and prop["key"] not in node.get("properties", {}))
    return out


def _blank(environment: str) -> dict:
    return {"schema_version": 1, "clone": "check", "environment": environment, "library_version": "2026.10",
            "version": {"number": 1, "author": "check", "reason": "check"},
            "nodes": [], "edges": [], "groups": [], "access_paths": [], "credential_refs": []}


if __name__ == "__main__":
    raise SystemExit(main())

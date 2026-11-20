"""Check an exported clone document the way the server does: the clone schema, the secret and vault rules, and the node library.

Usage (from platform/backend, so the library is importable):

    uv run python ../e2e/check_clone.py <path to a .clone.json file>

Prints the counts by provenance and how many nodes are Described (every required property filled). Exit 1 on any problem.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from library.clone_document import validate_clone
from library.resolve import load_library, resolve_clone


def main() -> int:
    document = json.loads(Path(sys.argv[1]).read_text())
    library = load_library()
    problems = validate_clone(document)
    _resolved, unresolved = resolve_clone(document, library)
    problems += unresolved
    items = document["nodes"] + document["groups"]
    undescribed = [item["name"] for item in items if _missing(item, library)]
    provenance = Counter(item.get("provenance", {}).get("kind", "not recorded") for item in items)
    edge_provenance = Counter(edge.get("provenance", {}).get("kind", "not recorded") for edge in document["edges"])
    print(json.dumps({
        "clone": document["clone"], "nodes": len(document["nodes"]), "groups": len(document["groups"]), "edges": len(document["edges"]),
        "described": len(items) - len(undescribed), "not_described": undescribed,
        "without_owner": [item["name"] for item in items if not item.get("owner")],
        "without_notes": [item["name"] for item in items if not item.get("notes")],
        "item_provenance": dict(provenance), "edge_provenance": dict(edge_provenance), "problems": problems,
    }, indent=2))
    return 1 if problems or undescribed else 0


def _missing(item: dict, library: dict) -> list[str]:
    type_id = item["type"].rsplit("@", 1)[0]
    values = item.get("properties", {})
    required = [prop["key"] for prop in library[type_id]["properties"] if prop["tier"] == "required"]
    return [key for key in required if values.get(key) in (None, "", [])]


if __name__ == "__main__":
    raise SystemExit(main())

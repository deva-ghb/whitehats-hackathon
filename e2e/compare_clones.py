"""Compare the hand-built proving-ground clone (P2.5) with the clone imported from the real Terraform state (PG.3).

Usage (from platform/backend, so the node library is importable):

    uv run python ../e2e/compare_clones.py <hand.clone.json> <imported.clone.json>

Prints, as JSON: the counts of nodes, groups and edges by provenance in each clone; what the two share (matched by the table below,
because a person and a state name the same thing differently); what only the hand-built clone has; what only the import has; the
properties that differ on matched nodes; and how many items of each clone are Described. Nothing is written.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from library.resolve import load_library

# The hand-drawn name of each thing the state also holds, and the name the state gave it. Written by reading both, not guessed by code.
SAME_THING = {
    "shop-http-api": "shop-api (HTTP API)",
    "gatekeeper": "shop-api-authorizer",
    "webhook-signing-secret": "/whitehats-proving-ground/webhook-signing-secret",
    "api-token": "/whitehats-proving-ground/api-token",
    "whitehats-proving-ground repository": "deva-ghb/whitehats-proving-ground",
    "GitHub Actions workflows": "deva-ghb/whitehats-proving-ground workflows",
    "receipts": "whitehats-proving-ground-509594260364-receipts",
    "trail bucket": "whitehats-proving-ground-509594260364-trail",
    "artifacts bucket": "whitehats-proving-ground-509594260364-artifacts",
    "shop-api log group": "/aws/lambda/shop-api",
    "gatekeeper log group": "/aws/lambda/shop-api-authorizer",
    "payments-worker log group": "/aws/lambda/payments-worker",
    "audit trail": "whitehats-proving-ground",
    "payments-worker image repository": "whitehats-proving-ground/payments-worker",
    "Proving-ground AWS account": "AWS account 509594260364",  # live discovery (PG.3 on the deployed platform) names the account by its number
}
SAME_NAME = ["data key", "shop-api (function)", "payments-worker", "customers", "orders", "pg-gatekeeper", "pg-shop-api", "pg-payments-worker", "pg-deployer", "payment-jobs", "payment-jobs-dlq", "ap-southeast-2"]


def label(document: dict, item: dict) -> str:
    """The state names the HTTP API and the shop function both shop-api: tell them apart by type, as a reader does."""
    if item["name"] == "shop-api" and item["type"].startswith("edge."):
        return "shop-api (HTTP API)"
    if item["name"] == "shop-api" and item["type"].startswith("service."):
        return "shop-api (function)"
    return item["name"]


def main() -> int:
    hand = json.loads(Path(sys.argv[1]).read_text())
    imported = json.loads(Path(sys.argv[2]).read_text())
    library = load_library()
    hand_names = {item["id"]: label(hand, item) for item in hand["nodes"] + hand["groups"]}
    imported_names = {item["id"]: label(imported, item) for item in imported["nodes"] + imported["groups"]}
    to_imported = {**{name: name for name in SAME_NAME}, **SAME_THING}
    matched = {key: to_imported[name] for key, name in hand_names.items() if name in to_imported}
    imported_by_name = {name: key for key, name in imported_names.items()}
    hand_nodes = {item["id"]: item for item in hand["nodes"] + hand["groups"]}
    imported_nodes = {item["id"]: item for item in imported["nodes"] + imported["groups"]}

    def hand_edge(edge: dict) -> tuple:
        return (matched.get(edge["from"]), edge["type"], matched.get(edge["to"]))

    imported_edges = {(imported_names[e["from"]], e["type"], imported_names[e["to"]]): e for e in imported["edges"]}
    hand_edges = {}
    for edge in hand["edges"]:
        hand_edges[(hand_names[edge["from"]], edge["type"], hand_names[edge["to"]])] = edge
    shared_edges = [key for key, edge in hand_edges.items() if hand_edge(edge) in imported_edges]
    only_hand_edges = [key for key, edge in hand_edges.items() if hand_edge(edge) not in imported_edges]
    seen = {hand_edge(edge) for edge in hand_edges.values()}
    only_imported_edges = [key for key in imported_edges if key not in seen]

    differences = {}
    for key, name in matched.items():
        mine, theirs = hand_nodes[key].get("properties", {}), imported_nodes[imported_by_name[name]].get("properties", {})
        diff = {k: {"hand": mine[k], "import": theirs[k]} for k in mine.keys() & theirs.keys() if mine[k] != theirs[k]}
        if diff or mine.keys() ^ theirs.keys():
            differences[hand_names[key]] = {"different": diff, "only_hand": sorted(mine.keys() - theirs.keys()), "only_import": sorted(theirs.keys() - mine.keys())}

    def described(document: dict) -> int:
        count = 0
        for item in document["nodes"] + document["groups"]:
            required = [p["key"] for p in library[item["type"].rsplit("@", 1)[0]]["properties"] if p["tier"] == "required"]
            count += all(item.get("properties", {}).get(k) not in (None, "", []) for k in required)
        return count

    def counts(document: dict) -> dict:
        return {"nodes": len(document["nodes"]), "groups": len(document["groups"]), "edges": len(document["edges"]),
                "node_provenance": dict(Counter(n.get("provenance", {}).get("kind", "not recorded") for n in document["nodes"])),
                "group_provenance": dict(Counter(n.get("provenance", {}).get("kind", "not recorded") for n in document["groups"])),
                "edge_provenance": dict(Counter(e.get("provenance", {}).get("kind", "not recorded") for e in document["edges"])),
                "described": described(document), "items": len(document["nodes"]) + len(document["groups"]),
                "with_owner": sum(1 for n in document["nodes"] + document["groups"] if n.get("owner")),
                "with_notes": sum(1 for n in document["nodes"] + document["groups"] if n.get("notes"))}

    def sources(document: dict) -> dict:
        """Where the imported items came from: the Terraform state, the repository scan, or the GitHub connection."""
        found = Counter()
        for item in document["nodes"] + document["groups"] + document["edges"]:
            provenance = item.get("provenance") or {}
            if provenance.get("kind") in ("imported", "discovered"):
                found[str(provenance.get("source", "")).split(":")[0]] += 1
        return dict(found)

    # The completed twin: every item the hand-built clone has, plus what only the import has (a person would keep it).
    only_imported_items = [name for name in imported_names.values() if name not in matched.values()]
    twin_items = len(hand["nodes"]) + len(hand["groups"]) + len(only_imported_items)
    imported_items = len(imported["nodes"]) + len(imported["groups"])
    twin_edges = len(hand["edges"]) + len(only_imported_edges)
    report = {
        "hand": counts(hand), "imported": counts(imported), "imported_by_source": sources(imported),
        "shared_nodes": len(matched), "only_hand_nodes": sorted(name for key, name in hand_names.items() if key not in matched),
        "only_imported_nodes": sorted(only_imported_items),
        "shared_edges": len(shared_edges), "only_hand_edges": [" ".join(key) for key in only_hand_edges],
        "only_imported_edges": [" ".join(key) for key in only_imported_edges],
        "property_differences": differences,
        # imported and discovered both count as machine-found (a discovered item is read from the cloud, not drawn by a person)
        "share_of_twin_from_import": {
            "nodes_and_containers": f"{imported_items} of {twin_items}: {round(100 * imported_items / twin_items, 1)} percent",
            "edges": f"{len(imported['edges'])} of {twin_edges}: {round(100 * len(imported['edges']) / twin_edges, 1)} percent",
            "all": round(100 * (imported_items + len(imported["edges"])) / (twin_items + twin_edges), 1),
        },
        "hand_items_the_import_also_found": {"nodes": f"{len(matched)} of {len(hand_nodes)}", "edges": f"{len(shared_edges)} of {len(hand_edges)}"},
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

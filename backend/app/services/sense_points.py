"""Rank where a WhiteHat should watch, from the clone graph alone.

Entry points are public edges, repos, pipelines, registries, webhooks and
personas. Crown jewels are marked, or they hold PHI, PCI or a secret. Paths
are a simple search from an entry to a jewel, walking each edge in the
direction an attack moves along it (ATTACK_FLOW). A path's weight is the number of distinct ATT&CK
techniques on it, plus one for each missing control.
The ranking is a greedy set cover: each pick is the gap that covers the most
remaining path weight per unit of cost, so a path through a missing control or
many techniques counts for more than a safe one (R-056). Choke points are
ordered the same way. The same graph always returns the same list.
"""

from __future__ import annotations

import itertools

_MAX_NODES = 6
_MAX_PATHS = 2000
# Paths to a jewel are capped above, but a dense graph with no reachable jewel
# finds none, so the walk itself needs a limit too. Each step visits one node
# on one partial path; 200 000 steps take well under a second.
_MAX_STEPS = 200_000

FORWARD, REVERSE, BOTH, NONE = "forward", "reverse", "both", "none"
# How an attack moves along each edge type of library/schemas/clone.schema.json,
# relative to the stored from -> to. Every schema type must be listed; a test
# fails when a new type has no entry here.
ATTACK_FLOW = {
    "calls": FORWARD,  # a caller sends what it likes to the callee
    "publishes_to": FORWARD,  # a publisher poisons the channel
    "consumes_from": REVERSE,  # the channel's messages reach the consumer
    "reads": FORWARD,  # the reader holds access to the store
    "writes": FORWARD,  # the writer holds access to the store
    "deploys_from": REVERSE,  # a push to the repo becomes the running service
    "built_by": FORWARD,  # a push to the repo runs in the pipeline
    "pushes_image_to": FORWARD,  # the pipeline decides what the registry holds
    # The host controls every service on it, and a service that escapes its
    # sandbox lands on the host.
    "runs_on": BOTH,
    "runs_as": FORWARD,  # whoever runs the service holds its identity's rights
    "contained_in": NONE,  # sharing a group grants nothing by itself
    "routes_through": REVERSE,  # traffic passes the gateway to reach the service
    # The identity provider vouches for every app that trusts it, and an app
    # or pipeline signing in through it receives that identity's rights.
    "authenticates_via": BOTH,
    "stores_secret_in": FORWARD,  # the service can read its own secrets
    "emits_telemetry_to": FORWARD,  # a sender can forge or flood its logs
    # A copy holds the same data as the original, and changes to the original
    # (ransomware, poisoning) reach the copy.
    "replicates_to": BOTH,
    "backs_up_to": BOTH,
    "depends_on": REVERSE,  # a compromised supplier reaches whoever relies on it
    "peers_with": BOTH,  # peered networks reach each other
    "syncs_with": BOTH,  # a sync holds access to both sides
    # Meaning unknown, so assume the worst: a missed path costs more than an extra one.
    "generic_relation": BOTH,
}
# An attack path that goes through a service acts with the service's identity, so the identity is on the path too.
# These edge types lead from a node to the identity it acts as.
_ACTS_AS = ("runs_as",)
_ENTRY_PREFIXES = ("repo.", "delivery.", "edge.")
_JEWELS = {"PHI", "PCI", "secret"}
_library: dict | None = None


def analyse(graph: dict) -> dict:
    """Paths, choke points and ranked sense points for one clone graph."""
    nodes = _index(graph)
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    found, is_complete = _paths(nodes, edges)
    on_path = {node_id for path in found for node_id in _on(path)}
    return {
        "paths": found,
        "is_complete": is_complete,
        "choke_points": _chokes(found),
        "sense_points": _rank(nodes, edges, found),
        "entry_points": sorted(node_id for node_id, node in nodes.items() if _is_entry(node)),
        "crown_jewels": sorted(node_id for node_id, node in nodes.items() if _is_jewel(node)),
        "coverage_gaps": sorted(node_id for node_id in on_path if not _watched(nodes[node_id], edges)),
    }


def _index(graph: dict) -> dict[str, dict]:
    if not isinstance(graph, dict):
        raise TypeError("A clone graph must be an object")
    found = {}
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            found[node["id"]] = node
    return found


def _flow(edge: dict) -> list[tuple]:
    """The (attacker side, reached side) steps one edge allows."""
    edge_type = edge.get("type")
    if edge_type not in ATTACK_FLOW:
        raise ValueError(f"Edge type {edge_type!r} has no attack-flow direction")
    source, target = edge.get("from"), edge.get("to")
    return {
        FORWARD: [(source, target)],
        REVERSE: [(target, source)],
        BOTH: [(source, target), (target, source)],
        NONE: [],
    }[ATTACK_FLOW[edge_type]]


def _adjacent(edges: list[dict]) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for edge in edges:
        for source, target in _flow(edge):
            if isinstance(source, str) and isinstance(target, str):
                found.setdefault(source, set()).add(target)
    return {key: sorted(value) for key, value in found.items()}


class _Search:
    """One bounded depth-first walk from every entry; stops at either cap."""

    def __init__(self, nodes: dict[str, dict], adjacent: dict[str, list[str]]):
        self.nodes = nodes
        self.adjacent = adjacent
        self.found: list[list[str]] = []
        self.steps = 0

    @property
    def is_spent(self) -> bool:
        return len(self.found) >= _MAX_PATHS or self.steps >= _MAX_STEPS

    def walk(self, path: list[str]) -> None:
        if self.is_spent:
            return
        self.steps += 1
        node_id = path[-1]
        if _is_jewel(self.nodes[node_id]) and (len(path) > 1 or _is_entry(self.nodes[node_id])):
            self.found.append(list(path))
        if len(path) >= _MAX_NODES:
            return
        for nxt in self.adjacent.get(node_id, []):
            if self.is_spent:
                return
            if nxt in path or nxt not in self.nodes:
                continue
            path.append(nxt)
            self.walk(path)
            path.pop()


def _paths(nodes: dict[str, dict], edges: list[dict]) -> tuple[list[dict], bool]:
    """Every path from an entry to a jewel, and whether the search finished."""
    search = _Search(nodes, _adjacent(edges))
    for start in sorted(node_id for node_id, node in nodes.items() if _is_entry(node)):
        search.walk([start])
    unique = sorted({tuple(path) for path in search.found})
    acting = _acting(edges)
    paths = [{"nodes": list(path), "weight": _weight(nodes, edges, path), "acts_as": _identities(acting, path)} for path in unique]
    return paths, not search.is_spent


def _acting(edges: list[dict]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("type") in _ACTS_AS and isinstance(edge.get("from"), str) and isinstance(edge.get("to"), str):
            found.setdefault(edge["from"], set()).add(edge["to"])
    return found


def _identities(acting: dict[str, set[str]], path: list[str]) -> list[str]:
    """The identities the nodes of a path act as, that are not already steps of the path."""
    return sorted({identity for node_id in path for identity in acting.get(node_id, ()) if identity not in path})


def _on(path: dict) -> list[str]:
    """Every node that sees a path: its steps, and the identities its steps act as. Watching one of them watches the path."""
    return path["nodes"] + path.get("acts_as", [])


def passes(path: dict, node_id: str) -> bool:
    return node_id in _on(path)


def _is_entry(node: dict) -> bool:
    properties = _properties(node)
    if properties.get("exposure") == "public" or properties.get("webhook") is True or properties.get("persona") is True:
        return True
    name = _type_name(node)
    return name.startswith(_ENTRY_PREFIXES) or name == "saas.persona"


def _is_jewel(node: dict) -> bool:
    if node.get("criticality") == "crown-jewel":
        return True
    return bool(_JEWELS & set(node.get("data_classification") or []))


def _weight(nodes: dict[str, dict], edges: list[dict], path: list[str]) -> int:
    techniques: set[str] = set()
    missing = 0
    for node_id in path:
        techniques |= _techniques(nodes[node_id])
        properties = _properties(nodes[node_id])
        missing += int(properties.get("auth") is False)
        missing += int(properties.get("tls") is False)
        missing += int(properties.get("wide_iam") is True)
    techniques |= _edge_techniques(edges, path)
    return len(techniques) + missing


def _path_weight(path: dict) -> int:
    """A path counts at least once, however safe it looks."""
    return max(int(path.get("weight") or 0), 1)


def _chokes(paths: list[dict]) -> list[dict]:
    nodes: dict[str, list[int]] = {}
    edges: dict[tuple[str, str], list[int]] = {}
    for path in paths:
        ids, weight = path["nodes"], _path_weight(path)
        for node_id in set(ids):
            count = nodes.setdefault(node_id, [0, 0])
            count[0], count[1] = count[0] + 1, count[1] + weight
        for pair in set(itertools.pairwise(ids)):
            count = edges.setdefault(pair, [0, 0])
            count[0], count[1] = count[0] + 1, count[1] + weight
    points = [{"kind": "node", "id": node_id, "paths": count, "weight": weight} for node_id, (count, weight) in nodes.items()]
    for (source, target), (count, weight) in edges.items():
        points.append({"kind": "edge", "from": source, "to": target, "paths": count, "weight": weight})
    return sorted(points, key=_choke_key)


def _choke_key(point: dict) -> tuple:
    identity = point.get("id") or f"{point.get('from')}->{point.get('to')}"
    return (-point["weight"], -point["paths"], point["kind"], identity)


def _rank(nodes: dict[str, dict], edges: list[dict], paths: list[dict]) -> list[dict]:
    if not paths:
        return []
    on_path = {node_id for path in paths for node_id in _on(path)}
    gaps = [node_id for node_id in sorted(on_path) if not _watched(nodes[node_id], edges)]
    uncovered = set(range(len(paths)))
    ranked = []
    while uncovered:
        pick = _best(gaps, nodes, paths, uncovered)
        if pick is None:
            break
        node_id, covered = pick
        point = _sense_point(nodes[node_id], len(covered), len(paths))
        point["weight"] = sum(_path_weight(paths[index]) for index in covered)
        ranked.append(point)
        uncovered -= covered
        gaps.remove(node_id)
    return ranked


def also_on_paths(graph: dict, result: dict) -> list[dict]:
    """The gaps the greedy cover did not need, most paths first, so a person has a choice.

    The cover stops once every path is covered, so a graph with a few big gaps suggests only a few
    nodes. These are the other unwatched nodes on the same paths, each counted on its own.
    """
    nodes = _index(graph)
    picked = {point["target"] for point in result["sense_points"]}
    left = []
    for node_id in result["coverage_gaps"]:
        if node_id in picked:
            continue
        count = sum(passes(path, node_id) for path in result["paths"])
        left.append(_sense_point(nodes[node_id], count, len(result["paths"])))
    return sorted(left, key=lambda point: (-point["paths"], point["cost"], point["target"]))


def _best(gaps: list[str], nodes: dict[str, dict], paths: list[dict], uncovered: set[int]):
    chosen: tuple[tuple, str, set[int]] | None = None
    for node_id in gaps:
        covered = {index for index in uncovered if passes(paths[index], node_id)}
        if not covered:
            continue
        cost = _cost(nodes[node_id])
        weight = sum(_path_weight(paths[index]) for index in covered)
        key = (-(weight / cost), -weight, -len(covered), cost, node_id)
        if chosen is None or key < chosen[0]:
            chosen = (key, node_id, covered)
    if chosen is None:
        return None
    return chosen[1], chosen[2]


def _sense_point(node: dict, covered: int, total: int) -> dict:
    mode = _mode(node)
    return {
        "target": node["id"],
        "mode": mode,
        "why": f"Covers {covered} of {total} attack paths. No telemetry and no control on this node.",
        "modes": modes(node),
        "reads": mode,
        "access": "read-only",
        "cost": _cost(node),
        "coverage": covered / total,
        "paths": covered,
        "techniques": sorted(_techniques(node)),
        "confidence": "high" if _modes_known(node) else "low",
    }


def _watched(node: dict, edges: list[dict]) -> bool:
    properties = _properties(node)
    if properties.get("telemetry") is True or properties.get("control") is True:
        return True
    if _type_name(node).startswith("control."):
        return True
    return any(edge.get("from") == node["id"] and edge.get("type") == "emits_telemetry_to" for edge in edges)


def modes(node: dict) -> list[str]:
    """The WhiteHat modes that can watch a node, the one its kind is best watched in first.

    A node that lists its own modes wins. Otherwise the node library says: the modes of the kind's default sense points
    come first, then the rest of the modes its WhiteHats support. A kind the library does not know is watched by telemetry.
    One sense point is one read-only way in to a node (a repository, a log group, an account), and every mode listed
    here is a WhiteHat that can work from that same way in.
    """
    listed = node.get("modes")
    if isinstance(listed, list) and listed and all(isinstance(item, str) for item in listed):
        return list(dict.fromkeys(listed))
    document = _catalogue().get(_type_name(node)) or {}
    first = [item["mode"] for item in document.get("default_sense_points") or [] if isinstance(item, dict) and isinstance(item.get("mode"), str)]
    rest = [item for item in document.get("whitehat_modes") or [] if isinstance(item, str)]
    return list(dict.fromkeys(first + rest)) or ["telemetry"]


def _mode(node: dict) -> str:
    return modes(node)[0]


def _modes_known(node: dict) -> bool:
    """Whether the node or the library says how to watch it, rather than the telemetry fallback."""
    return bool(node.get("modes")) or bool(_catalogue().get(_type_name(node)))


def _cost(node: dict) -> int:
    cost = _properties(node).get("watch_cost")
    if isinstance(cost, int) and not isinstance(cost, bool) and cost >= 1:
        return cost
    return 1


def _techniques(node: dict) -> set[str]:
    listed = node.get("attack_techniques")
    if isinstance(listed, list):
        return {item for item in listed if isinstance(item, str)}
    return _library_techniques(_type_name(node))


def _edge_techniques(edges: list[dict], path: list[str]) -> set[str]:
    steps = set(itertools.pairwise(path))
    found: set[str] = set()
    for edge in edges:
        if steps.isdisjoint(_flow(edge)):
            continue
        listed = edge.get("attack_techniques")
        if isinstance(listed, list):
            found.update(item for item in listed if isinstance(item, str))
    return found


def _library_techniques(type_id: str) -> set[str]:
    if not type_id:
        return set()
    document = _catalogue().get(type_id) or {}
    listed = document.get("attack_techniques") or []
    return {item for item in listed if isinstance(item, str)}


def _catalogue() -> dict:
    global _library
    if _library is None:
        from library.resolve import load_library

        _library = load_library()
    return _library


def _properties(node: dict) -> dict:
    properties = node.get("properties")
    return properties if isinstance(properties, dict) else {}


def _type_name(node: dict) -> str:
    name = node.get("type")
    return name.split("@", 1)[0] if isinstance(name, str) else ""

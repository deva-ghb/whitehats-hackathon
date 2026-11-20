"""SC.1: each service node's known package and image weaknesses, ranked by reach. No model call: the facts come from
the knowledge base (KB.1) for packages, the last Trivy scan of the image the node runs or is built from (VS.5c), and
the reach from the attack paths of the same clone version (P6.1).

Reach, best first:
1. the node is on an attack path from an entry point to a crown jewel;
2. an entry point reaches the node, but no path through it reaches a crown jewel;
3. nothing from outside reaches it: internal only.
Within a reach, the higher severity ranks first, then the advisory id, so the same clone version always ranks the same.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..knowledge import lookup
from ..knowledge.osv import ATTRIBUTION
from ..knowledge.watch import severity, used_packages
from ..models import ImageScan
from ..scanning import inventory
from . import sense_points

IMAGE_ATTRIBUTION = "Image weaknesses found by Trivy (Aqua Security), from its scan of the image."
ImageWeaknesses = Callable[[str], list[dict]]  # an image reference -> the fixable weaknesses of its last scan

REACH_WORDS = {1: "on an attack path", 2: "reachable from an entry point", 3: "internal only"}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def for_clone(db: Session, document: dict, images: ImageWeaknesses | None = None) -> dict:
    """Per node with weaknesses: its reach, why in one line, and its weaknesses in rank order; and every row ranked.

    `images` gives an image's weaknesses; by default the last stored scan (an image not scanned yet has none)."""
    images = images or (lambda image: _scanned(db, image))
    nodes = sense_points._index(document)
    edges = [edge for edge in document.get("edges", []) if isinstance(edge, dict)]
    paths = sense_points.analyse(document)["paths"]
    reached = _reached(nodes, edges)
    out: dict[str, dict] = {}
    for node_id in sorted(nodes):
        node = nodes[node_id]
        every = _weaknesses(db, node) + _image_weaknesses(node, images)
        rows = [row for row in every if not row.get("not_affected")]  # a VEX statement covers the rest (SCAN.5)
        covered = [row for row in every if row.get("not_affected")]
        if not rows:
            continue
        reach, why, route = _reach(node_id, nodes, paths, reached)
        for row in rows:
            row.update({"node": node_id, "node_name": node.get("name", node_id), "reach": reach, "why": why})
        rows.sort(key=_row_key)
        out[node_id] = {"name": node.get("name", node_id), "reach": reach, "reach_words": REACH_WORDS[reach], "why": why,
                        "route": route,
                        "count": len(rows), "highest": rows[0]["severity"], "weaknesses": rows,
                        "not_affected": sorted(covered, key=lambda row: row["advisory"])}
    ranked = sorted((row for entry in out.values() for row in entry["weaknesses"]), key=_row_key)
    result = {"nodes": out, "ranked": ranked, "attribution": ATTRIBUTION}
    if any(row.get("image") for row in ranked):
        result["image_attribution"] = IMAGE_ATTRIBUTION
    return result


def _scanned(db: Session, image: str) -> list[dict]:
    row = db.get(ImageScan, image)
    return list(row.weaknesses) if row is not None and row.scanned_at is not None else []


def _image_weaknesses(node: dict, images: ImageWeaknesses) -> list[dict]:
    """One row per fixable weakness in the image the node runs, else the one it is built from, else (a zip function) its
    AWS runtime, naming the image."""
    image, _how = inventory.image_of(node)  # the image it runs, is built from, or its AWS runtime (SCAN.2)
    if not image:
        return []
    return [{"package": item["package"], "version": item["installed"], "ecosystem": "image", "image": image,
             "advisory": item["advisory"], "aliases": [], "severity": item["severity"],
             "fixed_versions": [part.strip() for part in str(item["fixed"]).split(",") if part.strip()],
             "summary": str(item.get("title") or "")[:240], "url": f"https://osv.dev/vulnerability/{item['advisory']}",
             "modified": "", **({"not_affected": item.get("vex_reason") or "Not affected"} if item.get("vex_status") else {})}
            for item in images(image)]


def _row_key(row: dict) -> tuple:
    return (row["reach"], _SEVERITY_ORDER.get(row["severity"], 4), row["node_name"], row["package"], row["advisory"])


def _weaknesses(db: Session, node: dict) -> list[dict]:
    """One row per weakness: records of the same weakness from different sources (a GHSA and a PYSEC naming the same
    CVE) are one row, under the most severe record, with every other id among its aliases."""
    rows = []
    for ecosystem, name, version in used_packages(node):
        found = []
        for item in lookup.by_package(db, ecosystem, name, version):
            level = severity({"severity": item["severity"], "database_specific": {"severity": item["database_severity"]}})
            found.append({"package": name, "version": version, "ecosystem": ecosystem, "advisory": item["id"],
                          "aliases": item["aliases"], "severity": level, "fixed_versions": item["fixed_versions"],
                          "summary": item["summary"][:240], "url": item["url"], "modified": item["modified"]})
        rows.extend(_merged(found))
    return rows


def _merged(found: list[dict]) -> list[dict]:
    groups: list[list[dict]] = []
    for row in sorted(found, key=lambda item: (_SEVERITY_ORDER.get(item["severity"], 4), item["advisory"])):
        names = {row["advisory"], *row["aliases"]}
        home = next((group for group in groups if names & {name for member in group for name in (member["advisory"], *member["aliases"])}), None)
        if home is None:
            groups.append([row])
        else:
            home.append(row)
    out = []
    for group in groups:
        lead = dict(group[0])  # the most severe record speaks for the weakness
        ids = {name for member in group for name in (member["advisory"], *member["aliases"])} - {lead["advisory"]}
        lead["aliases"] = sorted(ids)
        lead["fixed_versions"] = sorted({version for member in group for version in member["fixed_versions"]})
        out.append(lead)
    return out


def _reached(nodes: dict[str, dict], edges: list[dict]) -> dict[str, list[str]]:
    """For every node an entry point reaches, the shortest way there, entry first (breadth first, in id order)."""
    adjacent = sense_points._adjacent(edges)
    found: dict[str, list[str]] = {}
    queue = deque([[start] for start in sorted(node_id for node_id, node in nodes.items() if sense_points._is_entry(node))])
    while queue:
        way = queue.popleft()
        if way[-1] in found:
            continue
        found[way[-1]] = way
        for following in adjacent.get(way[-1], []):
            if following in nodes and following not in found:
                queue.append([*way, following])
    return found


def _reach(node_id: str, nodes: dict[str, dict], paths: list[dict], reached: dict[str, list[str]]) -> tuple[int, str, str]:
    """The reach, why in one line, and the way named on its own (empty when nothing reaches the node)."""
    through = sorted((path["nodes"] for path in paths if sense_points.passes(path, node_id)),
                     key=lambda way: (len(way), _entry_rank(nodes.get(way[0], {})), way))
    if through:
        route = _named(through[0], nodes)
        return 1, f"On an attack path: {route}.", route
    if node_id in reached and len(reached[node_id]) > 1:
        route = _named(reached[node_id], nodes)
        return 2, f"Reachable from an entry point: {route}; no path through it reaches a crown jewel.", route
    if node_id in reached:
        return 2, "It is an entry point itself; no path through it reaches a crown jewel.", ""
    return 3, "Internal only: no entry point reaches it.", ""


def _entry_rank(node: dict) -> int:
    """Among equally short paths, the one from the internet is named first, then a repository or pipeline, then a person."""
    kind = str(node.get("type", "")).split("@")[0]
    if kind.startswith("edge.") or (node.get("properties") or {}).get("exposure") == "public":
        return 0
    return 1 if kind.startswith(("repo.", "delivery.")) else 2


def _named(way: list[str], nodes: dict[str, dict]) -> str:
    """The steps by name; a name that appears twice on the way also says what kind of thing each one is."""
    names = [str(nodes[step].get("name", step)) for step in way if step in nodes]
    kinds = [str(nodes[step].get("type", "")).split("@")[0].rsplit(".", 1)[-1] for step in way if step in nodes]
    return ", then ".join(f"{name} ({kind})" if names.count(name) > 1 and kind else name for name, kind in zip(names, kinds))

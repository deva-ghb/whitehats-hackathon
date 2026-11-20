"""P6.4: do the top sense point suggestions on the proving-ground twin match the weaknesses in the scenario catalogue?

Usage (from platform/backend):

    uv run python scripts/measure_sense_points.py <clone document.json> [--top 3,5,9]

The document is an exported clone (the hand-built proving ground, or the twin an import made). The run marks the crown
jewels a person marks (sense_point_weaknesses.json), asks for the Where to watch list exactly as the route stores it,
and for each weakness says whether a suggestion of a right mode sits on its place or next to it, for the first N
suggestions. The judged N is five: the five the Where to watch browser journey accepts, which is the point at which
that journey's coverage figure reaches 100 percent. Also reported: the size of the greedy cover (the fewest sense points
that watch every attack path), nine, and the whole list. Each is given two ways: a weakness is seen when the suggestion
names one of the right modes (a sense point is one way in to a node and every mode listed can work from it), and, stricter,
when the suggestion's first mode is a right one. Nothing is written and nothing leaves the machine. Exit 1 when the share
at the judged N is under 80 percent.

The weaknesses, their modes and their places are data (sense_point_weaknesses.json). This file and the data are the
only places a scenario id is written; the analysis and the library never see one.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sense_point_store import suggest

DATA = Path(__file__).with_name("sense_point_weaknesses.json")
THRESHOLD = 0.8
JUDGED = 5


def load_data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def modes_of(data: dict, weakness: dict) -> list[str]:
    return data["mode_words"][weakness["catalogue_mode"]]


def matches(node: dict, ref: dict) -> bool:
    """A node is the place when its library type is the type and its name is one of the names, or ends with -name or /name."""
    if str(node.get("type", "")).split("@", 1)[0] != ref["type"]:
        return False
    name = node.get("name", "")
    return any(name == want or name.endswith(("-" + want, "/" + want)) for want in ref["names"])


def find(document: dict, ref: dict) -> list[str]:
    return sorted(node["id"] for node in document["nodes"] if matches(node, ref))


def place_nodes(document: dict, place: dict) -> set[str]:
    refs = [place["node"]] if "node" in place else place["edge"]
    return {node for ref in refs for node in find(document, ref)}


def marked(document: dict, data: dict) -> dict:
    """A copy of the twin with the crown jewels a person marks."""
    found = copy.deepcopy(document)
    wanted = data["crown_jewels"]["places"]
    for node in found["nodes"]:
        if any(matches(node, ref) for ref in wanted):
            node["criticality"] = "crown-jewel"
    return found


def neighbours(result: dict) -> set[tuple[str, str]]:
    """Every pair of consecutive steps of an attack path the analysis found, in both orders."""
    pairs = set()
    for path in result["paths"]:
        for first, second in itertools.pairwise(path["nodes"]):
            pairs |= {(first, second), (second, first)}
    return pairs


def seen_by(point: dict, strict: bool = False) -> list[str]:
    """The modes a suggestion offers: all of its modes, or with strict only the first."""
    modes = point.get("modes") or [point["mode"]]
    return modes[:1] if strict else modes


def check(weakness: dict, data: dict, document: dict, top: list[dict], near: set[tuple[str, str]], strict: bool = False) -> dict:
    """For one weakness and one list of suggestions: the first suggestion that sees it, and how it sits."""
    place = place_nodes(document, weakness["place"])
    right = modes_of(data, weakness)
    for point in top:
        if not set(seen_by(point, strict)) & set(right):
            continue
        if point["target"] in place:
            return {"covered": True, "by": point["name"], "rank": point["rank"], "how": "on it"}
        if any((point["target"], node) in near for node in place):
            return {"covered": True, "by": point["name"], "rank": point["rank"], "how": "next to it"}
    return {"covered": False, "by": "", "rank": 0, "how": "", "place_found": bool(place)}


def cover_size(ranked: list[dict]) -> int:
    return sum(point["is_cover"] for point in ranked)


def _share(data: dict, twin: dict, top: list[dict], near: set[tuple[str, str]], strict: bool) -> dict:
    rows = {w["id"]: check(w, data, twin, top, near, strict) for w in data["weaknesses"]}
    covered = sum(row["covered"] for row in rows.values())
    return {"rows": rows, "covered": covered, "of": len(rows), "share": covered / len(rows)}


def measure(document: dict, data: dict | None = None, tops: tuple[int, ...] = (9,)) -> dict:
    """The coverage table: for the judged N, the size of the cover, each extra N and the whole list: each weakness, and the share."""
    data = data or load_data()
    twin = marked(document, data)
    ranked, result = suggest(twin)
    near = neighbours(result)
    sizes = {"judged": JUDGED, "cover": cover_size(ranked), "all": len(ranked), **{f"top{n}": n for n in tops}}
    table = {}
    for label, size in sizes.items():
        top = ranked[:size]
        table[label] = {"n": size, **_share(data, twin, top, near, False), "strict": _share(data, twin, top, near, True)}
    return {"suggestions": ranked, "paths": len(result["paths"]), "table": table}


def show(report: dict, data: dict) -> str:
    lines = [f"{report['paths']} attack paths; {len(report['suggestions'])} suggestions:"]
    for point in report["suggestions"]:
        lines.append(f"  {point['rank']}. {point['name']} [{', '.join(seen_by(point))}] on {point['paths']} paths")
    for label, entry in report["table"].items():
        lines.append(f"\nFirst {entry['n']} suggestions ({label}):")
        for weakness in data["weaknesses"]:
            row = entry["rows"][weakness["id"]]
            said = f"{row['how']}: {row['by']} (rank {row['rank']})" if row["covered"] else "not covered"
            lines.append(f"  {weakness['id']}  {'/'.join(modes_of(data, weakness)):<22} {said}")
        lines.append(f"  covered {entry['covered']} of {entry['of']}: {entry['share']:.1%}; first mode only {entry['strict']['covered']} of {entry['of']}: {entry['strict']['share']:.1%}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("document", type=Path)
    parser.add_argument("--top", default="9", help="extra values of N to report, comma separated")
    args = parser.parse_args()
    data = load_data()
    report = measure(json.loads(args.document.read_text(encoding="utf-8")), data, tuple(int(n) for n in args.top.split(",")))
    print(show(report, data))
    return 0 if report["table"]["judged"]["share"] >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())

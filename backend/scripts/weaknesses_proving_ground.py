"""SC.1 on the proving ground: which nodes of the imported twin show a weakness badge, for the baseline and scenarios.

Usage (from platform/backend; reads only, writes nothing outside a temporary folder):

    uv run python scripts/weaknesses_proving_ground.py --repo <proving-ground clone> --twin <imported twin export>
        --knowledge <SQLite copy of the knowledge base> [--scenario S04 --scenario S10 ...]

For each run the scenario is applied to a copy of the baseline with the proving ground's own scenario tool (as
`make demo` does), the repository is scanned as the repository import scans it, and what the scan learns about the
twin's own nodes (the exact package versions and the base image) is filled into a copy of the twin, as accepting that
import does. Each base image is scanned with Trivy for real (Docker), as the image worker scans it. The
crown jewels are the ones a person marks (sense_point_weaknesses.json, as P6.4). Then the ranking is read exactly as the
weaknesses route reads it.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from confirm_proving_ground import _apply, _baseline_copy
from measure_sense_points import load_data, marked
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.connectors.github import RepositoryFiles
from app.decisions_api.scanners.trivy import scan_image
from app.services import changesets
from app.services.repo_scan import scan_repository
from app.services.weaknesses import for_clone

REPO = "deva-ghb/whitehats-proving-ground"


def _files(tree: Path) -> dict[str, str]:
    found = {}
    for path in tree.rglob("*"):
        relative = path.relative_to(tree).as_posix()
        if path.is_file() and not relative.startswith((".git/", "scenarios/")) and path.stat().st_size < 1_000_000:
            try:
                found[relative] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
    return found


def _repository(files: dict[str, str]) -> RepositoryFiles:
    node = {"id": "github_repo_proving_ground", "type": "repo.github@1", "name": REPO, "environment": "shared",
            "provenance": {"kind": "discovered", "source": "github"}, "properties": {"repo": REPO}}
    return RepositoryFiles(REPO, frozenset(files), files.get, node, None)


def with_scan(twin: dict, tree: Path) -> dict:
    """The twin with what a repository import of `tree` fills into its own nodes (the `packages` it learns)."""
    found = scan_repository(_repository(_files(tree)), environment="prod")
    filled = changesets.enrich({"nodes": found.nodes, "groups": [], "edges": found.edges}, twin)
    learned = {node["id"]: node.get("properties") or {} for node in filled["nodes"]}
    out = copy.deepcopy(twin)
    for node in out["nodes"]:
        for key in ("packages", "base_image"):
            if learned.get(node["id"], {}).get(key):
                node.setdefault("properties", {})[key] = learned[node["id"]][key]
    return out


def _trivy(cache: dict):
    """An image's fixable weaknesses, scanned once per run of this script."""
    def images(image: str) -> list[dict]:
        if image not in cache:
            found, _unfixed, run = scan_image(image, timeout=600)
            if run.error:
                raise SystemExit(f"{image}: {run.error}")
            cache[image] = [asdict(item) for item in found]
        return cache[image]
    return images


def main() -> int:
    parser = argparse.ArgumentParser(description="SC.1 badges on the proving ground's imported twin.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--twin", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--ref", default="pg-baseline")
    parser.add_argument("--scenario", action="append", help="repeat; the baseline always runs first")
    args = parser.parse_args()
    twin = json.loads(args.twin.read_text(encoding="utf-8"))
    db = sessionmaker(create_engine(f"sqlite:///{args.knowledge}"))()
    data = load_data()
    images = _trivy({})
    for scenario in [None, *(args.scenario or [])]:
        with tempfile.TemporaryDirectory(prefix="wh-sc1-") as folder:
            tree = Path(folder)
            _baseline_copy(args.repo, args.ref, tree)
            if scenario:
                _apply(tree, scenario)
            result = for_clone(db, marked(with_scan(twin, tree), data), images=images)
        name = scenario or "baseline"
        if not result["nodes"]:
            print(f"{name}: no badge")
        for entry in sorted(result["nodes"].values(), key=lambda item: (item["reach"], item["name"])):
            rows = "; ".join(f"{row['advisory']} {row['package']} {row['version']} {row['severity']}"
                             f" fixed in {', '.join(row['fixed_versions']) or 'nothing yet'}"
                             + (f" (image {row['image']})" if row.get("image") else "") for row in entry["weaknesses"][:3])
            if entry["count"] > 3:
                rows += f"; and {entry['count'] - 3} more"
            print(f"{name}: {entry['name']}: {entry['count']} ({entry['reach_words']}). {entry['why']} {rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

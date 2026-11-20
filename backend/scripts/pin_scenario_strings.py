"""Pin the strings that exist only in the proving ground's scenario patches, for the boundary test (R-057).

Usage (from platform/backend):

    uv run python scripts/pin_scenario_strings.py --repo ../../../whitehats-proving-ground [--ref origin/main]

Reads the proving ground at one commit through git (no checkout is changed): every tracked file outside
`scenarios/` is the baseline, and each `scenarios/*/inject.patch` adds lines. A line or a long name a patch adds
that the baseline does not hold is scenario-only, and the rules and prompts must never contain it. The result is
written to tests/data/scenario_strings.json with the commit it came from, so the test needs no network and no copy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "tests"))

from test_decisions_boundaries import PINNED, scenario_modes, scenario_only_strings


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          encoding="utf-8", errors="ignore").stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Pin the proving ground's scenario-only strings for the boundary test.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--ref", default="origin/main")
    args = parser.parse_args()
    commit = _git(args.repo, "rev-parse", args.ref).strip()
    files = {name: _git(args.repo, "show", f"{commit}:{name}")
             for name in _git(args.repo, "ls-tree", "-r", "--name-only", commit).splitlines()
             if not name.lower().endswith((".png", ".zip", ".pyc")) and name != "tfplan"}
    needles, scenarios = scenario_only_strings(files)
    PINNED.parent.mkdir(parents=True, exist_ok=True)
    PINNED.write_text(json.dumps({"proving_ground_commit": commit, "scenarios": scenarios,
                                  "modes": scenario_modes(files), "strings": needles},
                                 indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"pinned {len(needles)} strings from {len(scenarios)} scenarios at {commit[:7]} into {PINNED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

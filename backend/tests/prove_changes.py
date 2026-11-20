"""Live, read-only proof of the change trigger's reading step against the proving-ground repository.

    uv run python tests/prove_changes.py --head <commit> [--base <commit or branch>] [--clone <local clone>] [--engines]

Reads the diff of `head` against `base` (default: its first parent) and only the files that diff touches,
through the WhiteHats GitHub App. With --clone, the files read are compared with what `git diff` says
changed. With --engines, the Decisions API runs over them (its engines need Docker).
Every call is a GET (the App's token exchange is a POST to GitHub's sign-in). Nothing is written to GitHub.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

os.environ["WH_DATA_DIR"] = tempfile.mkdtemp(prefix="wh-prove-")
os.environ["WH_LOAD_DOTENV"] = "0"
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]

from app.changes.fetch import fetched
from app.connectors.github import GitHub
from app.connectors.github_app import app_token_from_settings
from app.connectors.http import httpx_transport
from app.decisions_api import decide


def _kind(url: str) -> str:
    for word in ("compare", "contents", "access_tokens"):
        if f"/{word}" in url:
            return word
    return "other"


def _counting_source(calls: Counter) -> GitHub:
    def transport(method, url, headers, body):
        calls[f"{method} {_kind(url)}"] += 1
        return httpx_transport(method, url, headers, body)

    return GitHub({"installation_id": os.environ["GITHUB_APP_INSTALLATION_ID"]}, transport=transport,
                  app_token=app_token_from_settings())


def _git_changed(clone: str, base: str, head: str) -> list[str]:
    out = subprocess.run(["git", "-C", clone, "diff", "--name-only", "--diff-filter=d", base, head],
                         capture_output=True, text=True, check=True).stdout
    return sorted(out.split())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base")
    parser.add_argument("--clone")
    parser.add_argument("--engines", action="store_true")
    args = parser.parse_args()
    repo = os.environ["PROVING_GROUND_REPO"]
    base = args.base or f"{args.head}~1"
    calls: Counter = Counter()
    source = _counting_source(calls)
    started = time.monotonic()
    with fetched(source, repo, base, args.head) as read:
        took = time.monotonic() - started
        print(f"read {base} .. {args.head[:7]} in {took:.1f}s: diff {len(read.diff.encode())} bytes, "
              f"{len(read.files)} file(s) read, {len(read.skipped)} skipped")
        for path in read.files:
            print("  read   ", path)
        for item in read.skipped:
            print("  skipped", item["path"], "-", item["reason"])
        print("GitHub calls:", dict(calls))
        ok = True
        if args.clone:
            expected = _git_changed(args.clone, base, args.head)
            actual = sorted([*read.files, *(s["path"] for s in read.skipped)])
            ok = expected == actual
            print("files git says changed (not deleted):", len(expected), "| match:", ok)
            if not ok:
                print("  only in git:", sorted(set(expected) - set(actual)))
                print("  only read  :", sorted(set(actual) - set(expected)))
            on_disk = sorted(str(p.relative_to(read.root)) for p in read.root.rglob("*") if p.is_file())
            print("tree holds exactly the files read:", on_disk == sorted(read.files))
            ok = ok and on_disk == sorted(read.files)
        if args.engines:
            started = time.monotonic()
            decision = decide(read.root, read.diff)
            print(f"Decisions API in {time.monotonic() - started:.1f}s: {decision.verdict}")
            for run in decision.engines:
                print(f"  engine {run.engine:10} {run.seconds:5.1f}s {run.error or 'ok'}")
            for s in decision.suspects:
                print(f"  {s.severity:8} {s.engine:10} {s.rule_id}  {s.evidence.path}:{s.evidence.start_line}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

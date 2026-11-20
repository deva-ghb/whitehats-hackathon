"""Print each change the server has kept, and how long each step took after the push.

Usage (from platform/backend, with the same WH_DATA_DIR or database settings as the server):

    uv run python scripts/change_timings.py [--last 10]

"Push" is the time GitHub gives for the push; "kept" is when WhiteHats recorded the change (by webhook or by
poll); "read" is when its diff and changed files had been fetched; "judged" is when the Decisions API answered.
Times are UTC. GitHub's push time is in whole seconds, so a delta can be off by up to a second.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("WH_LOAD_DOTENV", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ChangeEvent


def _gap(start, end) -> str:
    return f"{(end - start).total_seconds():6.1f}s" if start and end else "     - "


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--last", type=int, default=10)
    args = parser.parse_args()
    with SessionLocal() as db:
        rows = db.scalars(select(ChangeEvent).order_by(ChangeEvent.id.desc()).limit(args.last)).all()
        for row in reversed(rows):
            print(f"#{row.id} {row.repo} {row.branch} {row.head_sha[:7]} via {row.source}: {row.status}"
                  f" {row.verdict or '-'} ({len(row.files)} file(s) read)")
            print(f"    push to kept {_gap(row.pushed_at, row.received_at)} | kept to read "
                  f"{_gap(row.received_at, row.fetched_at)} | read to judged {_gap(row.fetched_at, row.decided_at)}"
                  f" | push to judged {_gap(row.pushed_at, row.decided_at)}")
            if row.error:
                print(f"    note: {row.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

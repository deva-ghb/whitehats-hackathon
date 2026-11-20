"""Run an audit, or load a finished one into a WhiteHats project.

    uv run python -m app.audit run --target <snapshot> --out <run folder> --repo owner/name --ref <commit> [--budget 15]
    uv run python -m app.audit import --run <run folder> --company acme --project main --repo owner/name [--branch main]

`run` reads AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY from the environment; running it again with the same
--out resumes the audit. `import` is safe to repeat: nothing is added twice.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from sqlalchemy import select

from .azure import Client
from .conductor import Run, audit


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.audit")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--target", required=True, type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--repo", required=True)
    run.add_argument("--ref", required=True)
    run.add_argument("--profile", default="quick", choices=["quick", "change"])
    run.add_argument("--budget", type=float, default=15.0, help="stop starting new agents past this many dollars")
    load = sub.add_parser("import")
    load.add_argument("--run", required=True, type=Path)
    load.add_argument("--company", required=True, help="the company's slug")
    load.add_argument("--project", required=True, help="the project's slug")
    load.add_argument("--repo", required=True)
    load.add_argument("--branch", default="")
    load.add_argument("--trigger", default="onboarding",
                      choices=["onboarding", "cloud_change", "code_change", "schedule", "request"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "run":
        job = Run(target=args.target.resolve(), out=args.out.resolve(), repo=args.repo, source_ref=args.ref,
                  profile=args.profile, budget_usd=args.budget)
        print(json.dumps(audit(job, Client(job.out / "usage.jsonl")), indent=1))
        return
    from ..db import SessionLocal
    from ..models import Organization, Project
    from .store import import_run

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == args.company))
        if org is None:
            raise SystemExit(f"No company {args.company}.")
        project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == args.project))
        if project is None:
            raise SystemExit(f"No project {args.project} in company {args.company}.")
        row = import_run(db, org.id, project.id, args.run.resolve(), args.trigger, args.repo, branch=args.branch)
        print(json.dumps({"audit": row.id, "status": row.status, "counts": row.counts, "cost": row.cost_usd}))


if __name__ == "__main__":
    main()

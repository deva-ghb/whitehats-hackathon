"""VS.6: the effectiveness scorecard. Every proving-ground scenario in turn, judged the way a push is judged, scored
against what the scenario says it plants, twice, and written to results/<date>.md and .json.

Usage (from platform/backend, Docker running, .env holding the model settings for confirmation):

    uv run python scripts/benchmark_proving_ground.py --repo ../../../whitehats-proving-ground
    uv run python scripts/benchmark_proving_ground.py --repo ... --offline       # no model: nothing is confirmed
    uv run python scripts/benchmark_proving_ground.py --repo ... --price-in 0.15 --price-out 0.6   # USD per million tokens

Each scenario is applied to a copy of the baseline (the proving ground's own scenario tool, as `make demo` does). The
change is planned (SCAN.1), decided by every engine the plan names and the house rules, and the escalated suspects are
confirmed through the model gateway, exactly as for a push. Then it is scored:
- found at the right place: an escalated finding on a file the scenario's patch changes, within LINE_SLACK lines of a
  line the patch adds or removes;
- confirmed: that finding's confirmation is "confirmed" (a model said so);
- severity: at least the scenario's `min_severity`;
- S10 (the negative control): nothing escalated at all;
- `must_not_flag`: no finding above low in those files, away from the planted lines.
Timings here are the platform's own judging time on this machine. Push-to-finding (a push, the webhook, the server's
check run) is the live run's to measure. A scenario with no patch (S12: a live console change) is reported as needing
the live run, never counted as found. Hold-out scenarios (scenarios/holdout/, added by the reviewer) are run the same
way and reported apart; this script never shows their contents.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from confirm_proving_ground import _apply, _baseline_copy, _git, _model_ask, _offline_ask, _service_facts, _staged_diff

from app.confirmation import confirm
from app.confirmation.verdict import CONFIRMED
from app.decisions_api import decide
from app.decisions_api.acceptances import load_acceptances
from app.decisions_api.diff import parse_diff
from app.scanning import Scope, checked, planner

REPO = "deva-ghb/whitehats-proving-ground"
LINE_SLACK = 3
RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
BAR = {"found": 10, "of": 11, "chunk_share": 0.20}


def _scenarios(repo: Path, ref: str) -> list[tuple[str, bool]]:
    """(scenario path under scenarios/, is a hold-out), in order; hold-outs are listed by name only."""
    names = [(f"scenarios/{name}", False) for name in _git("ls-tree", "--name-only", f"{ref}:scenarios", cwd=repo).decode().split()
             if name.startswith("S")]
    try:
        held = _git("ls-tree", "--name-only", f"{ref}:scenarios/holdout", cwd=repo).decode().split()
    except subprocess.CalledProcessError:  # the reviewer has not added any yet
        held = []
    return names + [(f"scenarios/holdout/{name}", True) for name in held]


def _meta(repo: Path, ref: str, path: str) -> dict:
    return yaml.safe_load(_git("show", f"{ref}:{path}/scenario.yaml", cwd=repo)) or {}


def _has_patch(repo: Path, ref: str, path: str) -> bool:
    return bool(_git("ls-tree", "--name-only", ref, f"{path}/inject.patch", cwd=repo).strip())


def _changed_lines(diff: str) -> dict[str, set[int]]:
    """{path: the new-file line numbers the patch adds or removes at}: where a planted weakness can be."""
    lines: dict[str, set[int]] = {}
    for change in parse_diff(diff):
        lines.setdefault(change.path, set()).update(number for number, _text in [*change.added, *change.removed])
    return lines


def _chunks(lines: set[int]) -> list[tuple[int, int]]:
    """Contiguous changed regions of one file (a gap above LINE_SLACK starts a new one)."""
    out: list[list[int]] = []
    for number in sorted(lines):
        if out and number - out[-1][1] <= LINE_SLACK:
            out[-1][1] = number
        else:
            out.append([number, number])
    return [(first, last) for first, last in out]


def _near(suspect, changed: dict[str, set[int]]) -> bool:
    evidence = suspect.evidence
    return any(evidence.start_line - LINE_SLACK <= number <= evidence.end_line + LINE_SLACK
               for number in changed.get(evidence.path, ()))


def judge(repo: Path, ref: str, path: str, ask, facts: str, acceptances) -> dict:
    """One scenario, judged as a push and scored."""
    meta = _meta(repo, ref, path)
    name = str(meta.get("id") or path.rsplit("/", 1)[-1])
    row: dict = {"scenario": name, "title": meta.get("title", ""), "mode": meta.get("mode", ""),
                 "injection": meta.get("injection", ""), "min_severity": (meta.get("expected_finding") or {}).get("min_severity", "")}
    if not _has_patch(repo, ref, path):
        return {**row, "status": "needs the live run (no patch: a live change)"}
    with tempfile.TemporaryDirectory(prefix="wh-bench-") as folder:
        tree = Path(folder)
        _baseline_copy(repo, ref, tree)
        _apply(tree, path.rsplit("/", 1)[-1])
        diff = _staged_diff(tree)
        changed = _changed_lines(diff)
        files = sorted(changed)
        scope = Scope(files={REPO: frozenset(files)})
        started = time.monotonic()
        plan = checked(planner.for_push(REPO, f"demo/{name}", "benchmark", files, []), scope)
        decision = decide(tree, diff, planned=plan.paths_by_scanner(REPO), acceptances=acceptances)
        decided = time.monotonic() - started
        confirmations = confirm(decision, tree, ask, diff=diff, facts=facts)
        confirmed_in = time.monotonic() - started - decided
    escalated = list(decision.escalating)
    hits = [item for item in confirmations if _near(item.suspect, changed)]
    best = max(hits, key=lambda item: RANK.get(item.severity, 0), default=None)
    chunks = {(path_, region) for path_, lines in changed.items() for region in _chunks(lines)}
    sent = {(item.suspect.evidence.path, region) for item in confirmations for region in _chunks(changed.get(item.suspect.evidence.path, set()))
            if region[0] - LINE_SLACK <= item.suspect.evidence.start_line <= region[1] + LINE_SLACK}
    quiet = [str(entry).split()[0] for entry in meta.get("must_not_flag") or []]
    # A must-not-flag entry is a part of a file ("shop/pyproject.toml tool.ruff"), and the planted weakness can sit in
    # the same file: what counts against it is a finding above low in that file away from the planted lines.
    noisy = [f"{s.evidence.path}:{s.evidence.start_line} {s.rule_id} {s.severity}" for s in decision.suspects
             if s.evidence.path in quiet and s.is_above_low and s.status == "open" and not _near(s, changed)]
    negative = name == "S10" or "zero findings" in str(meta.get("mode", ""))
    found = bool(hits) and not negative
    row.update({
        "status": "scored", "negative_control": negative,
        "found": found, "confirmed": bool(best and best.status == CONFIRMED) if found else False,
        "severity_ok": bool(best and RANK.get(best.severity, 0) >= RANK.get(row["min_severity"], 0)) if found else False,
        "where": sorted({f"{item.suspect.evidence.path}:{item.suspect.evidence.start_line}" for item in hits}),
        "rules": sorted({f"{item.suspect.engine}/{item.suspect.rule_id}" for item in hits}),
        "severity": best.severity if best else "", "confirmation": best.status if best else "",
        "escalated": len(escalated), "elsewhere": sorted({f"{s.evidence.path}:{s.evidence.start_line} {s.rule_id}" for s in escalated
                                                          if not _near(s, changed)}),
        "must_not_flag": noisy, "chunks": len(chunks), "chunks_sent": len(sent),
        "prompt_tokens": sum(item.prompt_tokens for item in confirmations),
        "completion_tokens": sum(item.completion_tokens for item in confirmations),
        "engines": {run.engine: {"seconds": round(run.seconds, 1), "error": run.error} for run in decision.engines},
        "decide_seconds": round(decided, 1), "confirm_seconds": round(confirmed_in, 1),
        "plan_targets": len(plan.targets),
    })
    if negative:
        row["clean"] = not escalated
    return row


def summary(runs: list[list[dict]], price_in: float | None, price_out: float | None) -> dict:
    first = [row for row in runs[0] if row.get("status") == "scored"]
    planted = [row for row in first if not row["negative_control"] and not row["scenario"].startswith("H")]
    counted = sum(row["found"] and row["confirmed"] for row in planted)
    live = [row["scenario"] for row in runs[0] if row.get("status", "").startswith("needs")]
    negatives = [row for row in first if row["negative_control"]]
    tokens_in = sum(row["prompt_tokens"] for row in first)
    tokens_out = sum(row["completion_tokens"] for row in first)
    chunks, sent = sum(row["chunks"] for row in first), sum(row["chunks_sent"] for row in first)
    times = [row["decide_seconds"] + row["confirm_seconds"] for row in planted]
    same = all(_detections(run) == _detections(runs[0]) for run in runs[1:])
    cost = (round(tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out, 4)
            if price_in is not None and price_out is not None else None)
    return {
        "found_and_confirmed": counted, "found": sum(row["found"] for row in planted), "planted_scored": len(planted),
        "of": BAR["of"], "needs_live_run": live,
        "negative_control_clean": all(row.get("clean") for row in negatives) if negatives else None,
        "must_not_flag_clean": all(not row["must_not_flag"] for row in first),
        "chunk_share": round(sent / chunks, 3) if chunks else None, "chunks": chunks, "chunks_sent": sent,
        "prompt_tokens": tokens_in, "completion_tokens": tokens_out, "cost_usd": cost,
        "median_local_judge_seconds": round(statistics.median(times), 1) if times else None,
        "repeatable": same, "runs": len(runs),
        "holdout": [row["scenario"] for row in runs[0] if row.get("scenario", "").startswith("H")],
    }


def _detections(run: list[dict]) -> list[tuple]:
    return [(row["scenario"], row.get("found"), tuple(row.get("where", ())), row.get("clean")) for row in run]


def markdown(meta: dict, total: dict, runs: list[list[dict]]) -> str:
    bar_met = (total["found_and_confirmed"] >= BAR["found"] and total["negative_control_clean"] and total["must_not_flag_clean"]
               and total["chunk_share"] is not None and total["chunk_share"] < BAR["chunk_share"] and total["repeatable"])
    lines = [f"# Proving-ground scorecard, {meta['date']}", "",
             (f"Baseline `{meta['ref']}` ({meta['commit'][:7]}), WhiteHats `{meta['whitehats'][:7]}`, {total['runs']} runs, "
              f"model: {meta['model']}."), "",
             ("Each scenario is planted on a copy of the baseline and judged the way a push is judged: the scan plan, "
              "every engine it names and the house rules, then the model's confirmation. Timings are the platform's own "
              "judging time on this machine, not push-to-finding (that is the live run's)."), "",
             "## The v1 bar", "",
             (f"- Found, confirmed and at the right file and line: **{total['found_and_confirmed']} of {total['of']}** "
              f"(bar: {BAR['found']}). Found but not confirmed: {total['found'] - total['found_and_confirmed']}. "
              f"Not run here, needs the live run: {', '.join(total['needs_live_run']) or 'none'}."),
             (f"- Negative control S10: {'clean' if total['negative_control_clean'] else 'NOT clean'}. "
              f"`must_not_flag` files: {'clean' if total['must_not_flag_clean'] else 'NOT clean'}."),
             (f"- Share of changed chunks sent to the model: **{_pct(total['chunk_share'])}** "
              f"({total['chunks_sent']} of {total['chunks']}; bar: under {int(BAR['chunk_share'] * 100)} percent)."),
             f"- Model use for one full run: {total['prompt_tokens']} prompt and {total['completion_tokens']} completion tokens"
             + (f", USD {total['cost_usd']}." if total["cost_usd"] is not None else " (no price given, tokens only)."),
             f"- Median judging time per planted scenario on this machine: {total['median_local_judge_seconds']} s.",
             f"- Two runs give the same detections: **{'yes' if total['repeatable'] else 'NO'}**.",
             f"- Hold-out scenarios: {', '.join(total['holdout']) or 'none added yet'}.",
             "", f"**Bar met on what runs here: {'yes' if bar_met else 'no'}.**", "",
             "## Per scenario (first run)", "",
             "| Scenario | Found at the right place | Confirmed | Severity (min) | Where | Engine and rule | Judging s |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in runs[0]:
        if row.get("status") != "scored":
            lines.append(f"| {row['scenario']} | {row['status']} | | | | | |")
            continue
        if row["negative_control"]:
            found = "clean (nothing escalated)" if row.get("clean") else f"NOT clean: {row['escalated']} escalated"
            lines.append(f"| {row['scenario']} | {found} | | | {', '.join(row['elsewhere'])} | | "
                         f"{row['decide_seconds'] + row['confirm_seconds']:.1f} |")
            continue
        lines.append(f"| {row['scenario']} | {'yes' if row['found'] else 'NO'} | {row['confirmation'] or '-'} | "
                     f"{row['severity'] or '-'} ({row['min_severity']}) | {', '.join(row['where']) or '-'} | "
                     f"{', '.join(row['rules']) or '-'} | {row['decide_seconds'] + row['confirm_seconds']:.1f} |")
    misses = [row for row in runs[0] if row.get("status") == "scored" and not row["negative_control"] and not row["found"]]
    if misses or any(row.get("must_not_flag") for row in runs[0]):
        lines += ["", "## What did not hold", ""]
        for row in misses:
            lines.append(f"- {row['scenario']} ({row['title']}): nothing escalated at the planted lines"
                         + (f"; escalated elsewhere: {', '.join(row['elsewhere'])}" if row["elsewhere"] else "") + ".")
        for row in runs[0]:
            if row.get("must_not_flag"):
                lines.append(f"- {row['scenario']}: above low in a must-not-flag file: {', '.join(row['must_not_flag'])}.")
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f} percent"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True, help="a clone of the proving-ground repository")
    parser.add_argument("--ref", default="pg-baseline")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--offline", action="store_true", help="no model: nothing is confirmed")
    parser.add_argument("--scenario", action="append", help="only these (repeat); default all")
    parser.add_argument("--out", type=Path, help="folder for the scorecard; default <repo>/results")
    parser.add_argument("--price-in", type=float, help="USD per million prompt tokens")
    parser.add_argument("--price-out", type=float, help="USD per million completion tokens")
    args = parser.parse_args()
    repo = args.repo.resolve()
    commit = _git("rev-parse", f"{args.ref}^{{commit}}", cwd=repo).decode().strip()
    with tempfile.TemporaryDirectory() as folder:
        accepted = Path(folder) / "accepted.yaml"
        accepted.write_bytes(_git("show", f"{commit}:accepted-findings.yaml", cwd=repo))
        acceptances = load_acceptances(accepted)
    facts = _service_facts(repo, commit)
    ask = _offline_ask if args.offline else _model_ask()
    chosen = [(path, held) for path, held in _scenarios(repo, commit)
              if not args.scenario or path.rsplit("/", 1)[-1] in args.scenario]
    runs = []
    for number in range(args.runs):
        run = []
        for path, _hold_out in chosen:
            row = judge(repo, commit, path, ask, facts, acceptances)
            print(f"run {number + 1} {row['scenario']}: " + json.dumps({key: row.get(key) for key in (
                "status", "found", "confirmed", "where", "severity", "clean", "decide_seconds", "confirm_seconds")}), flush=True)
            run.append(row)
        runs.append(run)
    whitehats = _git("rev-parse", "HEAD", cwd=BACKEND).decode().strip()
    meta = {"date": datetime.now(UTC).strftime("%Y-%m-%d"), "ref": args.ref, "commit": commit, "whitehats": whitehats,
            "model": "none (offline)" if args.offline else "the gateway's model for the seeded company"}
    total = summary(runs, args.price_in, args.price_out)
    out = (args.out or repo / "results").resolve()
    out.mkdir(parents=True, exist_ok=True)
    stem = out / meta["date"]
    stem.with_suffix(".json").write_text(json.dumps({"meta": meta, "summary": total, "runs": runs}, indent=2), encoding="utf-8")
    stem.with_suffix(".md").write_text(markdown(meta, total, runs), encoding="utf-8")
    print(json.dumps(total, indent=2))
    print(f"wrote {stem}.md and .json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

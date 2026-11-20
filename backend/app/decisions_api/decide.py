"""Decide whether a tree, or the diff that changed it, is clean or suspect.

Engines run in parallel, each in its own container. House rules run in this
process. Nothing in this package calls a model.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .container import folders_visible
from .diff import FileChange, parse_diff
from .rules.run import scan as scan_rules
from .scanners import SCANNERS
from .suspect import OPEN, Acceptance, Decision, EngineRun, Suspect

Scanner = Callable[[Path, list[str] | None], tuple[list[Suspect], EngineRun]]
TIMEOUT = int(os.environ.get("WH_DECISIONS_ENGINE_TIMEOUT", "45"))
NOT_VISIBLE = "the scan folder is not visible to Docker, so the engine would have scanned an empty folder"


def _lines(changes: list[FileChange]) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for change in changes:
        numbers = {number for number, _ in change.added}
        numbers.update(number for number, _ in change.removed)
        found[change.path] = numbers
    return found


def _overlaps(suspect: Suspect, changed: dict[str, set[int]] | None) -> bool:
    if changed is None:
        return True
    numbers = changed.get(suspect.evidence.path)
    if numbers is None:
        return False
    if not numbers:
        return True
    start = suspect.evidence.start_line
    end = max(suspect.evidence.end_line, start)
    return any(start <= number <= end for number in numbers)


def _dedupe(suspects: list[Suspect]) -> list[Suspect]:
    seen: set[tuple[str, str, str, int, str]] = set()
    kept: list[Suspect] = []
    for suspect in suspects:
        key = (
            suspect.engine, suspect.rule_id, suspect.evidence.path,
            suspect.evidence.start_line, suspect.evidence.resource,
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(suspect)
    kept.sort(key=lambda item: (item.evidence.path, item.evidence.start_line, item.engine, item.rule_id))
    return kept


def _run_planned(root: Path, planned: Mapping[str, Sequence[str]], scanners: Sequence[Scanner],
                 timeout: int) -> tuple[list[Suspect], list[EngineRun]]:
    """Each scanner the plan names, on its own files only, all at once; gitleaks reads a whole tree, so what each
    scanner finds is narrowed to its files."""
    work = [(scanner, list(planned.get(str(getattr(scanner, "name", "")), ()))) for scanner in scanners]
    work = [(scanner, files) for scanner, files in work if files]
    if not work:
        return [], []

    def invoke(item: tuple[Scanner, list[str]]) -> tuple[list[Suspect], list[EngineRun]]:
        scanner, files = item
        found, runs = _run_scanners(root, files, [scanner], timeout)
        return [suspect for suspect in found if suspect.evidence.path in set(files)], runs

    suspects: list[Suspect] = []
    runs: list[EngineRun] = []
    with ThreadPoolExecutor(max_workers=len(work)) as pool:
        for found, engine in pool.map(invoke, work):
            suspects.extend(found)
            runs.extend(engine)
    return suspects, runs


def _run_scanners(
    root: Path, paths: list[str] | None, scanners: Sequence[Scanner], timeout: int,
) -> tuple[list[Suspect], list[EngineRun]]:
    if not scanners:
        return [], []
    if shutil.which("docker") and not folders_visible():  # an engine would scan an empty folder and find nothing
        return [], [EngineRun(str(getattr(scanner, "name", "engine")), str(getattr(scanner, "image", "")), 0.0,
                              error=NOT_VISIBLE) for scanner in scanners]
    suspects: list[Suspect] = []
    runs: list[EngineRun] = []

    def invoke(scanner: Scanner) -> tuple[list[Suspect], EngineRun]:
        try:
            return scanner(root, paths, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - one engine failing must not hide the others
            name = getattr(scanner, "name", "engine")
            image = getattr(scanner, "image", "")
            return [], EngineRun(str(name), str(image), 0.0, error=type(exc).__name__)

    with ThreadPoolExecutor(max_workers=len(scanners)) as pool:
        for found, run in pool.map(invoke, scanners):
            suspects.extend(found)
            runs.append(run)
    return suspects, runs


def decide(
    root: Path | str,
    diff: str | None = None,
    *,
    acceptances: Sequence[Acceptance] = (),
    scanners: Sequence[Scanner] | None = None,
    planned: Mapping[str, Sequence[str]] | None = None,
    timeout: int = TIMEOUT,
) -> Decision:
    """Return clean, or suspect when something open is above low.

    Pass `diff` (unified, as `git diff` prints it) to judge only that change.
    With no diff, the whole tree is judged. `scanners=()` runs house rules only.
    `planned` is a scan plan's {scanner name: files} (SCAN.1): each named scanner runs on its own files and keeps
    only what it says about them; a scanner the plan does not name does not run. The house rules always run.
    """
    tree = Path(root).resolve()
    changes = parse_diff(diff) if diff is not None else None
    paths = [change.path for change in changes] if changes is not None else None
    changed = _lines(changes) if changes is not None else None
    suspects = scan_rules(tree, changes, paths)
    chosen = SCANNERS if scanners is None else scanners
    if planned is not None:
        engine_suspects, runs = _run_planned(tree, planned, chosen, timeout)
    elif paths is not None and not paths:
        engine_suspects, runs = [], []
    else:
        engine_suspects, runs = _run_scanners(tree, paths, chosen, timeout)
    suspects.extend(engine_suspects)
    suspects = [suspect for suspect in suspects if _overlaps(suspect, changed)]
    for suspect in suspects:
        for acceptance in acceptances:
            if acceptance.covers(suspect) and suspect.status == OPEN:
                suspect.status = acceptance.status
                break
    suspects = _dedupe(suspects)
    verdict = "suspect" if any(suspect.status == OPEN and suspect.is_above_low for suspect in suspects) else "clean"
    return Decision(verdict, suspects, runs, paths)

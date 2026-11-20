"""Rules and prompts must not name a scenario or a string that exists only in a scenario patch."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1] / "app"
_PACKAGES = (_APP / "decisions_api", _APP / "confirmation")  # the rules, and the confirmation prompt
_SCENARIO_ID = re.compile(r"\bS(?:0[1-9]|1[0-2])\b|\bH0[1-3]\b")
_FILE_NAMES = ("inject.patch", "scenario.yaml", "drift.sh")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{15,}")


def _package_text() -> str:
    parts = []
    for path in (item for package in _PACKAGES for item in package.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".md", ".toml"} and "__pycache__" not in path.parts:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


PINNED = Path(__file__).resolve().parent / "data" / "scenario_strings.json"
_SKIPPED_PREFIXES = ("scenarios/", ".git/", ".pytest_cache/", ".ruff_cache/")


def _added_lines(patch: str) -> list[str]:
    lines = []
    for line in patch.splitlines():
        if line.startswith(("+++", "---", "diff ", "@@", "index ")):
            continue
        if line.startswith("+"):
            text = line[1:].strip()
            if text:
                lines.append(text)
    return lines


def scenario_only_strings(files: dict[str, str]) -> tuple[list[str], list[str]]:
    """From the proving ground's files (path -> text): the strings only a scenario patch adds, and the scenario ids."""
    baseline = "\n".join(text for name, text in files.items()
                         if not name.startswith(_SKIPPED_PREFIXES) and len(text) <= 1_000_000)
    forbidden: set[str] = set()
    scenarios = []
    for name, text in sorted(files.items()):
        parts = name.split("/")
        if len(parts) != 3 or parts[0] != "scenarios" or parts[2] != "inject.patch":
            continue
        scenarios.append(parts[1])
        for line in _added_lines(text):
            if line not in baseline and (len(line) >= 12 or any(char.isdigit() for char in line)):
                forbidden.add(line)
            for token in _TOKEN.findall(line):
                if token not in baseline:
                    forbidden.add(token)
    return sorted(forbidden, key=lambda item: (-len(item), item)), scenarios


def scenario_modes(files: dict[str, str]) -> dict[str, str]:
    """Each scenario's stated mode (the `mode: ...` line of its scenario.yaml), read as plain lines."""
    modes = {}
    for name, text in files.items():
        parts = name.split("/")
        if len(parts) == 3 and parts[0] == "scenarios" and parts[2] == "scenario.yaml":
            line = next((line for line in text.splitlines() if line.startswith("mode: ")), None)
            if line:
                modes[parts[1]] = line[len("mode: "):]
    return dict(sorted(modes.items()))


def pinned() -> dict:
    """The pinned copy; a missing pin is a failure, never a pass."""
    assert PINNED.is_file(), f"{PINNED} is missing: run scripts/pin_scenario_strings.py --repo <proving ground>"
    return json.loads(PINNED.read_text(encoding="utf-8"))


def _checkout_files(root: Path) -> dict[str, str]:
    files = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or relative.startswith((".git/", ".pytest_cache/", ".ruff_cache/")):
            continue
        if path.suffix.lower() in {".png", ".zip", ".pyc"} or path.name == "tfplan":
            continue
        files[relative] = path.read_text(encoding="utf-8", errors="ignore")
    return files


def test_rules_do_not_name_a_scenario_or_a_scenario_only_string():
    blob = _package_text()
    assert _SCENARIO_ID.search(blob) is None
    for name in _FILE_NAMES:
        assert name not in blob
    # The scenario-only strings are pinned from proving-ground main.
    pin = pinned()
    assert len(pin["scenarios"]) >= 11 and pin["strings"], "the pin holds no scenarios"
    for needle in pin["strings"]:
        assert needle not in blob, needle[:160]


def test_the_pin_matches_the_proving_ground_when_one_is_given():
    """With PROVING_GROUND pointing at a checkout of main, the pin must be what that checkout gives today."""
    root = os.environ.get("PROVING_GROUND")
    if not root:
        pytest.skip("PROVING_GROUND is not set; the pinned copy is checked by the test above")
    files = _checkout_files(Path(root))
    strings, scenarios = scenario_only_strings(files)
    pin = pinned()
    assert (pin["strings"], pin["scenarios"], pin["modes"]) == (strings, scenarios, scenario_modes(files)), \
        "the proving ground changed: run scripts/pin_scenario_strings.py again"

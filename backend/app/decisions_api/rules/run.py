"""Run the house rules over a tree and the diff that produced it."""

from __future__ import annotations

import os
from pathlib import Path

from ..diff import FileChange
from ..suspect import Evidence, Suspect
from .base import ChangeRule, Example, FileRule, Hit, Rule
from .image_pin import RULE as IMAGE_PIN
from .integrity_check import RULE as INTEGRITY_CHECK
from .owner_check import RULE as OWNER_CHECK
from .provider_key import RULE as PROVIDER_KEY
from .sensitive_output import RULE as SENSITIVE_OUTPUT
from .storage_key import RULE as STORAGE_KEY
from .swallowed_error import RULE as SWALLOWED_ERROR
from .unsafe_load import RULE as UNSAFE_LOAD

RULES: tuple[Rule, ...] = (
    PROVIDER_KEY,
    OWNER_CHECK,
    INTEGRITY_CHECK,
    STORAGE_KEY,
    UNSAFE_LOAD,
    SENSITIVE_OUTPUT,
    SWALLOWED_ERROR,
    IMAGE_PIN,
)

_SKIP_DIRS = {
    ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules",
    ".terraform", "dist", ".venv", "venv",
}
_BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip", ".ttf", ".woff", ".pyc", ".so", ".bin"}


def evaluate(rule: Rule, example: Example) -> list[Hit]:
    """Run one example the way the rule's own tests do."""
    if isinstance(rule, ChangeRule):
        from ..diff import parse_diff
        hits: list[Hit] = []
        for change in parse_diff(example.text):
            hits.extend(rule.check_change(change))
        return hits
    if isinstance(rule, FileRule):
        if example.path and not rule.applies_to(example.path):
            return []
        return rule.check_file(example.path, example.text)
    return []


def _read(path: Path) -> str | None:
    if path.suffix.lower() in _BINARY or path.name == "tfplan":
        return None
    try:
        if path.stat().st_size > 1_000_000:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _files(root: Path, selected: set[str] | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if selected is not None:
        for rel in sorted(selected):
            text = _read(root / rel)
            if text is not None:
                found.append((rel, text))
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            text = _read(path)
            if text is None:
                continue
            found.append((path.relative_to(root).as_posix(), text))
    return found


def hit_to_suspect(rule: Rule, hit: Hit) -> Suspect:
    return Suspect(
        engine="whitehats",
        rule_id=hit.rule_id,
        title=rule.title,
        evidence=Evidence(hit.path, hit.start_line, hit.end_line),
        severity=hit.severity or rule.severity,
        cwe=list(rule.cwe),
        note=hit.detail,
    )


def scan(root: Path, changes: list[FileChange] | None, paths: list[str] | None) -> list[Suspect]:
    selected = set(paths) if paths is not None else None
    files = _files(root, selected)
    suspects: list[Suspect] = []
    for rule in RULES:
        if isinstance(rule, ChangeRule):
            if not changes:
                continue
            for change in changes:
                if selected is not None and change.path not in selected:
                    continue
                suspects.extend(hit_to_suspect(rule, hit) for hit in rule.check_change(change))
            continue
        if isinstance(rule, FileRule):
            for rel, text in files:
                if rule.applies_to(rel):
                    suspects.extend(hit_to_suspect(rule, hit) for hit in rule.check_file(rel, text))
    return suspects

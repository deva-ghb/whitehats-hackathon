"""Does an OSV record's `affected` entry cover one version of a package? (KB.1)

OSV says it two ways, and both are read: a list of `versions`, and `ranges` of events (`introduced`, `fixed`,
`last_affected`, `limit`) in the ecosystem's own version order. PyPI orders by PEP 440 (the `packaging` library);
npm by semantic versioning, parsed here with plain string splitting. A version that cannot be read is never
called affected by a range, only by an exact entry in `versions`.
"""

from __future__ import annotations

from functools import cmp_to_key
from typing import Any

from packaging.version import InvalidVersion, Version


def _pypi(text: str) -> Version | None:
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _semver(text: str) -> tuple | None:
    """`1.2.3-beta.1+build` as ((1, 2, 3), prerelease parts) with build metadata dropped; None when unreadable."""
    core = text.strip().lstrip("v").partition("+")[0]
    numbers, _, pre = core.partition("-")
    parts = numbers.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    prerelease = tuple((0, int(item), "") if item.isdigit() else (1, 0, item) for item in pre.split(".")) if pre else ()
    return tuple(int(part) for part in parts), prerelease


def _compare_semver(left: tuple, right: tuple) -> int:
    if left[0] != right[0]:
        return -1 if left[0] < right[0] else 1
    # A release sorts after any of its prereleases; prerelease parts compare one by one.
    if not left[1] or not right[1]:
        return (len(right[1]) > 0) - (len(left[1]) > 0)
    if left[1] == right[1]:
        return 0
    return -1 if left[1] < right[1] else 1


def parse(ecosystem: str, text: str) -> Any:
    if text == "0":
        return "zero"  # OSV's "from the very first version"
    return _pypi(text) if ecosystem == "PyPI" else _semver(text)


def compare(ecosystem: str, left: Any, right: Any) -> int:
    if left == "zero" or right == "zero":
        return 0 if left == right else (-1 if left == "zero" else 1)
    if ecosystem == "PyPI":
        return (left > right) - (left < right)
    return _compare_semver(left, right)


def sort_key(ecosystem: str):
    return cmp_to_key(lambda a, b: compare(ecosystem, a, b))


def affects(ecosystem: str, affected: dict, version: str) -> bool:
    """True when this `affected` entry of a record covers `version` of its package."""
    if version in (affected.get("versions") or []):
        return True
    wanted = parse(ecosystem, version)
    if wanted is None or wanted == "zero":
        return False
    for span in affected.get("ranges") or []:
        if span.get("type") not in ("ECOSYSTEM", "SEMVER"):
            continue  # a GIT range names commits, not package versions
        if _inside(ecosystem, span.get("events") or [], wanted):
            return True
    return False


def _inside(ecosystem: str, events: list[dict], wanted: Any) -> bool:
    """Walk the events in version order: inside after an `introduced`, outside after a `fixed` or past a `last_affected`."""
    marks = []
    for event in events:
        for kind in ("introduced", "fixed", "last_affected", "limit"):
            if kind in event:
                at = parse(ecosystem, str(event[kind]))
                if at is not None:
                    marks.append((at, kind))
    marks.sort(key=lambda mark: sort_key(ecosystem)(mark[0]))
    inside = False
    for at, kind in marks:
        order = compare(ecosystem, wanted, at)
        if kind == "introduced":
            if order < 0:
                break
            inside = True
        elif kind == "fixed" and order < 0:
            return inside
        elif kind == "fixed":
            inside = False
        elif kind == "last_affected" and order <= 0:
            return inside
        elif kind == "last_affected":
            inside = False
        elif kind == "limit" and order >= 0:
            return False
    return inside


def normalise(ecosystem: str, name: str) -> str:
    """The name OSV and the package index agree on: PyPI folds case and treats - _ . alike; npm is exact."""
    if ecosystem != "PyPI":
        return name.strip()
    lowered = name.strip().lower()
    for mark in ("_", "."):
        lowered = lowered.replace(mark, "-")
    while "--" in lowered:
        lowered = lowered.replace("--", "-")
    return lowered

"""A scan plan (spec 5.3, SCAN.1): which files and images to scan, with which catalogue scanner, and why.

A plan comes in as plain data, as an agent would write it, and is checked before anything runs:
- only scanners from the catalogue;
- only targets inside the scope the project attached (its repositories' files, the images on its twin);
- nothing but a target, its scanners and a reason: a plan cannot carry scanner options, flags or anything else.
A refused plan runs nothing; the refusal is kept with the plan.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from .catalogue import CATALOGUE, defaults

PLAN_KEYS = frozenset({"trigger", "made_by", "targets"})
TARGET_KEYS = frozenset({"repo", "path", "image", "scanners", "reason"})


class PlanRefused(ValueError):
    """The plan asks for something it may not have. The message says what, in one line."""


@dataclass(frozen=True)
class Target:
    scanners: tuple[str, ...]
    reason: str
    repo: str = ""
    path: str = ""
    image: str = ""


@dataclass(frozen=True)
class Plan:
    trigger: str
    made_by: str
    targets: tuple[Target, ...] = ()

    def to_dict(self) -> dict:
        return {"trigger": self.trigger, "made_by": self.made_by,
                "targets": [{key: value for key, value in asdict(target).items() if value} | {"scanners": list(target.scanners)}
                            for target in self.targets]}

    def paths_by_scanner(self, repo: str) -> dict[str, list[str]]:
        """{scanner: the files of `repo` it scans}, for the scanners that read files."""
        out: dict[str, list[str]] = {}
        for target in self.targets:
            if target.path and target.repo == repo:
                for name in target.scanners:
                    out.setdefault(name, []).append(target.path)
        return {name: sorted(set(paths)) for name, paths in out.items()}

    def images(self) -> list[str]:
        return sorted({target.image for target in self.targets if target.image})


@dataclass(frozen=True)
class Scope:
    """What a plan may touch: the files of each attached repository the run can read, and the twin's images."""

    files: dict[str, frozenset[str]] = field(default_factory=dict)  # repo -> paths
    images: frozenset[str] = frozenset()


def checked(data: object, scope: Scope) -> Plan:
    """The plan, once every part of it is allowed; PlanRefused naming the first part that is not."""
    if not isinstance(data, dict):
        raise PlanRefused("A scan plan must be an object.")
    extra = sorted(set(data) - PLAN_KEYS)
    if extra:
        raise PlanRefused(f"A scan plan may not carry {', '.join(extra)}.")
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise PlanRefused("A scan plan needs at least one target.")
    return Plan(str(data.get("trigger") or ""), str(data.get("made_by") or ""),
                tuple(_target(item, scope) for item in targets))


def _target(item: object, scope: Scope) -> Target:
    if not isinstance(item, dict):
        raise PlanRefused("Each target must be an object.")
    extra = sorted(set(item) - TARGET_KEYS)
    if extra:  # options, flags, args, config: a scanner's options come from the catalogue, never from a plan
        raise PlanRefused(f"A target may not carry {', '.join(extra)}; scanner options come from the catalogue.")
    scanners = item.get("scanners")
    if not isinstance(scanners, list) or not scanners or not all(isinstance(name, str) for name in scanners):
        raise PlanRefused("Each target names the catalogue scanners to run on it.")
    unknown = sorted(set(scanners) - set(CATALOGUE))
    if unknown:
        raise PlanRefused(f"{', '.join(unknown)} is not in the scanner catalogue.")
    reason = str(item.get("reason") or "").strip()
    if not reason:
        raise PlanRefused("Each target says in one line why it is scanned.")
    repo, path, image = (str(item.get(key) or "") for key in ("repo", "path", "image"))
    if bool(path) == bool(image):
        raise PlanRefused("A target is one file or one image.")
    if path:
        if repo not in scope.files:
            raise PlanRefused(f"{repo or 'That repository'} is not attached to this project.")
        if path not in scope.files[repo]:
            raise PlanRefused(f"{path} is not a file of {repo} this run can read.")
    elif image not in scope.images:
        raise PlanRefused(f"{image} is not an image on this project's twin.")
    return Target(tuple(dict.fromkeys(scanners)), reason, repo, path, image)


def every_file(repo: str, paths: Iterable[str], reason: str) -> list[dict]:
    """Targets for every file with its default scanners (the floor, and the fallback when a plan is refused)."""
    return [{"repo": repo, "path": path, "scanners": list(defaults(path)), "reason": reason} for path in sorted(paths)]

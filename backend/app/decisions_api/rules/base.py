"""The shape every WhiteHats rule written in Python shares.

A rule names a weakness class, not a place: "a guard that checked ownership was
taken away", never a file or function of one repository. Each rule carries
examples that must match and examples that must not, and a test runs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..diff import FileChange


@dataclass(frozen=True)
class Hit:
    rule_id: str
    path: str
    start_line: int
    end_line: int
    detail: str = ""
    severity: str = ""


@dataclass(frozen=True)
class Example:
    """A file (path and text) for file rules, or a unified diff for change rules."""

    text: str
    path: str = ""


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    weakness: str
    cwe: tuple[str, ...]
    severity: str
    must_match: tuple[Example, ...] = field(default_factory=tuple)
    must_not_match: tuple[Example, ...] = field(default_factory=tuple)


class FileRule(Rule):
    """Looks at one file's whole text."""

    def applies_to(self, path: str) -> bool:
        raise NotImplementedError

    def check_file(self, path: str, text: str) -> list[Hit]:
        raise NotImplementedError


class ChangeRule(Rule):
    """Looks at what a change took away or added, which a scan of the result alone cannot see."""

    def check_change(self, change: FileChange) -> list[Hit]:
        raise NotImplementedError

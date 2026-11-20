"""Cut the evidence for one suspect: the flagged lines, the lines around them, and what the change removed.

Only the suspect's own span leaves the tree, never the whole file or the whole diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..decisions_api.diff import FileChange
from ..decisions_api.suspect import Suspect

CONTEXT_LINES = 20  # on each side of the span, as VS.3 asks
SPAN_LIMIT = 60  # a long Terraform resource is cut here; the start of a block says what it is
LINE_LIMIT = 240  # characters per line; minified or generated lines would otherwise eat the budget


@dataclass(frozen=True)
class Line:
    number: int
    text: str


@dataclass
class Chunk:
    """What the model sees about one suspect. Every text field is untrusted data from the repository."""

    path: str
    resource: str
    start_line: int
    end_line: int
    flagged: list[Line] = field(default_factory=list)
    before: list[Line] = field(default_factory=list)
    after: list[Line] = field(default_factory=list)
    removed: list[Line] = field(default_factory=list)  # lines the change took away, at the line they sat at

    def narrowed(self, context: int) -> Chunk:
        """The same chunk with at most `context` lines on each side."""
        return Chunk(self.path, self.resource, self.start_line, self.end_line, self.flagged,
                     self.before[len(self.before) - context:] if context else [],
                     self.after[:context], self.removed)


def _clip(text: str) -> str:
    return text if len(text) <= LINE_LIMIT else text[:LINE_LIMIT] + " [line cut]"


def _read_lines(root: Path, relative: str) -> list[str]:
    """The file's lines, or none when the path is not a file inside `root` (a deleted file, a live resource)."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _numbered(lines: list[str], first: int, last: int) -> list[Line]:
    """1-based, inclusive, clipped to the file."""
    first, last = max(first, 1), min(last, len(lines))
    return [Line(number, _clip(lines[number - 1])) for number in range(first, last + 1)]


def _removed_near(change: FileChange | None, first: int, last: int) -> list[Line]:
    if change is None:
        return []
    return [Line(number, _clip(text)) for number, text in change.removed if first <= number <= last]


def cut(suspect: Suspect, root: Path | str, changes: list[FileChange] | None = None) -> Chunk:
    """The chunk for `suspect` in the tree at `root`, with the removed lines from `changes` when given."""
    tree = Path(root).resolve()
    evidence = suspect.evidence
    start = max(evidence.start_line, 1)
    end = max(evidence.end_line, start)
    shown_end = min(end, start + SPAN_LIMIT - 1)
    lines = _read_lines(tree, evidence.path)
    change = next((item for item in changes or [] if item.path == evidence.path), None)
    return Chunk(
        path=evidence.path,
        resource=evidence.resource,
        start_line=start,
        end_line=end,
        flagged=_numbered(lines, start, shown_end),
        before=_numbered(lines, start - CONTEXT_LINES, start - 1),
        after=_numbered(lines, end + 1, end + CONTEXT_LINES),
        removed=_removed_near(change, start - CONTEXT_LINES, end + CONTEXT_LINES),
    )

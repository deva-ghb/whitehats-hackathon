"""Read a unified diff (as `git diff` prints it) into changed files and their lines.

Removed lines keep the line number in the new file where they used to be, so a
finding about something taken away still points at a real place in the change.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileChange:
    path: str
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line number, text)
    removed: list[tuple[int, str]] = field(default_factory=list)  # (new line number it sat at, text)


def parse_diff(text: str) -> list[FileChange]:
    changes: list[FileChange] = []
    current: FileChange | None = None
    new_line = old_left = new_left = 0
    for line in text.splitlines():
        if line.startswith("\\"):  # "\ No newline at end of file" is a remark, not a line
            continue
        if old_left > 0 or new_left > 0:  # inside a hunk, a line is content even if it looks like a header
            if current is not None and line.startswith("+"):
                current.added.append((new_line, line[1:]))
            elif current is not None and line.startswith("-"):
                current.removed.append((new_line, line[1:]))
            if not line.startswith("-"):
                new_line += 1
                new_left -= 1
            if not line.startswith("+"):
                old_left -= 1
            continue
        if line.startswith("+++ "):
            current = _start_file(line[4:], changes)
        elif line.startswith("@@"):
            old_left, new_line, new_left = _hunk_ranges(line)
    return changes


def changed_paths(changes: list[FileChange]) -> list[str]:
    return [c.path for c in changes]


def _start_file(target: str, changes: list[FileChange]) -> FileChange | None:
    target = target.split("\t", 1)[0].strip()
    if target == "/dev/null":
        return None  # a deleted file has nothing left to point at
    change = FileChange(path=target.removeprefix("b/"))
    changes.append(change)
    return change


def _hunk_ranges(header: str) -> tuple[int, int, int]:
    """'@@ -12,7 +14,8 @@ name' -> (7, 14, 8): old line count, new start, new line count."""
    parts = header.split()
    if len(parts) < 3 or not parts[1].startswith("-") or not parts[2].startswith("+"):
        raise ValueError(f"not a diff hunk header: {header!r}")
    return _count(parts[1]), int(parts[2][1:].split(",", 1)[0]), _count(parts[2])


def _count(range_text: str) -> int:
    """'-12,7' -> 7; a range with no count covers one line."""
    _, _, count = range_text[1:].partition(",")
    return int(count) if count else 1

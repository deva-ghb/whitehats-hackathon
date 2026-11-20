"""Read one change from GitHub: its diff, and only the files the diff touches, as a tree on disk.

The tree holds nothing else, so an engine judging it sees the changed files and no more. It is a
temporary folder, gone when the caller is done; the customer's code is not kept.
"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..connectors.github import GitHub
from ..decisions_api.diff import parse_diff

MAX_FILES = 300  # GitHub itself stops a comparison at 300 files
MAX_TOTAL_BYTES = 20_000_000  # a change bigger than this is read as far as it goes, and the rest is said to be skipped
WORKERS = 6  # files read at once; each is a round trip to GitHub, so one by one is slow for a big change


@dataclass
class Fetched:
    """A change as read: the diff, the tree holding the changed files, and what was left out and why."""

    diff: str
    root: Path
    files: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {"path": ..., "reason": ...}


def _unsafe(path: str) -> str:
    """Why this changed path must not be written to disk, or an empty string when it is fine."""
    parts = PurePosixPath(path).parts
    if path.startswith('"'):
        return "its name has unusual characters"
    if PurePosixPath(path).is_absolute() or ".." in parts:
        return "its path leaves the repository"
    return ""


class _Reader:
    """Reads changed files at one commit into a folder, from several threads, stopping at the size limit."""

    def __init__(self, source: GitHub, repo: str, head: str, root: Path):
        self.source, self.repo, self.head, self.root = source, repo, head, root
        self._total = 0
        self._lock = threading.Lock()

    def read(self, path: str) -> str:
        """Write the file under the root. Returns why it was not read, or an empty string."""
        with self._lock:
            if self._total >= MAX_TOTAL_BYTES:
                return "the change is bigger than WhiteHats reads"
        data, reason = self.source.file_at(self.repo, path, self.head)
        if data is None:
            return reason
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        with self._lock:
            self._total += len(data)
        return ""


@contextmanager
def fetched(source: GitHub, repo: str, base: str, head: str) -> Iterator[Fetched]:
    """The diff of `head` against `base`, and each changed file at `head`. SourceError when GitHub cannot say."""
    diff = source.compare_diff(repo, base, head)
    with tempfile.TemporaryDirectory(prefix="whitehats-change-") as folder:
        result = Fetched(diff, Path(folder).resolve())
        paths = list(dict.fromkeys(change.path for change in parse_diff(diff)))
        reasons = {path: _unsafe(path) for path in paths}
        for path in paths[MAX_FILES:]:
            reasons[path] = reasons[path] or f"more than {MAX_FILES} files changed"
        readable = [path for path in paths if not reasons[path]]
        reader = _Reader(source, repo, head, result.root)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            reasons.update(zip(readable, pool.map(reader.read, readable), strict=True))
        for path in paths:
            if reasons[path]:
                result.skipped.append({"path": path, "reason": reasons[path]})
            else:
                result.files.append(path)
        yield result

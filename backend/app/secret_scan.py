"""Find a secret value in a database, a file, a log line or an API response.

The check for the vault is that a value used in tests never appears in those
places. Ciphertext is still scanned: a sealed value that contains its own
plaintext has not been sealed.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_FILE_SUFFIXES = {".log", ".json", ".db", ".sqlite", ".sqlite3", ".txt"}
_MIN_NEEDLE = 8  # shorter text matches ordinary words, so it is not a useful needle


def find_leaks(secrets: Iterable[str], labeled: Iterable[tuple[str, str]]) -> list[str]:
    """Labels of texts that contain one of the secrets. The secret itself is not copied into the label."""
    needles = [(index, secret) for index, secret in enumerate(secrets) if len(secret) >= _MIN_NEEDLE]
    found: list[str] = []
    seen: set[str] = set()
    for label, blob in labeled:
        item = _first_hit(label, blob, needles)
        if item and item not in seen:
            seen.add(item)
            found.append(item)
    return found


def database_cells(engine: Engine) -> list[tuple[str, str]]:
    """Every cell of every table, labelled table.column#row."""
    info = inspect(engine)
    found: list[tuple[str, str]] = []
    with engine.connect() as conn:
        for table in info.get_table_names():
            found.extend(_table_cells(conn, table, [column["name"] for column in info.get_columns(table)]))
    return found


def file_texts(roots: Iterable[Path]) -> list[tuple[str, str]]:
    """Log files, exports and database files under these paths, read as text."""
    found: list[tuple[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        paths = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        for path in paths:
            if _worth_reading(path):
                found.append((str(path), path.read_bytes().decode("utf-8", "replace")))
    return found


def collect(engine: Engine, roots: Iterable[Path], extra: Iterable[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """Responses and logs the caller captured, plus every database cell and file under the roots."""
    labeled = list(extra)
    labeled.extend(database_cells(engine))
    labeled.extend(file_texts(roots))
    return labeled


def _first_hit(label: str, blob: str, needles: list[tuple[int, str]]) -> str:
    for index, secret in needles:
        if secret in blob:
            return f"{label} (needle {index})"
    return ""


def _table_cells(conn, table: str, names: list[str]) -> list[tuple[str, str]]:
    if not names:
        return []
    sql = "SELECT " + ", ".join(_quote(name) for name in names) + f" FROM {_quote(table)}"
    found = []
    for index, row in enumerate(conn.execute(text(sql))):
        for name, value in zip(names, row, strict=True):
            if value is not None:
                found.append((f"{table}.{name}#{index}", _as_text(value)))
    return found


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _quote(name: str) -> str:
    return '"' + name.replace('"', "") + '"'


def _worth_reading(path: Path) -> bool:
    return path.suffix.lower() in _FILE_SUFFIXES or path.name.endswith(("-wal", "-shm"))

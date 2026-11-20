"""A broad error handler discards the error.

The class is "except Exception, or a bare except, whose body is only pass".
A named error such as ClientError is a chosen failure. A handler that does
more than pass, or that raises again, does not match.
"""

from __future__ import annotations

import re

from .base import Example, FileRule, Hit

# A broad handler only. A named error such as ClientError is a chosen failure, not this class.
_EXCEPT = re.compile(r"^(\s*)except(?:\s+(?:Exception|BaseException))?\s*:\s*(?:#.*)?$")
_EXCEPT_PASS = re.compile(r"^(\s*)except(?:\s+(?:Exception|BaseException))?\s*:\s*pass\s*(?:#.*)?$")
_PASS = re.compile(r"^\s*pass\s*(?:#.*)?$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


class SwallowedError(FileRule):
    def applies_to(self, path: str) -> bool:
        return path.endswith(".py")

    def check_file(self, path: str, text: str) -> list[Hit]:
        hits: list[Hit] = []
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if _EXCEPT_PASS.match(line):
                hits.append(Hit(self.id, path, index + 1, index + 1, "exception discarded"))
                continue
            header = _EXCEPT.match(line)
            if header is None:
                continue
            indent = _indent(line)
            nxt = index + 1
            while nxt < len(lines) and lines[nxt].strip().startswith("#"):
                nxt += 1
            if nxt >= len(lines) or not _PASS.match(lines[nxt]):
                continue
            after = nxt + 1
            while after < len(lines) and not lines[after].strip():
                after += 1
            next_line = lines[after] if after < len(lines) else ""
            if next_line.strip() and not next_line.strip().startswith("#") and _indent(next_line) > indent:
                continue
            hits.append(Hit(self.id, path, index + 1, index + 1, "exception discarded"))
        return hits


_MATCH = """\
def load(raw):
    try:
        return parse(raw)
    except Exception:
        pass
"""

_MATCH_INLINE = """\
def load(raw):
    try:
        return parse(raw)
    except: pass
"""

_RERAISE = """\
def load(raw):
    try:
        return parse(raw)
    except Exception:
        report()
        raise
"""

_HANDLE = """\
def load(raw):
    try:
        return parse(raw)
    except Exception:
        return None
"""

_SPECIFIC = """\
def sweep():
    try:
        remove()
    except ClientError:
        pass
"""

RULE = SwallowedError(
    id="whitehats.code.swallowed-error",
    title="Exception discarded",
    weakness="a broad error handler discards the error",
    cwe=("CWE-390",),
    severity="medium",
    must_match=(Example(_MATCH, "billing/parse.py"), Example(_MATCH_INLINE, "billing/parse.py")),
    must_not_match=(
        Example(_RERAISE, "billing/parse.py"),
        Example(_HANDLE, "billing/parse.py"),
        Example(_SPECIFIC, "tools/cleanup.py"),
    ),
)

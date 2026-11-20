"""A caller-supplied value is used as an object key without a sanitiser.

The class is "untrusted input reaches an object storage key". The storage
primitive itself (a function named get_object or put_object) is not the bug.
A key built by calling another function is not the bug either.
"""

from __future__ import annotations

import re

from .base import Example, FileRule, Hit

_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\(")
_ASSIGN = re.compile(r"^(\w+)\s*=\s*(\w+)\s*$")
_STORAGE = re.compile(r"\b(?:get_object|put_object|delete_object|download_file|upload_file)\s*\(")
_SKIP_FUNCS = {"get_object", "put_object", "delete_object", "download_file", "upload_file"}
_SKIP_PARAMS = {"key", "bucket", "self", "cls"}


def _params(header: str) -> set[str]:
    names: set[str] = set()
    for part in header.split(","):
        part = part.strip()
        if not part or part.startswith("*"):
            continue
        name = part.split(":", 1)[0].split("=", 1)[0].strip()
        if name.isidentifier():
            names.add(name)
    return names


def _arguments(line: str, call: re.Match[str]) -> list[str]:
    depth = 1
    buf: list[str] = []
    args: list[str] = []
    for char in line[call.end():]:
        if char == "(":
            depth += 1
            buf.append(char)
        elif char == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(buf).strip())
                break
            buf.append(char)
        elif char == "," and depth == 1:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
    return [arg for arg in args if arg]


def _bare(arg: str) -> str | None:
    named = re.match(r"(?:Key|key)\s*=\s*(\w+)$", arg.strip())
    if named:
        return named.group(1)
    if re.match(r"\w+$", arg.strip()):
        return arg.strip()
    return None


class StorageKey(FileRule):
    def applies_to(self, path: str) -> bool:
        return path.endswith(".py")

    def check_file(self, path: str, text: str) -> list[Hit]:
        hits: list[Hit] = []
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            match = _DEF.match(lines[index])
            if match is None:
                index += 1
                continue
            indent = len(match.group(1))
            name = match.group(2)
            signature = lines[index]
            while ":" not in signature.split("#", 1)[0] and index + 1 < len(lines):
                index += 1
                signature += "\n" + lines[index]
            header = signature.split("(", 1)[1].rsplit(")", 1)[0]
            index += 1
            body: list[tuple[int, str]] = []
            while index < len(lines):
                current = lines[index]
                if current.strip() and _DEF.match(current) and (len(current) - len(current.lstrip())) <= indent:
                    break
                body.append((index + 1, current))
                index += 1
            if name in _SKIP_FUNCS:
                continue
            origin = {param: param for param in _params(header)}
            for _, line in body:
                assigned = _ASSIGN.match(line.split("#", 1)[0].strip())
                if assigned and assigned.group(2) in origin:
                    origin[assigned.group(1)] = origin[assigned.group(2)]
            for number, line in body:
                code = line.split("#", 1)[0]
                for call in _STORAGE.finditer(code):
                    for arg in _arguments(code, call):
                        bare = _bare(arg)
                        source = origin.get(bare or "")
                        if source and source not in _SKIP_PARAMS:
                            hits.append(Hit(self.id, path, number, number, "caller value used as an object key"))
                            break
        return hits


_MATCH = """\
def fetch(label: str) -> bytes:
    key = label
    return store.get_object(bucket, key)
"""

_MATCH_DIRECT = """\
def fetch(label: str) -> bytes:
    return store.get_object(bucket, label)
"""

_SANITISED = """\
def fetch(label: str) -> bytes:
    key = object_key(label)
    return store.get_object(bucket, key)
"""

_PRIMITIVE = """\
def get_object(bucket: str, key: str) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)
"""

_CONSTANT = """\
def fetch() -> bytes:
    key = "receipts/daily"
    return store.get_object(bucket, key)
"""

RULE = StorageKey(
    id="whitehats.code.storage-key",
    title="Caller value used as an object key",
    weakness="untrusted input is used as an object storage key",
    cwe=("CWE-22",),
    severity="high",
    must_match=(
        Example(_MATCH, "billing/objects.py"),
        Example(_MATCH_DIRECT, "billing/objects.py"),
    ),
    must_not_match=(
        Example(_SANITISED, "billing/objects.py"),
        Example(_PRIMITIVE, "storage/gateway.py"),
        Example(_CONSTANT, "billing/objects.py"),
    ),
)

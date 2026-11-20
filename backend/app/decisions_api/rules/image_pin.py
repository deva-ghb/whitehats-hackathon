"""A container base image is not pinned by digest.

The class is "a rebuild can pull a different image". A digest pin, a scratch
image, and a reference to an earlier build stage do not match.
"""

from __future__ import annotations

import re

from .base import Example, FileRule, Hit

_FROM = re.compile(r"(?i)^\s*FROM\s+(\S+)")


class UnpinnedBase(FileRule):
    def applies_to(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return name == "Dockerfile" or name.lower().endswith(".dockerfile")

    def check_file(self, path: str, text: str) -> list[Hit]:
        hits: list[Hit] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            match = _FROM.match(line)
            if match is None:
                continue
            image = match.group(1)
            if image.lower() == "scratch":
                continue
            if "@sha256:" in image.lower():
                continue
            if "/" not in image and ":" not in image:
                continue
            hits.append(Hit(self.id, path, number, number, "base image is not pinned by digest"))
        return hits


_DIGEST = "a" * 64
_MATCH = "FROM alpine:3.20\nUSER 1000:1000\n"
_PINNED = f"FROM alpine:3.20@sha256:{_DIGEST}\nUSER 1000:1000\n"
_SCRATCH = "FROM scratch\nCOPY app /app\n"
_STAGE = "FROM alpine:3.20@sha256:" + _DIGEST + "\nFROM builder\n"

RULE = UnpinnedBase(
    id="whitehats.image.unpinned-base",
    title="Base image is not pinned by digest",
    weakness="a container is built from a moving tag, so a rebuild can pull a different image",
    cwe=("CWE-1104",),
    severity="medium",
    must_match=(Example(_MATCH, "deploy/Dockerfile"),),
    must_not_match=(
        Example(_PINNED, "deploy/Dockerfile"),
        Example(_SCRATCH, "deploy/Dockerfile"),
        Example(_STAGE, "deploy/Dockerfile"),
    ),
)

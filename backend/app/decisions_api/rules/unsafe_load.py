"""Untrusted data passed to an unsafe deserialiser.

`yaml.load` without a safe loader, `yaml.unsafe_load`, and `pickle.loads` are
the class. `yaml.safe_load` and an explicit safe loader are not.
"""

from __future__ import annotations

import re

from .base import Example, FileRule, Hit

_YAML = re.compile(r"\byaml\.(?:load|load_all|unsafe_load)\s*\(")
_SAFE = re.compile(r"\b(?:SafeLoader|CSafeLoader)\b")
_PICKLE = re.compile(r"\bpickle\.loads\s*\(")


class UnsafeLoad(FileRule):
    def applies_to(self, path: str) -> bool:
        return path.endswith(".py")

    def check_file(self, path: str, text: str) -> list[Hit]:
        hits: list[Hit] = []
        for number, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]
            yaml_unsafe = _YAML.search(code) and not _SAFE.search(code)
            if yaml_unsafe or _PICKLE.search(code):
                hits.append(Hit(self.id, path, number, number, "unsafe deserialiser"))
        return hits


_YAML_MATCH = "import yaml\n\ndef read_settings(raw: str) -> dict:\n    return yaml." + "load(raw)\n"
_PICKLE_MATCH = "import pickle\n\ndef read_blob(raw: bytes) -> object:\n    return pickle.loads(raw)\n"
_SAFE_CALL = "import yaml\n\ndef read_settings(raw: str) -> dict:\n    return yaml.safe_load(raw)\n"
_SAFE_LOADER = (
    "import yaml\n\ndef read_settings(raw: str) -> dict:\n"
    "    return yaml.load(raw, Loader=yaml.SafeLoader)\n"
)

RULE = UnsafeLoad(
    id="whitehats.code.unsafe-deserialize",
    title="Unsafe deserialiser",
    weakness="untrusted data is passed to an unsafe deserialiser",
    cwe=("CWE-502",),
    severity="high",
    must_match=(
        Example(_YAML_MATCH, "billing/settings.py"),
        Example(_PICKLE_MATCH, "billing/blobs.py"),
    ),
    must_not_match=(
        Example(_SAFE_CALL, "billing/settings.py"),
        Example(_SAFE_LOADER, "billing/settings.py"),
    ),
)

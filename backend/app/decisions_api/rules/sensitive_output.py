"""A secret or a whole personal record is written to output.

The class is "a credential or a whole record is written to a log or to stdout".
A message that merely mentions the word secret, and a log of an id, do not match.
"""

from __future__ import annotations

import re

from .base import Example, FileRule, Hit

_SINK = re.compile(
    r"\b(?:print|logging\.(?:debug|info|warning|warn|error|exception|critical)|logger\.\w+)\s*\("
)
_INTERPOLATION = re.compile(r"\{(\w+)")
_SECRET_NAME = re.compile(r"(?i)authorization|password|secret|api_key|access_token|credential")
_RECORD = re.compile(r"(?i)^(?:customer|user|account|principal|profile)$")
_ENVIRON = re.compile(
    r"""(?:os\.environ(?:\.get)?\s*\(|getenv\s*\()\s*['"]([^'"]+)['"]"""
)


def _severity(secret: bool) -> str:
    return "high" if secret else "medium"


class SensitiveOutput(FileRule):
    def applies_to(self, path: str) -> bool:
        return path.endswith(".py")

    def check_file(self, path: str, text: str) -> list[Hit]:
        hits: list[Hit] = []
        for number, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]
            if not _SINK.search(code):
                continue
            names = _INTERPOLATION.findall(code)
            names.extend(_ENVIRON.findall(code))
            secret = any(_SECRET_NAME.search(name) for name in names)
            record = any(_RECORD.match(name) for name in names)
            if secret or record:
                hits.append(Hit(
                    self.id, path, number, number,
                    "credential or whole record written to output",
                    severity=_severity(secret),
                ))
        return hits


_MATCH_SECRET = (
    'def create(account):\n'
    '    print(f"login for {account} authorization={os.environ.get(\'REQUEST_AUTHORIZATION\')}")\n'
)
_MATCH_RECORD = (
    "def create(account):\n"
    "    logger.info(f\"loaded {account}\")\n"
)
_PROSE = 'print("history contains a secret; see the format note")\n'
_ID_ONLY = 'print(f"created {customer_id}")\n'
_COMMENT = "# never print the authorization header\nvalue = read_parameter()\n"

RULE = SensitiveOutput(
    id="whitehats.code.sensitive-output",
    title="Credential or record written to output",
    weakness="a credential or a whole personal record is written to a log or to stdout",
    cwe=("CWE-532",),
    severity="medium",
    must_match=(
        Example(_MATCH_SECRET, "billing/orders.py"),
        Example(_MATCH_RECORD, "billing/orders.py"),
    ),
    must_not_match=(
        Example(_PROSE, "tools/scan.py"),
        Example(_ID_ONLY, "billing/orders.py"),
        Example(_COMMENT, "billing/orders.py"),
    ),
)

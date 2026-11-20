"""A payment provider live key written into a file.

The proving ground's provider issues keys as `pgsk_live_` followed by random
letters and digits (docs/secret-format.md there). A value with the prefix and a
random-looking tail is a planted credential; a word-only tail is a documented
placeholder. Plain string tests, no pattern language.
"""

from __future__ import annotations

from .base import Example, FileRule, Hit

PREFIX = "pgsk_" + "live_"  # split so this file is not itself a hit
MIN_TAIL = 16  # shorter tails are too weak to be issued keys


def _key_tail(text: str, start: int) -> str:
    end = start
    while end < len(text) and text[end].isascii() and text[end].isalnum():
        end += 1
    return text[start:end]


def _looks_random(tail: str) -> bool:
    """Issued keys mix letters and digits; placeholders are words."""
    return any(c.isdigit() for c in tail) and any(c.isalpha() for c in tail)


class ProviderLiveKey(FileRule):
    def applies_to(self, path: str) -> bool:
        return True

    def check_file(self, path: str, text: str) -> list[Hit]:
        hits: list[Hit] = []
        for number, line in enumerate(text.splitlines(), start=1):
            at = line.find(PREFIX)
            while at != -1:
                tail = _key_tail(line, at + len(PREFIX))
                if len(tail) >= MIN_TAIL and _looks_random(tail):
                    hits.append(Hit(self.id, path, number, number, "provider live key in the file"))
                    break
                at = line.find(PREFIX, at + 1)
        return hits


RULE = ProviderLiveKey(
    id="whitehats.secrets.provider-live-key",
    title="Payment provider live key in source",
    weakness="a credential is written into the code instead of read from a secret store",
    cwe=("CWE-798",),
    severity="high",
    must_match=(
        Example('STRIPE_LIKE = "' + PREFIX + '9f3k2m8q7w1z0x4c5v6b"\n', "billing/config.py"),
        Example("export PAYMENTS_TOKEN=" + PREFIX + "0a1b2c3d4e5f6a7b8c9d\n", "deploy/env.sh"),
        Example("key: " + PREFIX + "AbCdEf123456GhIjKl\n", "settings.yaml"),
    ),
    must_not_match=(
        Example("The key looks like " + PREFIX + " followed by random characters.\n", "README.md"),
        Example('DEFAULT = "' + PREFIX + 'notarealvaluejustwords"\n', "billing/config.py"),
        Example('SHORT = "' + PREFIX + 'ab12"\n', "billing/config.py"),
        Example('token = read_parameter("/payments/provider-key")\n', "billing/config.py"),
    ),
)

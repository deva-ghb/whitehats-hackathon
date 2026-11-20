"""Hide secret values before evidence goes to a model (R-051).

The model is told what kind of value sat there and how long it was, never the value: it can still tell
a planted live key from a placeholder, because a placeholder ("changeme", "your-key-here") is a word and
stays visible. Plain string tests, no pattern language.

A word is hidden when it
- starts with a known credential prefix (a cloud access key id, a provider live key, a GitHub or Slack
  token, a private key header), or
- is the value given to a name that says it holds a secret (key, secret, token, password, ...) and is
  not an obvious placeholder, or
- is long and random-looking: letters and digits mixed, with high entropy per character.
"""

from __future__ import annotations

import math
from collections import Counter

from .chunk import Chunk, Line

# Credential prefixes and the kind each one names. Prefixes are not secret; they say what the value was.
PREFIXES = (
    ("pgsk_" + "live_", "provider live key"),
    ("AKIA", "cloud access key id"),
    ("ASIA", "cloud access key id"),
    ("ghp_", "GitHub token"),
    ("gho_", "GitHub token"),
    ("ghs_", "GitHub token"),
    ("github_pat_", "GitHub token"),
    ("xoxb-", "Slack token"),
    ("xoxp-", "Slack token"),
    ("sk-", "API key"),
    ("-----BEGIN", "private key"),
)
SECRET_NAMES = ("key", "secret", "token", "password", "passwd", "pwd", "credential", "auth", "private")
PLACEHOLDERS = ("change", "example", "placeholder", "your", "xxx", "dummy", "fake", "test", "sample", "redacted", "todo")
# Characters that end a word: a secret value is letters, digits and a few joiners (- _ / + .). "=" ends a word too,
# so `NAME=value` splits; base64 padding left visible says nothing.
BREAKS = frozenset(" \t\"'`,;:=()[]{}<>|\\!?&*#@$%^~")
MIN_RANDOM = 20  # a random-looking word shorter than this is more likely an id or a hash prefix
MIN_ENTROPY = 3.5  # bits per character; English words and identifiers sit well below
MIN_NAMED = 8  # a value given to a secret name is hidden from this length on


def _words(text: str) -> list[tuple[int, int]]:
    """(start, end) of each run of characters that are not word breaks."""
    spans, start = [], None
    for at, char in enumerate(text):
        if char in BREAKS:
            if start is not None:
                spans.append((start, at))
                start = None
        elif start is None:
            start = at
    if start is not None:
        spans.append((start, len(text)))
    return spans


def _entropy(word: str) -> float:
    counts = Counter(word)
    return -sum(n / len(word) * math.log2(n / len(word)) for n in counts.values())


def _random_looking(word: str) -> bool:
    core = word.strip("-_/.=+")
    return (len(core) >= MIN_RANDOM and any(c.isdigit() for c in core) and any(c.isalpha() for c in core)
            and _entropy(core) >= MIN_ENTROPY)


def _placeholder(word: str) -> bool:
    lowered = word.lower()
    return any(mark in lowered for mark in PLACEHOLDERS) or len(set(lowered)) <= 2


def _named_secret(text: str, start: int) -> bool:
    """The word at `start` is the value of an assignment or argument whose name says secret."""
    before = text[:start].rstrip(" \t\"'`")
    if not before or before[-1] not in "=:":
        return False
    name = before[:-1].rstrip(" \t\"'`:=")
    at = len(name)
    while at > 0 and (name[at - 1].isalnum() or name[at - 1] in "_-."):
        at -= 1
    lowered = name[at:].lower()
    return any(part in lowered for part in SECRET_NAMES)


def _a_value(text: str, start: int, end: int) -> bool:
    """A literal, not code: a quoted string, or the bare last word of the line that is not a dotted name
    (`os.environ[...]`, `settings.TOKEN` and `get_key()` are code the model must see)."""
    if start > 0 and text[start - 1] in "\"'`":
        return True
    return not text[end:].strip() and "." not in text[start:end]


def _kind(text: str, start: int, end: int) -> str | None:
    word = text[start:end]
    for prefix, kind in PREFIXES:
        if word.startswith(prefix) and len(word) > len(prefix) + 3:
            return kind
    if _placeholder(word):
        return None
    if len(word) >= MIN_NAMED and _named_secret(text, start) and _a_value(text, start, end):
        return "secret value"
    if _random_looking(word):
        return "random-looking value"
    return None


def mask_text(text: str) -> str:
    """The line with each secret-looking word replaced by `[hidden <kind>, <n> characters]`."""
    out, last = [], 0
    for start, end in _words(text):
        word = text[start:end]
        kind = _kind(text, start, end)
        if kind is None:
            continue
        out.append(text[last:start])
        out.append(f"[hidden {kind}, {len(word)} characters]")
        last = end
    out.append(text[last:])
    return "".join(out)


def _lines(lines: list[Line]) -> list[Line]:
    return [Line(line.number, mask_text(line.text)) for line in lines]


def mask_chunk(chunk: Chunk) -> Chunk:
    """The same evidence with every secret-looking value hidden, on every line the model would see."""
    return Chunk(chunk.path, mask_text(chunk.resource), chunk.start_line, chunk.end_line, _lines(chunk.flagged),
                 _lines(chunk.before), _lines(chunk.after), _lines(chunk.removed))

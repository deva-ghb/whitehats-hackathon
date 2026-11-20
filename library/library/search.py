"""Search the node library: names, aliases, vendors, families, common misspellings and pasted resource ids.

Pure: build an Index from load_library() once, then call search() as often as needed.
A match scores up to 100: a pasted resource id that names a type scores 100, and text scores the best of
a whole-phrase match, a type-ahead prefix, and every word found somewhere in the type. Context then adds
or takes away a few points.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .resource_ids import recognise

# How much a match in each part of a type counts.
FIELD_WEIGHTS = {"name": 1.0, "alias": 1.0, "id": 0.9, "vendor": 0.7, "family": 0.5}
EXACT, PREFIX, FUZZY_PHRASE, ALL_WORDS, SOME_WORDS = 100, 85, 75, 65, 35
WORD_EXACT, WORD_PREFIX, WORD_FUZZY = 1.0, 0.8, 0.7
GENERIC_FACTOR = 0.5  # a Generic type is a placeholder; it only leads when nothing better matches
CLOUD_BOOST, OTHER_CLOUD_PENALTY, PLACEMENT_BOOST = 12, 5, 10
CLOUDS = {"Amazon Web Services": "aws", "Microsoft": "azure", "Google Cloud": "gcp"}
# Inside these boundaries, people place network, compute and service nodes.
NETWORK_BOUNDARIES = {"boundary.vpc", "boundary.subnet", "boundary.eks", "boundary.aks"}
PLACED_INSIDE_NETWORKS = {"network", "compute", "services", "data", "messaging"}
FACETS = ("family", "cloud", "vendor", "importable", "whitehat_mode")


@dataclass(frozen=True)
class Context:
    """Where the person is searching from. Every part is optional."""

    cloud: str | None = None  # aws | azure | gcp: the canvas's cloud
    inside: str | None = None  # the type id of the group being searched in
    dragging_from: str | None = None  # the type id of the node an edge is being drawn from


@dataclass(frozen=True)
class Hit:
    type_id: str
    display_name: str
    family: str
    score: float
    matched: str  # what matched, in words


@dataclass(frozen=True)
class Results:
    hits: list[Hit]
    facets: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Entry:
    type_doc: dict
    phrases: tuple[tuple[str, str, float, str], ...]  # (field, compact text, weight, text as written)
    words: tuple[tuple[str, float], ...]  # every word in every phrase, with its field weight
    clouds: frozenset[str]

    @property
    def is_generic(self) -> bool:
        return bool(self.type_doc.get("generic"))


class Index:
    def __init__(self, library: Mapping[str, dict]):
        self._entries = {type_id: _entry(doc) for type_id, doc in library.items()}

    def search(self, query: str, context: Context | None = None, filters: Mapping[str, str] | None = None,
               limit: int = 10) -> Results:
        context = context or Context()
        scored = self._resource_hits(query) or self._text_hits(query)
        boosted = [(self._boost(type_id, score, context), type_id, matched) for type_id, score, matched in scored]
        kept = [(s, t, m) for s, t, m in boosted if s > 0 and _passes(self._entries[t], filters)]
        kept.sort(key=lambda item: (-item[0], self._entries[item[1]].is_generic, item[1]))
        hits = [self._hit(type_id, score, matched) for score, type_id, matched in kept]
        return Results(hits[:limit], _facets(self._entries[h.type_id] for h in hits))

    def _resource_hits(self, query: str) -> list[tuple[str, float, str]]:
        found = recognise(query)
        if found is None:
            return []
        return [(t, EXACT, found.kind) for t in found.type_ids if t in self._entries]

    def _text_hits(self, query: str) -> list[tuple[str, float, str]]:
        words = _words(query)
        if not words:
            return []
        wants_generic = bool({"generic", "other"} & set(words))
        hits = []
        for type_id, entry in self._entries.items():
            score, matched = _score(entry, words)
            if entry.is_generic and not wants_generic:
                score *= GENERIC_FACTOR
            if score:
                hits.append((type_id, score, matched))
        return hits

    def _boost(self, type_id: str, score: float, context: Context) -> float:
        if not score:
            return 0
        entry = self._entries[type_id]
        if context.cloud:
            if context.cloud in entry.clouds:
                score += CLOUD_BOOST
            elif entry.clouds:
                score -= OTHER_CLOUD_PENALTY
        if context.inside in NETWORK_BOUNDARIES and entry.type_doc["family"] in PLACED_INSIDE_NETWORKS:
            score += PLACEMENT_BOOST
        if context.dragging_from in self._entries and _can_connect(self._entries[context.dragging_from], entry):
            score += PLACEMENT_BOOST
        return score

    def _hit(self, type_id: str, score: float, matched: str) -> Hit:
        doc = self._entries[type_id].type_doc
        return Hit(type_id, doc["display_name"], doc["family"], round(score, 1), matched)


# --------------------------------------------------------------------------- scoring


def _score(entry: _Entry, words: list[str]) -> tuple[float, str]:
    """The best of: the whole query as one phrase, and every word somewhere in the type."""
    query = "".join(words)
    best, matched = 0.0, ""
    for field_name, phrase, weight, written in entry.phrases:
        score = _phrase_score(query, phrase) * weight
        if score > best:
            best, matched = score, f"{field_name} {written}"
    found = [max((_word_score(w, word) * weight for word, weight in entry.words), default=0) for w in words]
    coverage = sum(found) / len(words)
    if all(found):
        words_score = ALL_WORDS * coverage
    elif coverage >= 0.5:
        words_score = SOME_WORDS * coverage
    else:
        words_score = 0
    if words_score > best:
        best, matched = words_score, "words in its name, aliases or vendor"
    return best, matched


def _phrase_score(query: str, phrase: str) -> float:
    if query == phrase:
        return EXACT
    if len(query) >= 2 and phrase.startswith(query):
        return PREFIX
    if _close(query, phrase):
        return FUZZY_PHRASE
    return 0


def _word_score(word: str, candidate: str) -> float:
    if word == candidate:
        return WORD_EXACT
    if len(word) >= 3 and candidate.startswith(word):
        return WORD_PREFIX
    return WORD_FUZZY if _close(word, candidate) else 0


def _close(a: str, b: str) -> bool:
    """A common typing mistake apart: one edit in a word of 4 to 7 letters, two in a longer one."""
    allowed = 2 if min(len(a), len(b)) >= 8 else 1 if min(len(a), len(b)) >= 4 else 0
    return allowed > 0 and abs(len(a) - len(b)) <= allowed and _edits(a, b, allowed) <= allowed


def _edits(a: str, b: str, cap: int) -> int:
    """Insertions, deletions, substitutions and swapped neighbours to turn a into b, stopping above cap."""
    previous2: list[int] = []
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > cap:
            return cap + 1
        previous2, previous = previous, current
    return previous[-1]


def _can_connect(source: _Entry, target: _Entry) -> bool:
    outgoing = set(source.type_doc["edges"].get("allowed_out") or [])
    incoming = set(target.type_doc["edges"].get("allowed_in") or [])
    return bool((outgoing & incoming) - {"depends_on"})  # every type takes depends_on, so it says nothing


# --------------------------------------------------------------------------- building the index


def _words(text: str) -> list[str]:
    """Lower-case words of letters and digits; everything else separates them."""
    words, current = [], []
    for ch in text.lower():
        if ch.isalnum():
            current.append(ch)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def _entry(doc: dict) -> _Entry:
    fields = [("name", doc["display_name"]), ("id", doc["id"].split(".", 1)[-1]),
              ("family", doc["family"].replace("_", " "))]
    fields += [("alias", alias) for alias in doc.get("aliases") or []]
    fields += [("vendor", vendor) for vendor in doc.get("vendors") or []]
    phrases = tuple((name, "".join(_words(text)), FIELD_WEIGHTS[name], text) for name, text in fields)
    words = tuple((word, FIELD_WEIGHTS[name]) for name, text in fields for word in _words(text))
    clouds = frozenset(CLOUDS[v] for v in doc.get("vendors") or [] if v in CLOUDS)
    return _Entry(doc, phrases, words, clouds)


# --------------------------------------------------------------------------- facets and filters


def _facet_values(entry: _Entry) -> dict[str, Iterable[str]]:
    doc = entry.type_doc
    return {
        "family": [doc["family"]],
        "cloud": sorted(entry.clouds) or ["none"],
        "vendor": doc.get("vendors") or ["none"],
        "importable": ["yes" if doc.get("importers") else "no"],
        "whitehat_mode": ["yes" if doc.get("whitehat_modes") else "no"],
    }


def _facets(entries: Iterable[_Entry]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter] = {name: Counter() for name in FACETS}
    for entry in entries:
        for name, values in _facet_values(entry).items():
            counts[name].update(values)
    return {name: dict(sorted(counter.items())) for name, counter in counts.items()}


def _passes(entry: _Entry, filters: Mapping[str, str] | None) -> bool:
    if not filters:
        return True
    values = _facet_values(entry)
    return all(value in values[name] for name, value in filters.items() if name in values)


def main(argv: list[str] | None = None) -> int:
    """`python -m library.search <query> [--cloud aws|azure|gcp]`: print the ranked types."""
    import argparse

    from .resolve import load_library

    parser = argparse.ArgumentParser(description="Search the node library.")
    parser.add_argument("query")
    parser.add_argument("--cloud", choices=sorted(set(CLOUDS.values())))
    args = parser.parse_args(argv)
    for hit in Index(load_library()).search(args.query, Context(cloud=args.cloud)).hits:
        print(f"{hit.score:6.1f}  {hit.type_id:32} {hit.display_name}  ({hit.matched})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

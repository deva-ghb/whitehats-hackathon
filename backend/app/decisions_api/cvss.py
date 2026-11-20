"""CVSS v3.0 and v3.1 base score, used when an advisory gives a vector and no number."""

from __future__ import annotations

import math

_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}
_AC = {"L": 0.77, "H": 0.44}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}
_PR = {
    "U": {"N": 0.85, "L": 0.62, "H": 0.27},
    "C": {"N": 0.85, "L": 0.68, "H": 0.50},
}


def _roundup(value: float) -> float:
    """The CVSS round-up: the next tenth, unless the value is already on a tenth."""
    scaled = round(value * 100000)
    if scaled % 10000 == 0:
        return scaled / 100000.0
    return (math.floor(scaled / 10000) + 1) / 10.0


def base_score(vector: str) -> float | None:
    parts = vector.strip().split("/")
    if not parts or not parts[0].startswith("CVSS:3."):
        return None
    metrics: dict[str, str] = {}
    for part in parts[1:]:
        key, separator, value = part.partition(":")
        if separator:
            metrics[key] = value
    try:
        scope = metrics["S"]
        iss = 1 - (
            (1 - _CIA[metrics["C"]]) * (1 - _CIA[metrics["I"]]) * (1 - _CIA[metrics["A"]])
        )
        exploitability = 8.22 * _AV[metrics["AV"]] * _AC[metrics["AC"]] * _PR[scope][metrics["PR"]] * _UI[metrics["UI"]]
    except KeyError:
        return None
    if scope == "U":
        impact = 6.42 * iss
    elif scope == "C":
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        return None
    if impact <= 0:
        return 0.0
    raw = impact + exploitability if scope == "U" else 1.08 * (impact + exploitability)
    return _roundup(min(raw, 10))

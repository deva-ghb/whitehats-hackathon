"""Every ATT&CK technique a WhiteHat tags a signal with is one MITRE still publishes."""

import ast
from pathlib import Path

from library.validate import attack_reference

WHITEHATS = Path(__file__).resolve().parents[1] / "app" / "whitehats"


def _looks_like_technique(text: str) -> bool:
    head, _, sub = text.partition(".")
    return len(head) == 5 and head[0] == "T" and head[1:].isdigit() and (not sub or (len(sub) == 3 and sub.isdigit()))


def _technique_ids() -> set[str]:
    found = set()
    for path in WHITEHATS.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _looks_like_technique(node.value):
                found.add(node.value)
    return found


def test_whitehat_signals_use_current_attack_techniques():
    techniques = attack_reference()["techniques"]
    ids = _technique_ids()
    assert "T1078.004" in ids
    stale = {tid: techniques.get(tid) for tid in sorted(ids) if tid not in techniques or techniques[tid]["retired"]}
    assert stale == {}

"""VS.6: the scorecard's own rules. A finding counts when it is within a few lines of what the scenario's patch added
or removed (S08 removes a line), and changed regions are counted the same way."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_proving_ground import LINE_SLACK, _changed_lines, _chunks, _near

from app.decisions_api.suspect import Evidence, Suspect

DIFF = (
    "diff --git a/shop/orders.py b/shop/orders.py\n--- a/shop/orders.py\n+++ b/shop/orders.py\n"
    "@@ -10,4 +10,3 @@\n context\n-    if order['customer_id'] != customer_id:\n context\n context\n"
    "@@ -40,2 +39,3 @@\n context\n+print(customer)\n context\n"
)


def _at(path: str, line: int) -> Suspect:
    return Suspect("whitehats", "r", "t", Evidence(path, line, line), "high")


def test_a_finding_counts_near_an_added_or_removed_line_and_not_elsewhere():
    changed = _changed_lines(DIFF)
    assert changed == {"shop/orders.py": {11, 40}}  # the removed owner check sat at 11; the print is 40
    assert _near(_at("shop/orders.py", 11 + LINE_SLACK), changed)
    assert _near(_at("shop/orders.py", 40), changed)
    assert not _near(_at("shop/orders.py", 25), changed)  # between them, too far from both
    assert not _near(_at("shop/products.py", 40), changed)  # another file


def test_changed_lines_far_apart_are_separate_regions():
    assert _chunks({11, 12, 14, 40}) == [(11, 14), (40, 40)]

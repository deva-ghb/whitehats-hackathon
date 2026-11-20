"""P6.4: the top sense point suggestions on the proving-ground twin match the weaknesses in the scenario catalogue.

The weaknesses, their modes and their places are data (scripts/sense_point_weaknesses.json), measured by
scripts/measure_sense_points.py. The analysis, the library and their tests never name a scenario.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import test_decisions_boundaries as boundaries
from library.resolve import load_library
from library.validate import library_root

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))

import measure_sense_points as measure

from app.services.sense_point_store import suggest

TWIN = _BACKEND.parents[0] / "e2e" / "screenshots" / "P2.5" / "proving-ground-by-hand.clone.json"
IDS = [f"S0{number}" for number in range(1, 10)]
RULES = [
    _BACKEND / "app" / "services" / "sense_points.py",
    _BACKEND / "app" / "services" / "sense_point_store.py",
    _BACKEND / "app" / "routers" / "sense_points.py",
    _BACKEND / "tests" / "test_sense_points.py",
    _BACKEND / "tests" / "test_sense_point_routes.py",
]


@pytest.fixture(scope="module")
def data() -> dict:
    return measure.load_data()


@pytest.fixture(scope="module")
def twin() -> dict:
    return json.loads(TWIN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report(twin, data) -> dict:
    return measure.measure(twin, data)


def _modes_the_library_knows() -> set[str]:
    schema = json.loads((library_root() / "schemas" / "node-type.schema.json").read_text(encoding="utf-8"))
    return set(schema["$defs"]["mode"]["enum"])


def _places(data: dict):
    for weakness in data["weaknesses"]:
        place = weakness["place"]
        yield weakness["id"], [place["node"]] if "node" in place else place["edge"]


def test_the_mapping_names_each_weakness_once_with_modes_and_a_place_in_the_library_s_words(data):
    assert [weakness["id"] for weakness in data["weaknesses"]] == IDS
    known, kinds = _modes_the_library_knows(), set(load_library())
    for weakness in data["weaknesses"]:
        assert weakness["catalogue_mode"] in data["mode_words"], weakness["id"]
        assert weakness["why_there"]
    for modes in (value for key, value in data["mode_words"].items() if key != "about"):
        assert modes and set(modes) <= known
    for _id, refs in _places(data):
        assert 1 <= len(refs) <= 2
        for ref in refs:
            assert ref["type"] in kinds and ref["names"], ref
    for ref in data["crown_jewels"]["places"]:
        assert ref["type"] in kinds and ref["names"], ref


def test_every_place_is_found_in_the_hand_built_twin_and_an_edge_place_is_a_real_connection(twin, data):
    edges = {(edge["from"], edge["to"]) for edge in twin["edges"]}
    for weakness_id, refs in _places(data):
        found = [measure.find(twin, ref) for ref in refs]
        assert all(found), (weakness_id, refs)
        if len(refs) == 2:
            first, second = found
            assert any((a, b) in edges or (b, a) in edges for a in first for b in second), weakness_id
    for ref in data["crown_jewels"]["places"]:
        assert measure.find(twin, ref), ref


def test_a_name_matches_whole_or_after_a_dash_or_a_slash_and_never_in_the_middle():
    ref = {"type": "data.s3", "names": ["receipts"]}
    node = {"type": "data.s3@1"}
    assert measure.matches({**node, "name": "receipts"}, ref)
    assert measure.matches({**node, "name": "whitehats-proving-ground-123-receipts"}, ref)
    assert measure.matches({**node, "name": "/aws/receipts"}, ref)
    assert not measure.matches({**node, "name": "receipts-archive"}, ref)
    assert not measure.matches({**node, "name": "unreceipts"}, ref)
    assert not measure.matches({"type": "data.dynamodb@1", "name": "receipts"}, ref)


def test_the_modes_written_here_are_the_ones_the_catalogue_states(data):
    # The catalogue's modes are pinned from proving-ground main with the scenario strings (R-057).
    modes = boundaries.pinned()["modes"]
    for weakness in data["weaknesses"]:
        assert modes[weakness["id"]] == weakness["catalogue_mode"], weakness["id"]


def _table(report: dict, data: dict) -> str:
    return measure.show(report, data)


def test_the_top_five_cover_at_least_four_fifths_of_the_weaknesses_and_the_whole_list_covers_all(report, data):
    judged = report["table"]["judged"]
    assert judged["n"] == 5
    assert judged["share"] >= measure.THRESHOLD, _table(report, data)
    assert report["table"]["all"]["covered"] == len(IDS), _table(report, data)


def test_a_covered_weakness_names_the_suggestion_and_where_it_sits(report):
    for row in report["table"]["judged"]["rows"].values():
        if row["covered"]:
            assert row["how"] in {"on it", "next to it"} and row["by"] and 1 <= row["rank"] <= 5


def test_the_measurement_reports_a_weakness_nothing_is_near_as_not_covered(twin, data):
    without = copy.deepcopy(twin)
    gone = {node["id"] for node in without["nodes"] if node["name"] == "whitehats-proving-ground repository"}
    assert gone
    without["nodes"] = [node for node in without["nodes"] if node["id"] not in gone]
    without["edges"] = [edge for edge in without["edges"] if not {edge["from"], edge["to"]} & gone]
    found = measure.measure(without, data)["table"]["judged"]
    assert found["share"] < measure.THRESHOLD
    assert not found["rows"]["S01"]["covered"]


def test_the_strict_figure_only_counts_the_first_mode_of_a_suggestion(report):
    wide, strict = report["table"]["judged"], report["table"]["judged"]["strict"]
    assert strict["covered"] <= wide["covered"]
    assert all(not strict["rows"][key]["covered"] or wide["rows"][key]["covered"] for key in IDS)


def test_the_same_twin_gives_the_same_list_in_any_order_of_its_parts(twin, data):
    first, _ = suggest(measure.marked(twin, data))
    shuffled = copy.deepcopy(twin)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    second, _ = suggest(measure.marked(shuffled, data))
    assert first == second
    assert first == suggest(measure.marked(twin, data))[0]


def test_the_measurement_adds_only_the_crown_jewel_marks_a_person_makes(twin, data):
    marked = measure.marked(twin, data)
    changed = [after["name"] for before, after in zip(twin["nodes"], marked["nodes"], strict=True) if before != after]
    assert sorted(changed) == ["api-token", "customers", "orders", "receipts", "webhook-signing-secret"]
    assert all(node["criticality"] == "crown-jewel" for node in marked["nodes"] if node["name"] in changed)


def test_with_no_crown_jewel_marked_the_analysis_has_no_target_and_suggests_nothing(twin):
    ranked, result = suggest(twin)
    assert ranked == [] and result["paths"] == []


def test_the_analysis_and_its_tests_name_no_scenario_and_no_string_that_only_a_scenario_patch_holds():
    blob = "\n".join(path.read_text(encoding="utf-8") for path in RULES)
    blob += "\n".join(
        path.read_text(encoding="utf-8")
        for folder in ("types", "data")
        for path in sorted((library_root() / folder).iterdir())
        if path.suffix in {".yaml", ".json"}
    )
    assert boundaries._SCENARIO_ID.search(blob) is None
    for name in boundaries._FILE_NAMES:
        assert name not in blob
    for needle in boundaries.pinned()["strings"]:
        assert needle not in blob, needle[:160]

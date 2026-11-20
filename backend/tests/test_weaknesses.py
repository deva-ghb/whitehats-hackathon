"""SC.1: package weaknesses per node, ranked by reach: on an attack path, reachable from an entry point, internal only."""

from __future__ import annotations

import random
from pathlib import Path

from test_knowledge import _record, _session

from app.knowledge import osv
from app.services.repo_manifests import read_projects
from app.services.weaknesses import for_clone

LOCKS = Path(__file__).resolve().parent / "fixtures" / "locks"


def _knowledge():
    db = _session()
    osv.store(db, "PyPI", _record("PYSEC-2026-0401", "2026-10-30T09:00:00Z", fixed="5.4", name="PyYAML"))
    db.commit()
    return db


def _graph() -> dict:
    """A public API in front of one function that reads the customers table; a second function it calls that reads
    nothing that matters; and a third function nothing reaches. All three use the same weak package."""
    weak = {"packages": ["PyPI:pyyaml==5.3.1"]}
    return {"nodes": [
        {"id": "api", "type": "edge.api-gateway@1", "name": "public API"},  # an entry point by its kind
        {"id": "fn_a", "type": "service.function@2", "name": "shop-api", "properties": weak},
        {"id": "fn_c", "type": "service.function@2", "name": "thumbnailer", "properties": weak},
        {"id": "fn_b", "type": "service.function@2", "name": "nightly-report", "properties": weak},
        {"id": "db", "type": "data.dynamodb@1", "name": "the customers table", "criticality": "crown-jewel"},
    ], "edges": [
        {"from": "api", "to": "fn_a", "type": "calls"},
        {"from": "api", "to": "fn_c", "type": "calls"},
        {"from": "fn_a", "to": "db", "type": "reads"},
    ], "groups": []}


def test_the_same_advisory_ranks_first_where_the_internet_reaches_it_and_last_where_nothing_does():
    result = for_clone(_knowledge(), _graph())
    assert [(row["node_name"], row["reach"]) for row in result["ranked"]] == [
        ("shop-api", 1), ("thumbnailer", 2), ("nightly-report", 3)]
    shop = result["nodes"]["fn_a"]
    assert shop["why"] == "On an attack path: public API, then shop-api, then the customers table."
    assert shop["route"] == "public API, then shop-api, then the customers table"
    assert result["nodes"]["fn_b"]["route"] == ""
    assert result["nodes"]["fn_c"]["why"].startswith("Reachable from an entry point: public API, then thumbnailer")
    assert result["nodes"]["fn_b"]["why"] == "Internal only: no entry point reaches it."
    [row] = shop["weaknesses"]
    assert (row["package"], row["version"], row["advisory"], row["fixed_versions"]) == ("pyyaml", "5.3.1", "PYSEC-2026-0401", ["5.4"])
    assert "api" not in result["nodes"] and "db" not in result["nodes"]  # nothing weak there: no entry at all
    assert result["attribution"].startswith("Vulnerability data from OSV.dev")


def test_the_same_clone_version_always_ranks_the_same_way():
    db = _knowledge()
    first = for_clone(db, _graph())
    for seed in range(5):
        shuffled = _graph()
        random.Random(seed).shuffle(shuffled["nodes"])
        random.Random(seed).shuffle(shuffled["edges"])
        assert for_clone(db, shuffled) == first


def test_a_version_past_the_fix_shows_no_weakness():
    graph = _graph()
    for node in graph["nodes"]:
        if node.get("properties", {}).get("packages"):
            node["properties"]["packages"] = ["PyPI:pyyaml==5.4"]
    assert for_clone(_knowledge(), graph) == {"nodes": {}, "ranked": [], "attribution": osv.ATTRIBUTION}


def _read(names: list[str]):
    texts = {name: (LOCKS / name).read_text(encoding="utf-8") for name in names}
    return read_projects(frozenset(texts), texts.get)


def test_a_node_lock_file_yields_its_packages_in_each_format():
    expected = ["npm:@types/node==18.19.3", "npm:lodash==4.17.15", "npm:minimist==1.2.0"]
    for lock in ("yarn.lock", "pnpm-lock.yaml"):
        [project], _ = _read(["package.json", lock])
        assert (project.lock_file, project.packages) == (lock, expected), lock


def test_the_route_ranks_a_clones_weaknesses_for_anyone_who_sees_the_project(client, engineer):
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import Advisory, AdvisoryAlias, AdvisoryPackage

    with SessionLocal() as db:
        osv.store(db, "PyPI", _record("PYSEC-2026-0402", "2026-10-30T09:00:00Z", fixed="5.4", name="PyYAML"))
        db.commit()
    made_id = None
    try:
        document = {"schema_version": 1, "clone": "sc1-route", "environment": "prod", "library_version": "2026.10",
                    "version": {"number": 1, "author": "t", "reason": "r"}, "access_paths": [], "credential_refs": [],
                    "groups": [], "edges": [{"from": "api", "to": "fn", "type": "calls"}],
                    "nodes": [{"id": "api", "type": "edge.api-gateway@1", "name": "public API", "environment": "prod"},
                              {"id": "fn", "type": "service.function@2", "name": "shop-api", "environment": "prod",
                               "properties": {"packages": ["PyPI:pyyaml==5.3.1"]}}]}
        made = client.post("/api/clones", headers=engineer, json={"reason": "r", "document": document})
        assert made.status_code == 200, made.text
        made_id = made.json()["id"]
        result = client.get(f"/api/clones/{made_id}/weaknesses", headers=engineer).json()
        assert result["version"] == 1 and result["nodes"]["fn"]["count"] == 1 and result["nodes"]["fn"]["reach"] == 2
    finally:
        with SessionLocal() as db:
            for model in (AdvisoryAlias, AdvisoryPackage, Advisory):
                db.execute(delete(model))
            db.commit()
        if made_id is not None:  # other tests read every clone: its latest version uses no package any more
            document["nodes"][1]["properties"] = {}
            client.post(f"/api/clones/{made_id}/versions", headers=engineer, json={"reason": "tidy", "document": document})


def test_records_of_one_weakness_are_one_row_under_the_most_severe_and_a_repeated_name_says_its_kind():
    db = _session()
    ghsa = _record("GHSA-aaaa-bbbb-cccc", "2026-10-30T09:00:00Z", fixed="5.4", name="PyYAML")
    ghsa["aliases"] = ["CVE-2020-14343", "PYSEC-2021-142"]
    ghsa["database_specific"] = {"severity": "CRITICAL"}
    pysec = _record("PYSEC-2021-142", "2026-10-30T09:00:00Z", fixed="5.4", name="pyyaml")
    pysec["aliases"] = ["CVE-2020-14343", "GHSA-aaaa-bbbb-cccc"]
    osv.store(db, "PyPI", ghsa)
    osv.store(db, "PyPI", pysec)
    db.commit()
    graph = _graph()
    graph["nodes"][0]["name"] = "shop-api"  # the gateway carries the function's name, as the cloud names it
    result = for_clone(db, graph)
    [row] = result["nodes"]["fn_a"]["weaknesses"]
    assert (row["advisory"], row["severity"], row["aliases"]) == ("GHSA-aaaa-bbbb-cccc", "critical", ["CVE-2020-14343", "PYSEC-2021-142"])
    assert result["nodes"]["fn_a"]["route"] == "shop-api (api-gateway), then shop-api (function), then the customers table"


def test_an_image_weakness_is_a_row_naming_the_image_ranked_by_reach_like_a_package():
    graph = _graph()
    base = "public.ecr.aws/lambda/python:3.9"
    graph["nodes"][2]["properties"] = {"base_image": base}  # thumbnailer: reachable, no path to a crown jewel
    scanned = {base: [{"package": "curl", "installed": "8.3.0", "fixed": "8.3.1, 8.4.0", "advisory": "CVE-2026-8924",
                       "severity": "critical", "title": "curl: a flaw"}]}
    result = for_clone(_knowledge(), graph, images=lambda image: scanned.get(image, []))
    rows = result["nodes"]["fn_c"]["weaknesses"]
    assert [(row["advisory"], row["image"], row["fixed_versions"]) for row in rows] == [
        ("CVE-2026-8924", base, ["8.3.1", "8.4.0"])]
    assert result["nodes"]["fn_c"]["reach"] == 2 and result["nodes"]["fn_c"]["highest"] == "critical"
    assert result["image_attribution"].startswith("Image weaknesses found by Trivy")
    assert [row["node_name"] for row in result["ranked"]][:2] == ["shop-api", "thumbnailer"]  # reach first, then severity

"""What an import would change, and what accepting part of it does to a clone."""

import json
from pathlib import Path

import pytest
from library.resolve import load_library, resolve_clone

from app.services import changesets
from app.services.terraform_state import import_state

STATE = Path(__file__).resolve().parent / "fixtures" / "terraform" / "shop.state.json"


@pytest.fixture(scope="module")
def library() -> dict[str, dict]:
    return load_library()


@pytest.fixture
def generic(library):
    return lambda ref: bool(library.get(ref.rsplit("@", 1)[0], {}).get("generic"))


def _declared(library):
    return lambda ref: {prop["key"] for prop in library[ref.rsplit("@", 1)[0]]["properties"]}


def _blank() -> dict:
    return {"schema_version": 1, "clone": "shop", "environment": "prod", "library_version": "2026.10",
            "version": {"number": 1, "author": "test", "reason": "blank"},
            "nodes": [], "edges": [], "groups": [], "access_paths": [], "credential_refs": []}


def _state() -> dict:
    return json.loads(STATE.read_text())


def _accept_all(items: list[dict], type_ref: str | None = None) -> list[dict]:
    return changesets.decide(items, [item["key"] for item in items], "accept", type_ref)


def _items(incoming, document, generic, **kwargs):
    return changesets.build(incoming, document, owners=("terraform",), is_generic=generic, **kwargs)


def _apply(document, items, library, generic):
    return changesets.apply_accepted(document, items, declared=_declared(library), is_generic=generic)


def test_a_first_import_adds_everything_and_each_change_waits_for_a_decision(generic):
    incoming = import_state(_state())
    items = _items(incoming, _blank(), generic)
    assert {item["action"] for item in items} == {"add"}
    assert len(items) == len(incoming["nodes"]) + len(incoming["groups"]) + len(incoming["edges"])
    assert {item["decision"] for item in items} == {"undecided"}
    assert [item["kind"] for item in items] == sorted((item["kind"] for item in items), key=("group", "node", "edge").index)


def test_importing_the_same_state_again_after_accepting_everything_is_empty(library, generic):
    incoming = import_state(_state())
    items = _accept_all(_items(incoming, _blank(), generic))
    document, report = _apply(_blank(), items, library, generic)
    assert report["added"] == len(items) and report["left_out_edges"] == 0
    assert _items(import_state(_state()), document, generic) == []
    resolved, problems = resolve_clone(document, library)
    assert problems == [] and resolved


def test_an_import_never_proposes_removing_what_a_person_drew(generic):
    document = _blank()
    document["nodes"] = [
        {"id": "n_hand", "type": "service.backend@1", "name": "billing", "environment": "prod",
         "provenance": {"kind": "drawn", "source": "Clone Studio"}},
        {"id": "n_old", "type": "data.s3@1", "name": "old bucket", "environment": "prod",
         "provenance": {"kind": "imported", "source": "terraform:aws_s3_bucket.old"}},
    ]
    items = _items({"nodes": [], "groups": [], "edges": []}, document, generic)
    assert [(item["key"], item["action"]) for item in items] == [("node:n_old", "remove")]


def test_an_update_keeps_what_a_person_added(generic):
    incoming = import_state(_state())
    document = _blank()
    document["groups"], document["nodes"] = incoming["groups"], [dict(node) for node in incoming["nodes"]]
    bucket = next(node for node in document["nodes"] if node["name"] == "receipts")
    bucket["criticality"] = "crown-jewel"
    bucket["properties"] = {**bucket["properties"], "encryption": "sse_kms", "region": "us-east-1"}
    items = _items(incoming, document, generic)
    changed = [item for item in items if item["action"] == "update"]
    assert [item["key"] for item in changed] == [f"node:{bucket['id']}"]
    assert changed[0]["changes"] == [{"field": "region", "before": "us-east-1", "after": "ap-southeast-2"}]
    after = changesets.merge(bucket, changed[0]["item"], generic)
    assert after["criticality"] == "crown-jewel" and after["properties"]["encryption"] == "sse_kms"


def test_a_type_a_person_chose_for_a_held_item_survives_the_next_import(generic):
    held = {"id": "n_x", "type": "generic.services@1", "name": "clicks", "environment": "prod",
            "properties": {"what_is_this": "Terraform resource aws_kinesis_stream"},
            "provenance": {"kind": "imported", "source": "terraform:aws_kinesis_stream.clicks"}}
    chosen = {**held, "type": "messaging.sqs@1", "properties": {}}
    kept = changesets.merge(chosen, held, generic)
    assert kept["type"] == "messaging.sqs@1" and "what_is_this" not in kept.get("properties", {})
    assert changesets.merge(chosen, held, generic) == chosen  # nothing to propose
    assert changesets.merge(held, {**held, "type": "messaging.sqs@1"}, generic)["type"] == "messaging.sqs@1"


def test_a_removed_resource_takes_its_edges_with_it_without_asking_twice(library, generic):
    incoming = import_state(_state())
    document, _ = _apply(_blank(), _accept_all(_items(incoming, _blank(), generic)), library, generic)
    shrunk = {key: [item for item in incoming[key] if item["name"] != "shop-api"] for key in ("nodes", "groups")}
    shrunk["edges"] = incoming["edges"]
    items = _items(shrunk, document, generic)
    assert [(item["kind"], item["action"]) for item in items] == [("node", "remove")]
    after, report = _apply(document, _accept_all(items), library, generic)
    assert report["removed"] == 1 and report["left_out_edges"] > 0
    assert all(edge["from"] != "n_aws_lambda_function_shop" for edge in after["edges"])
    assert resolve_clone(after, library)[1] == []


def test_a_rejected_change_is_not_proposed_again_unless_it_changes(generic):
    incoming = import_state(_state())
    items = _items(incoming, _blank(), generic)
    rejected = {item["key"]: item["item"] for item in items if item["kind"] == "node"}
    again = _items(incoming, _blank(), generic, rejected=rejected)
    assert all(item["kind"] != "node" for item in again)
    bucket = next(node for node in incoming["nodes"] if node["name"] == "receipts")
    changed = {**incoming, "nodes": [{**bucket, "name": "receipts-2"} if node is bucket else node for node in incoming["nodes"]]}
    proposed = [item["key"] for item in _items(changed, _blank(), generic, rejected=rejected) if item["kind"] == "node"]
    assert proposed == [f"node:{bucket['id']}"]


def test_an_edge_whose_end_was_not_accepted_is_left_out_not_left_dangling(library, generic):
    incoming = import_state(_state())
    items = _items(incoming, _blank(), generic)
    kept = [item for item in items if item["key"] != "node:n_aws_sqs_queue_jobs"]
    document, report = _apply(_blank(), _accept_all(kept), library, generic)
    assert report["left_out_edges"] == 1
    assert not [edge for edge in document["edges"] if "n_aws_sqs_queue_jobs" in (edge["from"], edge["to"])]
    assert resolve_clone(document, library)[1] == []


def test_a_group_whose_parent_was_not_accepted_loses_the_parent_not_the_group(library, generic):
    incoming = import_state(_state())
    items = _items(incoming, _blank(), generic)
    parentless = next(item for item in items if item["kind"] == "group" and item["item"].get("parent"))
    kept = [item for item in items if item["key"] != f"group:{parentless['item']['parent']}"]
    document, _ = _apply(_blank(), _accept_all(kept), library, generic)
    group = next(entry for entry in document["groups"] if entry["id"] == parentless["item"]["id"])
    assert "parent" not in group


def _held_state() -> dict:
    state = _state()
    state["resources"].append({"mode": "managed", "type": "aws_kinesis_stream", "name": "clicks",
                               "instances": [{"attributes": {"id": "clicks", "name": "clicks"}}]})
    return state


def test_a_generic_item_is_held_until_it_has_a_real_type(library, generic):
    items = _items(import_state(_held_state()), _blank(), generic)
    held = [item for item in items if item["held"]]
    assert [item["item"]["name"] for item in held] == ["clicks"]
    with pytest.raises(ValueError, match="clicks has no library type yet"):
        changesets.decide(items, [held[0]["key"]], "accept")
    chosen = changesets.decide(items, [held[0]["key"]], "accept", "messaging.sqs@1")
    document, _ = _apply(_blank(), chosen, library, generic)
    node = next(entry for entry in document["nodes"] if entry["name"] == "clicks")
    assert node["type"] == "messaging.sqs@1"
    assert "what_is_this" not in node.get("properties", {})
    assert node["provenance"]["kind"] == "imported"
    assert resolve_clone(document, library)[1] == []


def test_a_held_item_left_undecided_never_reaches_the_clone(library, generic):
    items = _items(import_state(_held_state()), _blank(), generic)
    document, _ = _apply(_blank(), _accept_all([item for item in items if not item["held"]]), library, generic)
    assert "clicks" not in [node["name"] for node in document["nodes"]]
    assert all(not generic(node["type"]) for node in document["nodes"])


def test_a_decision_on_a_change_that_is_not_there_is_refused(generic):
    items = _items(import_state(_state()), _blank(), generic)
    with pytest.raises(KeyError):
        changesets.decide(items, ["node:n_nothing"], "accept")
    with pytest.raises(ValueError, match="A decision is one of"):
        changesets.decide(items, [items[0]["key"]], "maybe")


def test_provenance_counts_nodes_groups_and_edges_and_names_what_is_not_recorded():
    document = _blank()
    document["nodes"] = [{"provenance": {"kind": "imported", "source": "a"}}, {"provenance": {"kind": "drawn", "source": "b"}}]
    document["groups"] = [{"provenance": {"kind": "discovered", "source": "c"}}]
    document["edges"] = [{"from": "a", "to": "b", "type": "calls"}]
    assert changesets.provenance_counts(document) == {"imported": 1, "discovered": 1, "drawn": 1, "not recorded": 1}


def test_a_discovered_resource_the_clone_already_holds_by_arn_is_not_added_twice(library, generic):
    document, _ = _apply(_blank(), _accept_all(_items(import_state(_state()), _blank(), generic)), library, generic)
    queue = next(node for node in document["nodes"] if node["name"] == "payment-jobs")
    found = {
        "groups": [],
        "nodes": [
            {"id": "n_aws_jobs", "type": "messaging.sqs@1", "name": "payment-jobs", "environment": "prod",
             "properties": {"arn": queue["properties"]["arn"]}, "provenance": {"kind": "imported", "source": "aws"}},
            {"id": "n_aws_new", "type": "data.s3@1", "name": "scans", "environment": "prod",
             "properties": {"arn": "arn:aws:s3:::scans"}, "provenance": {"kind": "imported", "source": "aws"}},
        ],
        "edges": [{"from": "n_aws_new", "to": "n_aws_jobs", "type": "publishes_to"},
                  {"from": "n_aws_jobs", "to": "n_aws_jobs", "type": "depends_on"}],
    }
    aligned = changesets.align(found, document)
    assert [node["id"] for node in aligned["nodes"]] == ["n_aws_new"]
    assert aligned["edges"] == [{"from": "n_aws_new", "to": queue["id"], "type": "publishes_to"}]
    items = changesets.build(aligned, document, owners=("aws",), is_generic=generic)
    assert [item["key"] for item in items] == ["node:n_aws_new", f"edge:n_aws_new|publishes_to|{queue['id']}"]
    assert changesets.align({"nodes": [], "groups": [], "edges": []}, document) == {"nodes": [], "groups": [], "edges": []}


# What the Terraform state holds for each of these, and what live discovery would call the same thing.
# The ARNs are read from the recorded state itself, so the test does not repeat the importer's own work.
ARN_TYPES = {
    "aws_lambda_function": "service.function@2",
    "aws_s3_bucket": "data.s3@1",
    "aws_apigatewayv2_api": "edge.api-gateway@1",
    "aws_cloudwatch_log_group": "observability.cloudwatch@1",
}


def _state_arns() -> dict[str, str]:
    found = {}
    for resource in _state()["resources"]:
        if resource["type"] in ARN_TYPES:
            found[resource["instances"][0]["attributes"]["arn"]] = ARN_TYPES[resource["type"]]
    return found


def _found_elsewhere(arns: dict[str, str], source: str) -> dict:
    """The same resources as a connection or a scan names them: another id, another name, the real ARN."""
    nodes = [{"id": f"n_{source}_{number}", "type": type_ref, "name": f"{source}-{number}", "environment": "prod",
              "properties": {"arn": arn}, "provenance": {"kind": "imported", "source": source}}
             for number, (arn, type_ref) in enumerate(sorted(arns.items()))]
    return {"groups": [], "nodes": nodes, "edges": []}


def test_a_state_import_then_a_discovery_of_the_same_functions_buckets_apis_and_log_groups_adds_nothing(library, generic):
    arns = _state_arns()
    assert len(arns) == 4
    document, _ = _apply(_blank(), _accept_all(_items(import_state(_state()), _blank(), generic)), library, generic)
    in_clone = {node["properties"].get("arn") for node in document["nodes"]}
    assert set(arns) <= in_clone
    found = _found_elsewhere(arns, "aws")
    aligned = changesets.align(found, document)
    assert aligned == {"groups": [], "nodes": [], "edges": []}
    assert changesets.build(aligned, document, owners=("aws",), is_generic=generic) == []


def test_a_state_import_then_a_scan_of_the_same_things_fills_in_and_adds_no_second_copy(library, generic):
    arns = _state_arns()
    document, _ = _apply(_blank(), _accept_all(_items(import_state(_state()), _blank(), generic)), library, generic)
    found = _found_elsewhere(arns, "repo")
    enriched = changesets.enrich(found, document)
    clone_ids = {node["id"] for node in document["nodes"]}
    assert {node["id"] for node in enriched["nodes"]} <= clone_ids
    assert len(enriched["nodes"]) == len(arns)
    assert changesets.build(enriched, document, owners=("repo",), is_generic=generic) == []


def test_an_arn_the_state_does_not_hold_is_not_made_up():
    state = _state()
    for resource in state["resources"]:
        if resource["type"] in ARN_TYPES:
            del resource["instances"][0]["attributes"]["arn"]
    drawn = [node for node in import_state(state)["nodes"] if node["type"] in set(ARN_TYPES.values())]
    assert len(drawn) == 4
    assert all("arn" not in node["properties"] for node in drawn)


def test_an_edge_left_out_because_its_end_was_rejected_is_not_offered_again(library, generic):
    incoming = import_state(_state())
    items = _items(incoming, _blank(), generic)
    items = changesets.decide(items, [item["key"] for item in items if item["key"] != "node:n_aws_sqs_queue_jobs"], "accept")
    items = changesets.decide(items, ["node:n_aws_sqs_queue_jobs"], "reject")
    document, report = _apply(_blank(), items, library, generic)
    assert report["left_out_edges"] == 1
    settled = changesets.settle_left_out(items, report["left_out"])
    rejected = {item["key"]: item["item"] for item in settled if item["decision"] == "reject"}
    assert _items(import_state(_state()), document, generic, rejected=rejected) == []
    # An end that is only undecided keeps its edge on offer for the next import.
    undecided = changesets.decide(items, ["node:n_aws_sqs_queue_jobs"], "undecided")
    kept = changesets.settle_left_out(undecided, ["edge:n_aws_lambda_function_shop|consumes_from|n_aws_sqs_queue_jobs"])
    assert [item["decision"] for item in kept if item["key"].endswith("|n_aws_sqs_queue_jobs")] == ["accept"]


# -- a repository scan next to what a Terraform state already put in the clone ---------------------------------------------


def _scanned(extra_nodes=(), edges=()):
    """What a scan of the shop's repository says: the same function the state holds, plus what only the files know."""
    function = {"id": "scan_shop", "type": "service.function@2", "name": "shop-api", "environment": "prod",
                "provenance": {"kind": "imported", "source": "repo:acme/shop:terraform/main.tf"},
                "properties": {"platform": "aws_lambda", "runtime": "python3.11", "memory_mb": 1, "package_type": "zip"},
                "notes": "Python code in shop"}
    return {"nodes": [function, *extra_nodes], "groups": [], "edges": list(edges)}


def _with_state(library, generic):
    items = _accept_all([item for item in _items(import_state(_state()), _blank(), generic) if not item["held"]])
    return _apply(_blank(), items, library, generic)[0]


def _shop(document):
    return next(node for node in document["nodes"] if node["name"] == "shop-api")


def test_a_scan_finds_the_clones_own_function_by_its_name_and_fills_only_what_the_clone_lacks(library, generic):
    document = _with_state(library, generic)
    shop = _shop(document)
    shop["properties"].pop("package_type", None)
    fitted = changesets.enrich(_scanned(), document)
    [node] = fitted["nodes"]
    assert node["id"] == shop["id"]  # the clone's own item, not a second function
    assert node["properties"]["runtime"] == "python3.12" and node["properties"]["memory_mb"] == 256  # what the clone holds stays
    assert node["properties"]["package_type"] == "zip" and node["notes"] == "Python code in shop"  # what it lacked is filled
    assert node["provenance"]["source"].startswith("terraform:")
    items = changesets.build(fitted, document, owners=("repo",), is_generic=generic)
    assert [(item["kind"], item["action"], [c["field"] for c in item["changes"]]) for item in items] == [
        ("node", "update", ["notes", "package_type"])]


def test_a_scan_that_adds_nothing_to_what_the_clone_holds_is_an_empty_change_set(library, generic):
    document = _with_state(library, generic)
    again = changesets.enrich(_scanned(), document)
    items = _accept_all(changesets.build(again, document, owners=("repo",), is_generic=generic))
    document, _ = _apply(document, items, library, generic)
    assert changesets.build(changesets.enrich(_scanned(), document), document, owners=("repo",), is_generic=generic) == []


def test_a_connection_from_a_scan_is_pointed_at_the_clones_own_item(library, generic):
    document = _with_state(library, generic)
    repo = {"id": "github_repo_1", "type": "repo.github@1", "name": "acme/shop", "environment": "shared",
            "provenance": {"kind": "imported", "source": "github"},
            "properties": {"url": "https://github.com/acme/shop", "auth_method": "github_app"}}
    edge = {"id": "e_scan", "from": "scan_shop", "to": "github_repo_1", "type": "deploys_from",
            "provenance": {"kind": "imported", "source": "repo:acme/shop:terraform/main.tf"}}
    fitted = changesets.enrich(_scanned([repo], [edge]), document)
    assert [(e["from"], e["to"]) for e in fitted["edges"]] == [(_shop(document)["id"], "github_repo_1")]
    assert [item["key"] for item in changesets.build(fitted, document, owners=("repo",), is_generic=generic)
            if item["kind"] == "edge"] == [f"edge:{_shop(document)['id']}|deploys_from|github_repo_1"]


def test_a_scan_matches_by_arn_and_by_id_as_well_as_by_name(library, generic):
    document = _with_state(library, generic)
    queue = next(node for node in document["nodes"] if node["name"] == "payment-jobs")
    by_arn = {"id": "other_id", "type": "messaging.sqs@1", "name": "differently-named", "environment": "prod",
              "provenance": {"kind": "imported", "source": "repo:a/b:x.tf"},
              "properties": {"arn": queue["properties"]["arn"], "dlq": "payment-jobs-dlq"}}
    by_id = {**by_arn, "id": queue["id"], "properties": {"dlq": "dead"}}
    for scanned in (by_arn, by_id):
        [fitted] = changesets.enrich({"nodes": [scanned], "groups": [], "edges": []}, document)["nodes"]
        assert fitted["id"] == queue["id"] and fitted["name"] == "payment-jobs"
        assert fitted["properties"]["dlq"] in ("payment-jobs-dlq", "dead")


def test_something_the_scan_alone_found_is_added_and_a_scan_removes_only_what_an_earlier_scan_added(library, generic):
    document = _with_state(library, generic)
    new = {"id": "scan_bucket", "type": "data.s3@1", "name": "files", "environment": "prod",
           "provenance": {"kind": "imported", "source": "repo:acme/shop:x.tf"}, "properties": {"bucket_name": "files", "region": "r"}}
    items = changesets.build(changesets.enrich(_scanned([new]), document), document, owners=("repo",), is_generic=generic)
    assert [(item["action"], item["item"]["name"]) for item in items if item["kind"] == "node"] == [
        ("update", "shop-api"), ("add", "files")]
    document, _ = _apply(document, _accept_all(items), library, generic)
    later = changesets.build(changesets.enrich(_scanned(), document), document, owners=("repo",), is_generic=generic)
    assert [(item["action"], item["item"]["name"]) for item in later] == [("remove", "files")]  # it was put there by a scan
    assert not [item for item in later if item["item"]["name"] == "shop-api"]  # the state's own function is never removed by a scan

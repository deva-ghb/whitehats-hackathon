"""Importing an estate: the change set a person reviews, what accepting does, and who may do it."""

import json
from itertools import count
from pathlib import Path

from library.resolve import load_library, resolve_clone
from matrix_account import matrix_fixture  # noqa: F401 - the fixture, imported so this module can ask for `matrix`
from test_connector_status import EXTERNAL_ID, fake_aws  # noqa: F401 - a moto AWS account, used as a fixture

from app.db import SessionLocal
from app.models import ChangeSet, Clone

STATE = Path(__file__).resolve().parent / "fixtures" / "terraform" / "shop.state.json"
_slugs = count(1)


def _state(extra: bool = False) -> dict:
    state = json.loads(STATE.read_text())
    if extra:
        state["resources"].append({"mode": "managed", "type": "aws_kinesis_stream", "name": "clicks",
                                   "instances": [{"attributes": {"id": "clicks", "name": "clicks"}}]})
    return state


def _clone(client, headers, path="/api/clones") -> int:
    slug = f"import-{next(_slugs)}"
    document = {"schema_version": 1, "clone": slug, "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "Test", "reason": "blank"},
                "nodes": [], "edges": [], "groups": [], "access_paths": [], "credential_refs": []}
    made = client.post(path, headers=headers, json={"author": "Test", "reason": "blank", "document": document})
    assert made.status_code == 200, made.text
    return made.json()["id"]


def _start(client, headers, clone_id, state=None, **extra):
    body = {"source": "terraform_state", "clone_id": clone_id, "state": state or _state(), **extra}
    return client.post("/api/imports", headers=headers, json=body)


def _keys(detail, **where):
    return [item["key"] for item in detail["items"] if all(item[name] == value for name, value in where.items())]


def _decide(client, headers, import_id, keys, decision="accept", type_ref=None):
    return client.put(f"/api/imports/{import_id}/decisions", headers=headers,
                      json={"keys": keys, "decision": decision, **({"type": type_ref} if type_ref else {})})


def test_an_import_waits_for_review_and_changes_nothing_until_it_is_applied(client, engineer):
    clone_id = _clone(client, engineer)
    started = _start(client, engineer, clone_id)
    assert started.status_code == 200, started.text
    detail = client.get(f"/api/imports/{started.json()['id']}", headers=engineer).json()
    assert detail["status"] == "open" and detail["clone"].startswith("import-")
    assert detail["changes"] == started.json()["changes"] == len(detail["items"])
    assert detail["undecided"] == detail["changes"] and detail["accepted"] == 0
    assert client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]["nodes"] == []
    assert detail["now"]["total"] == 0 and detail["after"]["total"] == 0


def test_two_things_with_one_name_are_told_apart_in_the_sentence_of_a_connection(client, engineer):
    state = _state()
    next(r for r in state["resources"] if r["type"] == "aws_apigatewayv2_api")["instances"][0]["attributes"]["name"] = "shop-api"
    detail = client.get(f"/api/imports/{_start(client, engineer, _clone(client, engineer), state).json()['id']}",
                        headers=engineer).json()
    calls = next(item for item in detail["items"] if item["kind"] == "edge" and item["item"]["type"] == "calls")
    assert calls["ends"] == ["shop-api (Amazon API Gateway)", "shop-api (Serverless function)"]
    queue = next(item for item in detail["items"] if item["item"].get("name") == "payment-jobs")
    assert queue["item"]["name"] == "payment-jobs"  # a name nothing else shares is left as it is
    decided = _decide(client, engineer, detail["id"], [calls["key"]]).json()
    assert decided["items"][[item["key"] for item in decided["items"]].index(calls["key"])]["waits_for"] == [
        "shop-api (Amazon API Gateway)", "shop-api (Serverless function)"]


def test_accepting_and_applying_makes_the_next_version_and_a_second_import_is_empty(client, engineer):
    clone_id = _clone(client, engineer)
    detail = client.get(f"/api/imports/{_start(client, engineer, clone_id).json()['id']}", headers=engineer).json()
    decided = _decide(client, engineer, detail["id"], _keys(detail)).json()
    assert decided["accepted"] == decided["changes"]
    assert decided["after"]["share"] == 100.0 and decided["after"]["counts"]["drawn"] == 0
    applied = client.post(f"/api/imports/{detail['id']}/apply", headers=engineer)
    assert applied.status_code == 200, applied.text
    assert applied.json()["version"] == 2 and applied.json()["report"]["left_out_edges"] == 0
    document = client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]
    assert {node["name"] for node in document["nodes"]} >= {"shop-api", "receipts", "orders"}
    assert all(node["provenance"]["kind"] == "imported" for node in document["nodes"])
    assert all(edge["provenance"]["source"].startswith("terraform:") for edge in document["edges"])
    assert resolve_clone(document, load_library())[1] == []
    versions = client.get(f"/api/clones/{clone_id}/versions", headers=engineer).json()["versions"]
    assert versions[-1]["reason"].startswith("Import Terraform state: ")
    again = _start(client, engineer, clone_id)
    assert again.json() == {"id": None, "changes": 0, "clone_id": clone_id,
                            "message": "Nothing has changed since the clone was last brought up to date."}
    assert client.get(f"/api/imports/{detail['id']}", headers=engineer).json()["status"] == "applied"


def test_no_secret_value_from_the_state_is_stored_or_returned(client, engineer):
    state = _state()
    function = next(r for r in state["resources"] if r["type"] == "aws_lambda_function")
    function["instances"][0]["attributes"]["function_name"] = "github_pat_" + "A1b2C3d4E5" * 4  # a name that is a secret
    clone_id = _clone(client, engineer)
    started = _start(client, engineer, clone_id, state)
    assert started.status_code == 200, started.text
    detail = client.get(f"/api/imports/{started.json()['id']}", headers=engineer)
    with SessionLocal() as db:
        stored = json.dumps(db.get(ChangeSet, started.json()["id"]).items)
    for text in (detail.text, stored):
        assert "github_pat_" not in text and "not-copied-anywhere" not in text


def test_a_resource_the_library_has_no_type_for_is_held_and_cannot_be_accepted_untyped(client, engineer):
    clone_id = _clone(client, engineer)
    detail = client.get(f"/api/imports/{_start(client, engineer, clone_id, _state(extra=True)).json()['id']}",
                        headers=engineer).json()
    held = _keys(detail, held=True)
    assert held == ["node:n_aws_kinesis_stream_clicks"]
    refused = _decide(client, engineer, detail["id"], held)
    assert refused.status_code == 422 and refused.json()["detail"] == [
        "clicks has no library type yet. Pick one before accepting it."]
    generic = _decide(client, engineer, detail["id"], held, type_ref="generic.services@1")
    assert generic.status_code == 422 and "Generic type" in generic.json()["detail"][0]
    unknown = _decide(client, engineer, detail["id"], held, type_ref="made.up@1")
    assert unknown.status_code == 422
    typed = _decide(client, engineer, detail["id"], _keys(detail), type_ref="messaging.sqs@1")
    assert typed.status_code == 200, typed.text
    applied = client.post(f"/api/imports/{detail['id']}/apply", headers=engineer)
    assert applied.status_code == 200, applied.text
    document = client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]
    stream = next(node for node in document["nodes"] if node["name"] == "clicks")
    assert stream["type"] == "messaging.sqs@1"
    assert all(not node["type"].startswith("generic.") for node in document["nodes"])


def test_an_accepted_change_the_library_would_refuse_stops_the_apply_and_changes_nothing(client, engineer):
    clone_id = _clone(client, engineer)
    node = {"environment": "prod", "provenance": {"kind": "imported", "source": "terraform:x"}}
    items = [
        {"key": "node:n_q", "kind": "node", "action": "add", "held": False, "decision": "accept", "type": None,
         "item": {**node, "id": "n_q", "type": "messaging.sqs@1", "name": "jobs"}},
        {"key": "node:n_b", "kind": "node", "action": "add", "held": False, "decision": "accept", "type": None,
         "item": {**node, "id": "n_b", "type": "data.s3@1", "name": "files"}},
        {"key": "edge:n_q|reads|n_b", "kind": "edge", "action": "add", "held": False, "decision": "accept", "type": None,
         "item": {"from": "n_q", "to": "n_b", "type": "reads"}},
    ]
    with SessionLocal() as db:
        clone = db.get(Clone, clone_id)
        made = ChangeSet(org_id=clone.org_id, project_id=clone.project_id, clone_id=clone_id, source="terraform_state",
                         base_version=1, items=items)
        db.add(made)
        db.commit()
        made_id = made.id
    refused = client.post(f"/api/imports/{made_id}/apply", headers=engineer)
    assert refused.status_code == 422
    assert refused.json()["detail"] == ["n_q -reads-> n_b: messaging.sqs cannot start a 'reads' edge"]
    assert [row["number"] for row in client.get(f"/api/clones/{clone_id}/versions", headers=engineer).json()["versions"]] == [1]
    assert client.get(f"/api/imports/{made_id}", headers=engineer).json()["status"] == "open"


def test_applying_with_nothing_accepted_is_refused_and_a_discarded_import_is_closed(client, engineer):
    clone_id = _clone(client, engineer)
    made = _start(client, engineer, clone_id).json()["id"]
    empty = client.post(f"/api/imports/{made}/apply", headers=engineer)
    assert empty.status_code == 422
    assert empty.json()["detail"] == ["Nothing is accepted yet. Accept at least one change, or discard this import."]
    assert client.post(f"/api/imports/{made}/discard", headers=engineer).json()["status"] == "discarded"
    closed = client.post(f"/api/imports/{made}/apply", headers=engineer)
    assert closed.status_code == 422 and closed.json()["detail"] == ["This import is already discarded."]


def test_a_rejected_change_stays_out_of_the_next_import(client, engineer):
    clone_id = _clone(client, engineer)
    detail = client.get(f"/api/imports/{_start(client, engineer, clone_id).json()['id']}", headers=engineer).json()
    azure = [item["key"] for item in detail["items"] if "azurerm" in item["key"]]
    assert azure
    _decide(client, engineer, detail["id"], azure, "reject")
    _decide(client, engineer, detail["id"], [key for key in _keys(detail) if key not in azure])
    client.post(f"/api/imports/{detail['id']}/apply", headers=engineer)
    assert _start(client, engineer, clone_id).json()["changes"] == 0


def test_a_person_s_own_edits_between_the_import_and_the_apply_survive(client, engineer):
    clone_id = _clone(client, engineer)
    detail = client.get(f"/api/imports/{_start(client, engineer, clone_id).json()['id']}", headers=engineer).json()
    document = client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]
    document["nodes"].append({"id": "n_hand", "type": "service.backend@1", "name": "billing", "environment": "prod",
                              "provenance": {"kind": "drawn", "source": "Clone Studio"}})
    saved = client.post(f"/api/clones/{clone_id}/versions", headers=engineer,
                        json={"author": "Test", "reason": "drew billing", "document": document})
    assert saved.status_code == 200, saved.text
    _decide(client, engineer, detail["id"], _keys(detail))
    assert client.post(f"/api/imports/{detail['id']}/apply", headers=engineer).json()["version"] == 3
    kept = client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]
    assert "billing" in {node["name"] for node in kept["nodes"]}
    assert len(kept["nodes"]) > 5


def test_a_file_that_is_not_a_state_is_refused_in_plain_words(client, engineer):
    clone_id = _clone(client, engineer)
    wrong = _start(client, engineer, clone_id, {"version": 2, "resources": []})
    assert wrong.status_code == 422 and wrong.json()["detail"] == "Terraform state version must be 3 or 4"
    missing = client.post("/api/imports", headers=engineer, json={"source": "terraform_state", "clone_id": clone_id})
    assert missing.status_code == 422 and missing.json()["detail"] == "Send the Terraform state file as `state`."


def test_what_a_connection_found_is_reviewed_like_any_import_and_a_second_look_is_empty(
        client, ceo, engineer, fake_aws):  # noqa: F811
    aws = next(row for row in client.get("/api/connectors", headers=ceo).json() if row["key"] == "aws")
    try:
        client.put(f"/api/connectors/{aws['id']}", headers=ceo, json={"values": {
            "role_arn": fake_aws["role_arn"], "external_id": EXTERNAL_ID, "region": fake_aws["region"]}})
        assert client.post(f"/api/connectors/{aws['id']}/discover", headers=ceo).status_code == 200
        clone_id = _clone(client, engineer)
        made = client.post("/api/imports", headers=engineer, json={"source": "connections", "clone_id": clone_id})
        assert made.status_code == 200, made.text
        detail = client.get(f"/api/imports/{made.json()['id']}", headers=engineer).json()
        assert detail["label"].startswith("What AWS found") and detail["source"] == "connections"
        held = _keys(detail, held=True)
        assert held, "the fake account holds a security group the library has no type for"
        _decide(client, engineer, detail["id"], held, "reject")
        _decide(client, engineer, detail["id"], [key for key in _keys(detail) if key not in held])
        applied = client.post(f"/api/imports/{detail['id']}/apply", headers=engineer)
        assert applied.status_code == 200, applied.text
        document = client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]
        assert {"fintech-receipts", "orders", "payment-jobs", "admin-box"} <= {node["name"] for node in document["nodes"]}
        assert all(node["provenance"] == {"kind": "discovered", "source": "aws"} for node in document["nodes"])
        assert all(not node["type"].startswith("generic.") for node in document["nodes"])
        assert resolve_clone(document, load_library())[1] == []
        again = client.post("/api/imports", headers=engineer, json={"source": "connections", "clone_id": clone_id})
        assert again.json()["id"] is None
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_a_new_clone_is_only_started_when_the_source_reads_cleanly(client, engineer):
    new = {"name": "from-state", "environment": "prod"}
    before = {row["clone"] for row in client.get("/api/clones", headers=engineer).json()["clones"]}
    odd = client.post("/api/imports", headers=engineer, json={"source": "terraform_state", "new_clone": new,
                                                              "state": {"version": 2, "resources": []}})
    assert odd.status_code == 422
    assert {row["clone"] for row in client.get("/api/clones", headers=engineer).json()["clones"]} == before
    made = client.post("/api/imports", headers=engineer, json={"source": "terraform_state", "new_clone": new,
                                                               "state": _state()})
    assert made.status_code == 200, made.text
    detail = client.get(f"/api/imports/{made.json()['id']}", headers=engineer).json()
    assert detail["clone"] == "from-state" and detail["environment"] == "prod" and detail["base_version"] == 1
    again = client.post("/api/imports", headers=engineer, json={"source": "terraform_state", "new_clone": new,
                                                                "state": _state()})
    assert again.status_code == 409 and again.json()["detail"] == "This project already has a clone with that name."


def test_a_new_clone_needs_a_proper_name_and_exactly_one_target(client, engineer):
    bad = client.post("/api/imports", headers=engineer, json={
        "source": "terraform_state", "new_clone": {"name": "Not A Slug", "environment": "prod"}, "state": _state()})
    assert bad.status_code == 422 and "clone" in bad.json()["detail"][0]
    both = client.post("/api/imports", headers=engineer, json={
        "source": "terraform_state", "clone_id": 1, "new_clone": {"name": "x-1", "environment": "prod"}, "state": _state()})
    assert both.status_code == 422
    assert both.json()["detail"] == "Name either a clone to import into (`clone_id`) or a new one (`new_clone`)."
    neither = client.post("/api/imports", headers=engineer, json={"source": "terraform_state", "state": _state()})
    assert neither.status_code == 422


def test_an_import_from_connections_with_nothing_found_says_what_to_do(client, engineer):
    clone_id = _clone(client, engineer)
    found = client.post("/api/imports", headers=engineer, json={"source": "connections", "clone_id": clone_id})
    assert found.status_code == 422
    assert found.json()["detail"] == ("No connection attached to this project has found anything yet. "
                                      "Select a connection in the Studio and choose Find resources.")


def test_only_someone_who_can_edit_the_model_may_start_decide_or_apply(client, matrix):
    editor, viewer = matrix.headers["engineer"], matrix.headers["viewer"]
    clone_id = matrix.ids["clone_main"]
    assert _start(client, viewer, clone_id).status_code == 403
    made = _start(client, editor, clone_id).json()["id"]
    assert client.get(f"/api/imports/{made}", headers=viewer).status_code == 200
    assert _decide(client, viewer, made, ["node:n_aws_vpc_main"]).status_code == 403
    assert client.post(f"/api/imports/{made}/apply", headers=viewer).status_code == 403
    assert client.post(f"/api/imports/{made}/discard", headers=viewer).status_code == 403


def test_an_import_stays_in_its_own_project_and_company(client, ceo, matrix):
    editor = matrix.headers["engineer"]
    side = matrix.ids["clone_side"]
    assert _start(client, editor, side).status_code == 404  # a Side clone is not in the project the request names
    assert client.post("/api/imports?project=side", headers=editor,
                       json={"source": "terraform_state", "clone_id": side, "state": _state()}).status_code == 403
    made = _start(client, editor, matrix.ids["clone_main"]).json()["id"]
    assert client.get(f"/api/imports/{made}", headers=ceo).status_code == 404  # another company
    assert made not in {row["id"] for row in client.get("/api/imports", headers=ceo).json()["imports"]}
    assert made in {row["id"] for row in client.get("/api/imports", headers=editor).json()["imports"]}
    lead = matrix.headers["security_lead"]
    assert made not in {row["id"] for row in client.get("/api/imports?project=side", headers=lead).json()["imports"]}
    assert client.get(f"/api/imports/{made}", headers=lead).status_code == 200


def test_a_company_s_imports_leave_with_its_data_and_are_in_its_export(client):
    from app.models import Organization
    from app.projects import ensure_main_project
    from app.services.clones import blank_document, create_clone
    from app.services.tenancy import delete_tenant_data, export

    with SessionLocal() as db:
        org = Organization(name="Leaving Co", slug="leaving-co", mfa_required=False)
        db.add(org)
        db.flush()
        main = ensure_main_project(db, org)
        clone = create_clone(db, org.id, blank_document("gone", "prod", "test", "setup"), "test", "setup", project_id=main.id)
        db.add(ChangeSet(org_id=org.id, project_id=main.id, clone_id=clone.id, source="terraform_state", base_version=1,
                         items=[{"key": "node:n_x"}]))
        db.commit()
        path, _digest = export(db, org)
        try:
            assert json.loads(Path(path).read_text())["tables"]["change_sets"][0]["items"] == [{"key": "node:n_x"}]
        finally:
            Path(path).unlink()
        assert delete_tenant_data(db, org)["change_sets"] == 1
        db.commit()
        assert db.query(ChangeSet).filter_by(org_id=org.id).count() == 0


ARN_TYPES = {"aws_lambda_function": "service.function@2", "aws_s3_bucket": "data.s3@1",
             "aws_apigatewayv2_api": "edge.api-gateway@1", "aws_cloudwatch_log_group": "observability.cloudwatch@1"}


def test_a_state_import_after_a_discovery_adds_no_second_copy_of_what_was_discovered(client, engineer):
    """R-055: discovery first, then the Terraform state of the same estate: matched by ARN, nothing added twice."""
    discovered = [{"id": f"n_aws_{number}", "type": ARN_TYPES[resource["type"]], "name": f"found-{number}",
                   "environment": "prod", "provenance": {"kind": "imported", "source": "aws"},
                   "properties": {"arn": resource["instances"][0]["attributes"]["arn"]}}
                  for number, resource in enumerate(_state()["resources"]) if resource["type"] in ARN_TYPES]
    assert len(discovered) == 4
    blank = client.get(f"/api/imports/{_start(client, engineer, _clone(client, engineer)).json()['id']}",
                       headers=engineer).json()
    clone_id = _clone(client, engineer)
    current = client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]
    saved = client.post(f"/api/clones/{clone_id}/versions", headers=engineer, json={
        "author": "Test", "reason": "discovered", "document": {**current, "nodes": discovered}})
    assert saved.status_code == 200, saved.text
    detail = client.get(f"/api/imports/{_start(client, engineer, clone_id).json()['id']}", headers=engineer).json()
    added = [item["item"] for item in detail["items"] if item["action"] == "add" and item["kind"] == "node"]
    arns = {node["properties"]["arn"] for node in discovered}
    assert not [node for node in added if (node.get("properties") or {}).get("arn") in arns]
    blank_added = [item for item in blank["items"] if item["action"] == "add" and item["kind"] == "node"]
    assert len(added) == len(blank_added) - 4
    assert not [item for item in detail["items"] if item["action"] == "remove"]  # the discovered nodes stay

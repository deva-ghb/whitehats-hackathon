"""Terraform state becomes a clone change set. A second read of the same file is empty."""

import json
import re
from pathlib import Path

import pytest
from library.resolve import ROUTING_PROPERTIES, load_library, resolve_clone

from app.services.terraform_state import change_set, import_state, left_out
from app.services.terraform_types import GENERIC, MAPPINGS

STATE = Path(__file__).resolve().parent / "fixtures" / "terraform" / "shop.state.json"
_ID = re.compile(r"^[a-z][a-z0-9_]{1,80}$")


def _state() -> dict:
    return json.loads(STATE.read_text())


def _names(items: list[dict]) -> set[str]:
    return {item["name"] for item in items}


def _edge(imported: dict, kind: str, source_name: str, target_name: str) -> dict:
    by_id = {item["id"]: item["name"] for item in imported["nodes"] + imported["groups"]}
    found = [
        edge for edge in imported["edges"]
        if edge["type"] == kind and by_id.get(edge["from"]) == source_name and by_id.get(edge["to"]) == target_name
    ]
    assert found, (kind, source_name, target_name)
    return found[0]


def test_recorded_state_places_the_shop_and_the_azure_vm():
    imported = import_state(_state(), environment="prod")
    assert _names(imported["nodes"]) == {
        "shop-api", "shop-api-role", "payment-jobs", "receipts", "orders", "shop-http",
        "/aws/lambda/shop-api", "admin-vm", "appfiles",
    }
    assert _names(imported["groups"]) == {"app", "app-aws", "app-rg", "app-vnet", "app-azure"}
    assert _edge(imported, "runs_as", "shop-api", "shop-api-role")
    assert _edge(imported, "contained_in", "shop-api", "app-aws")
    assert _edge(imported, "consumes_from", "shop-api", "payment-jobs")
    assert _edge(imported, "calls", "shop-http", "shop-api")
    assert _edge(imported, "contained_in", "admin-vm", "app-rg")
    assert _edge(imported, "contained_in", "appfiles", "app-rg")
    groups = {item["name"]: item for item in imported["groups"]}
    assert groups["app-aws"]["parent"] == groups["app"]["id"]
    assert groups["app-azure"]["parent"] == groups["app-vnet"]["id"]
    assert groups["app-vnet"]["parent"] == groups["app-rg"]["id"]
    for collection in imported.values():
        for item in collection:
            assert _ID.fullmatch(item["id"])
    assert "other-cloud-logs" not in _names(imported["nodes"])
    packed = json.dumps(imported)
    assert "not-copied-anywhere" not in packed
    assert "github_pat_" not in packed
    function = next(item for item in imported["nodes"] if item["name"] == "shop-api" and item["type"] == "service.function@2")
    assert function["properties"]["runtime"] == "python3.12"
    assert function["properties"]["platform"] == "aws_lambda"
    assert function["properties"]["region"] == "ap-southeast-2"
    assert function["provenance"] == {"kind": "imported", "source": "terraform:aws_lambda_function.shop"}


def _as_clone(imported: dict) -> dict:
    return {"schema_version": 1, "clone": "shop", "environment": "prod", "library_version": "2026.10",
            "version": {"number": 1, "author": "terraform import", "reason": "recorded state"},
            "access_paths": [], "credential_refs": [], **imported}


def test_an_import_is_a_clone_that_resolves_against_the_node_library():
    imported = import_state(_state())
    resolved, problems = resolve_clone(_as_clone(imported), load_library())
    assert problems == []
    assert len(resolved) == len(imported["nodes"]) + len(imported["groups"])
    types = {item["name"]: item["type"] for item in imported["nodes"] + imported["groups"]}
    assert types["shop-api"] == "service.function@2"
    assert types["admin-vm"] == "compute.vm@1"
    assert types["app-vnet"] == "boundary.vpc@1"


@pytest.fixture(scope="module")
def library() -> dict[str, dict]:
    return load_library()


@pytest.mark.parametrize("resource_type", sorted(MAPPINGS))
def test_every_mapping_names_a_library_type_and_only_its_properties(library, resource_type):
    mapping = MAPPINGS[resource_type]
    type_id, version = mapping.type_ref.rsplit("@", 1)
    declared = library[type_id]
    assert declared["version"] == int(version)
    assert not declared.get("generic")
    keys = {key for _, key, _ in mapping.fields} | set(mapping.fixed) | ({mapping.region_key} - {""})
    assert keys <= {prop["key"] for prop in declared["properties"]} | set(ROUTING_PROPERTIES)
    assert bool(mapping.group_kind) == (declared["family"] == "boundaries")


def test_the_fallback_is_a_generic_type(library):
    assert library[GENERIC.rsplit("@", 1)[0]].get("generic") is True


def test_a_resource_the_library_does_not_know_becomes_a_generic_placeholder():
    state = _state()
    state["resources"].append({"mode": "managed", "type": "aws_kinesis_stream", "name": "clicks",
                               "instances": [{"attributes": {"id": "clicks", "name": "clicks"}}]})
    imported = import_state(state)
    stream = next(item for item in imported["nodes"] if item["name"] == "clicks")
    assert stream["type"] == "generic.services@1"
    assert stream["properties"]["what_is_this"] == "Terraform resource aws_kinesis_stream"
    _, problems = resolve_clone(_as_clone(imported), load_library())
    assert problems == [f"{stream['id']} (clicks): resolves only to the Generic type generic.services"]


def test_modules_punctuation_and_for_each_keys_do_not_share_an_id():
    def bucket(name, *, module=None, index_key=None, bucket_name):
        instance = {"attributes": {"id": bucket_name, "bucket": bucket_name}}
        if index_key is not None:
            instance["index_key"] = index_key
        resource = {"mode": "managed", "type": "aws_s3_bucket", "name": name, "instances": [instance]}
        if module:
            resource["module"] = module
        return resource

    state = {"version": 4, "resources": [
        bucket("logs", module="module.pay", bucket_name="pay-logs"),
        bucket("logs", module="module.shop", bucket_name="shop-logs"),
        bucket("web-api", bucket_name="web-api"),
        bucket("web_api", bucket_name="web_api"),
        {"mode": "managed", "type": "aws_s3_bucket", "name": "item", "instances": [
            {"index_key": "a-b", "attributes": {"id": "ab", "bucket": "item-ab"}},
            {"index_key": "a_b", "attributes": {"id": "a_b", "bucket": "item_ab"}},
        ]},
    ]}
    imported = import_state(state)
    ids = [item["id"] for item in imported["nodes"]]
    assert len(ids) == len(set(ids)) == 6
    for ident in ids:
        assert _ID.fullmatch(ident)


def test_reimporting_unchanged_state_is_an_empty_change_set():
    first = import_state(_state())
    again = import_state(_state())
    diff = change_set(first, again)
    assert diff == {"nodes": [], "groups": [], "edges": []}


def test_a_new_bucket_is_the_only_added_node():
    original = import_state(_state())
    changed = _state()
    changed["resources"].append({
        "mode": "managed",
        "type": "aws_s3_bucket",
        "name": "extra",
        "provider": "provider[\"registry.terraform.io/hashicorp/aws\"]",
        "instances": [{"attributes": {"id": "extra-receipts", "bucket": "extra-receipts", "region": "ap-southeast-2"}}],
    })
    diff = change_set(original, import_state(changed))
    assert [item["item"]["name"] for item in diff["nodes"]] == ["extra-receipts"]
    assert diff["nodes"][0]["action"] == "add"
    assert diff["groups"] == []
    assert diff["edges"] == []
    removed = change_set(import_state(changed), original)
    assert [item["action"] for item in removed["nodes"]] == ["remove"]
    assert removed["nodes"][0]["item"]["name"] == "extra-receipts"


def test_a_state_that_is_not_a_state_file_is_refused():
    with pytest.raises(TypeError, match="must be an object"):
        import_state([])
    with pytest.raises(ValueError, match="version must be 3 or 4"):
        import_state({"version": 2, "resources": []})


def _lambda_with_policy(statements: list[dict], role_name: str = "shop-api-role") -> dict:
    """The recorded shop state with its role given one policy, the way Terraform records an inline policy."""
    state = _state()
    role = next(item for item in state["resources"] if item["type"] == "aws_iam_role")
    role["instances"][0]["attributes"]["name"] = role_name
    state["resources"].append({"mode": "managed", "type": "aws_iam_role_policy", "name": "shop", "instances": [{"attributes": {
        "role": role_name, "name": "shop", "policy": json.dumps({"Version": "2012-10-17", "Statement": statements})}}]})
    return state


def _arn(state: dict, kind: str) -> str:
    return next(r for r in state["resources"] if r["type"] == kind)["instances"][0]["attributes"]["arn"]


def test_a_policy_makes_the_service_that_runs_as_the_role_read_and_write_what_it_names():
    recorded = _state()
    queue, table, bucket, logs = (_arn(recorded, kind) for kind in
                                  ("aws_sqs_queue", "aws_dynamodb_table", "aws_s3_bucket", "aws_cloudwatch_log_group"))
    state = _lambda_with_policy([
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": [f"{bucket}/*"]},
        {"Effect": "Allow", "Action": "dynamodb:*", "Resource": [table, f"{table}/index/*"]},
        {"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": queue},
        {"Effect": "Deny", "Action": "logs:PutLogEvents", "Resource": f"{logs}:*"},
        {"Effect": "Allow", "Action": "s3:*", "Resource": "*"},
    ])
    imported = import_state(state)
    for kind, target in (("reads", "receipts"), ("writes", "receipts"), ("reads", "orders"), ("writes", "orders"),
                         ("publishes_to", "payment-jobs")):
        edge = _edge(imported, kind, "shop-api", target)
        assert edge["provenance"] == {"kind": "imported", "source": "terraform:aws_iam_role_policy.shop"}
    names = {item["id"]: item["name"] for item in imported["nodes"]}
    access = {(edge["type"], names[edge["to"]]) for edge in imported["edges"]
              if edge["type"] in ("reads", "writes", "publishes_to", "emits_telemetry_to")}
    assert access == {("reads", "receipts"), ("writes", "receipts"), ("reads", "orders"), ("writes", "orders"),
                      ("publishes_to", "payment-jobs")}
    assert resolve_clone(_as_clone(imported), load_library())[1] == []


def test_a_policy_for_a_role_nothing_runs_as_makes_no_edge_and_an_unreadable_policy_none():
    state = _lambda_with_policy([{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::x/*"}], "someone-else")
    assert {edge["type"] for edge in import_state(state)["edges"]} == {edge["type"] for edge in import_state(_state())["edges"]}
    broken = _lambda_with_policy([])
    broken["resources"][-1]["instances"][0]["attributes"]["policy"] = "{not json"
    assert import_state(broken) == import_state(_state())


def test_settings_are_counted_and_not_drawn():
    state = _lambda_with_policy([])
    for kind in ("aws_s3_bucket_public_access_block", "aws_lambda_permission"):
        state["resources"].append({"mode": "managed", "type": kind, "name": "x", "instances": [{"attributes": {"id": "x"}}]})
    assert left_out(state) == {"aws_iam_role_policy": 1, "aws_lambda_permission": 1, "aws_s3_bucket_public_access_block": 1}
    assert left_out(_state()) == {}
    assert {item["name"] for item in import_state(state)["nodes"]} == {item["name"] for item in import_state(_state())["nodes"]}


_SERVICE_TYPES = {"s3": "data.s3", "dynamodb": "data.dynamodb", "sqs": "messaging.sqs", "ssm": "identity.ssm-parameter-store",
                  "kms": "identity.kms", "logs": "observability.cloudwatch"}


@pytest.mark.parametrize("service", sorted(_SERVICE_TYPES))
def test_every_edge_a_policy_can_make_is_one_the_library_allows(library, service):
    from app.services.terraform_access import _VERBS

    function, target = library["service.function"], library[_SERVICE_TYPES[service]]
    for edge in set(_VERBS[service].values()):
        assert edge in function["edges"]["allowed_out"], edge
        assert edge in target["edges"]["allowed_in"], edge


_PATH = "/shop/api_token"
_REAL_VALUE = "q7Zr2LmX9vB4nTc8WdHs5YjKe3PaUf6G"  # a made-up secret that no pattern would recognise: Terraform marks it sensitive


def _with_parameter() -> dict:
    """The shop state plus an SSM parameter holding a real secret, and a function that is told the parameter's path."""
    state = _state()
    function = next(r for r in state["resources"] if r["type"] == "aws_lambda_function")
    function["instances"][0]["attributes"]["environment"] = [{"variables": {"API_TOKEN_PARAMETER": _PATH}}]
    state["resources"].append({"mode": "managed", "type": "aws_ssm_parameter", "name": "token", "instances": [{
        "attributes": {"name": _PATH, "type": "SecureString", "value": _REAL_VALUE,
                       "arn": f"arn:aws:ssm:ap-southeast-2:111122223333:parameter{_PATH}"},
        "sensitive_attributes": [[{"type": "get_attr", "value": "value"}]]}]})
    return state


def test_the_path_to_a_parameter_is_kept_and_the_secret_it_holds_never_is():
    from app.services.terraform_secrets import leaks, secret_values

    state = _with_parameter()
    imported = import_state(state)
    parameter = next(item for item in imported["nodes"] if item["type"] == "identity.ssm-parameter-store@1")
    assert parameter["properties"]["parameter_path"] == _PATH
    assert _REAL_VALUE not in json.dumps(imported)
    found, short = secret_values(state)
    # The recorded state already holds one secret-looking handler; the parameter's own value is the second.
    assert set(found) == {"aws_lambda_function.shop[0]/handler", "aws_ssm_parameter.token[0]/value"} and short == 0
    assert leaks(state, json.dumps(imported)) == []
    # The same path in the import is a reference and not a leak, even though a function's environment names it.
    assert _PATH in json.dumps(imported)


def test_the_arn_the_state_holds_is_kept_on_a_function_bucket_api_log_group_and_parameter():
    state = _with_parameter()
    wanted = {"service.function@2": "aws_lambda_function", "data.s3@1": "aws_s3_bucket",
              "edge.api-gateway@1": "aws_apigatewayv2_api", "observability.cloudwatch@1": "aws_cloudwatch_log_group",
              "identity.ssm-parameter-store@1": "aws_ssm_parameter"}
    held = {resource["type"]: resource["instances"][0]["attributes"]["arn"]
            for resource in state["resources"] if resource["type"] in wanted.values()}
    assert set(held) == set(wanted.values())
    found = {node["type"]: node["properties"].get("arn") for node in import_state(state)["nodes"] if node["type"] in wanted}
    assert found == {type_ref: held[resource_type] for type_ref, resource_type in wanted.items()}
    assert _REAL_VALUE not in json.dumps(import_state(state))


def test_a_real_secret_in_an_import_is_a_leak_and_is_reported_by_where_it_is_not_what_it_is():
    from app.services.terraform_secrets import leaks

    state = _with_parameter()
    imported = import_state(state)
    imported["nodes"][0]["name"] = f"copied {_REAL_VALUE}"
    assert leaks(state, json.dumps(imported)) == ["aws_ssm_parameter.token[0]/value"]  # the handler never reaches an import


def test_a_value_that_looks_like_a_secret_is_found_without_terraform_marking_it():
    from app.services.terraform_secrets import secret_values

    state = _state()
    next(r for r in state["resources"] if r["type"] == "aws_sqs_queue")["instances"][0]["attributes"]["note"] = (
        "github_pat_" + "A1b2C3d4E5" * 4)
    assert "aws_sqs_queue.jobs[0]/note" in secret_values(state)[0]


def test_sensitive_values_too_short_to_search_for_are_counted():
    from app.services.terraform_secrets import secret_values

    state = _with_parameter()
    state["resources"][-1]["instances"][0]["attributes"]["value"] = "abc"
    found, short = secret_values(state)
    assert "aws_ssm_parameter.token[0]/value" not in found and short == 1


# What the real proving-ground state spells out as attributes (P5.2's importer follow-up): a gatekeeper function behind the
# API's authorizer, a dead-letter queue named in a redrive policy, a worker run from an image, and a customer managed key
# that a table and a parameter name. The values are made up; the shapes are the ones the real state has.
_ACCOUNT = "111122223333"
_REGION = "ap-southeast-2"
_KEY_ARN = f"arn:aws:kms:{_REGION}:{_ACCOUNT}:key/1234abcd-12ab-34cd-56ef-1234567890ab"
_IMAGE_REPOSITORY = f"{_ACCOUNT}.dkr.ecr.{_REGION}.amazonaws.com/shop/worker"


def _resource(kind: str, name: str, attributes: dict) -> dict:
    return {"mode": "managed", "type": kind, "name": name, "instances": [{"attributes": attributes}]}


def _function_arn(name: str) -> str:
    return f"arn:aws:lambda:{_REGION}:{_ACCOUNT}:function:{name}"


def _invoke_arn(name: str) -> str:
    return f"arn:aws:apigateway:{_REGION}:lambda:path/2015-03-31/functions/{_function_arn(name)}/invocations"


def _with_payments(*, alias: bool = False) -> dict:
    state = _state()
    queue = next(r for r in state["resources"] if r["type"] == "aws_sqs_queue")["instances"][0]["attributes"]
    table = next(r for r in state["resources"] if r["type"] == "aws_dynamodb_table")["instances"][0]["attributes"]
    dead_arn = f"arn:aws:sqs:{_REGION}:{_ACCOUNT}:payment-jobs-dlq"
    queue["redrive_policy"] = json.dumps({"deadLetterTargetArn": dead_arn, "maxReceiveCount": 3})
    table["server_side_encryption"] = [{"enabled": True, "kms_key_arn": _KEY_ARN}]
    state["resources"] += [
        _resource("aws_sqs_queue", "dead", {"id": "dead", "name": "payment-jobs-dlq", "arn": dead_arn}),
        _resource("aws_kms_key", "data", {"id": "1234abcd-12ab-34cd-56ef-1234567890ab", "arn": _KEY_ARN,
                                          "description": "the shop's data", "enable_key_rotation": True}),
        _resource("aws_ssm_parameter", "token", {"name": "/shop/token", "type": "SecureString", "key_id": _KEY_ARN,
                                                 "arn": f"arn:aws:ssm:{_REGION}:{_ACCOUNT}:parameter/shop/token"}),
        _resource("aws_iam_role", "gate", {"id": "gate-role", "name": "gate-role",
                                           "arn": f"arn:aws:iam::{_ACCOUNT}:role/gate-role"}),
        _resource("aws_lambda_function", "gate", {
            "id": "shop-gate", "function_name": "shop-gate", "runtime": "python3.12", "arn": _function_arn("shop-gate"),
            "invoke_arn": _invoke_arn("shop-gate"), "role": f"arn:aws:iam::{_ACCOUNT}:role/gate-role"}),
        _resource("aws_apigatewayv2_authorizer", "gate", {
            "id": "auth-1", "api_id": "api-123", "authorizer_type": "REQUEST", "name": "gate",
            "authorizer_uri": _invoke_arn("shop-gate")}),
        _resource("aws_ecr_repository", "worker", {
            "id": "shop/worker", "name": "shop/worker", "repository_url": _IMAGE_REPOSITORY,
            "arn": f"arn:aws:ecr:{_REGION}:{_ACCOUNT}:repository/shop/worker"}),
        _resource("aws_iam_role", "worker", {"id": "worker-role", "name": "worker-role",
                                             "arn": f"arn:aws:iam::{_ACCOUNT}:role/worker-role"}),
        _resource("aws_lambda_function", "worker", {
            "id": "payments-worker", "function_name": "payments-worker", "package_type": "Image", "runtime": "",
            "image_uri": f"{_IMAGE_REPOSITORY}:0123456789abcdef", "arn": _function_arn("payments-worker"),
            "role": f"arn:aws:iam::{_ACCOUNT}:role/worker-role"}),
        _resource("aws_lambda_event_source_mapping", "worker", {
            "id": "map-2", "function_name": "payments-worker",
            "event_source_arn": f"arn:aws:sqs:{_REGION}:{_ACCOUNT}:payment-jobs"}),
        _resource("aws_iam_role_policy", "worker", {"role": "worker-role", "name": "worker-data", "policy": json.dumps({
            "Statement": [{"Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage"],
                           "Resource": [queue["arn"], dead_arn]}]})}),
    ]
    if alias:
        state["resources"].append(_resource("aws_kms_alias", "data", {
            "name": "alias/shop-data", "target_key_arn": _KEY_ARN, "target_key_id": "1234abcd-12ab-34cd-56ef-1234567890ab"}))
    return state


def _edges_by_name(imported: dict) -> set[tuple[str, str, str]]:
    names = {item["id"]: item["name"] for item in imported["nodes"] + imported["groups"]}
    return {(names[edge["from"]], edge["type"], names[edge["to"]]) for edge in imported["edges"]}


def test_an_api_calls_the_function_its_integration_and_its_authorizer_point_at_by_invoke_address():
    imported = import_state(_with_payments())
    assert ("shop-http", "calls", "shop-api") in _edges_by_name(imported)  # the integration, given the invoke address
    assert ("shop-http", "calls", "shop-gate") in _edges_by_name(imported)  # the authorizer
    authorizer = next(edge for edge in imported["edges"] if edge["provenance"]["source"].endswith("aws_apigatewayv2_authorizer.gate"))
    assert authorizer["provenance"] == {"kind": "imported", "source": "terraform:aws_apigatewayv2_authorizer.gate"}
    assert left_out(_with_payments()).get("aws_apigatewayv2_authorizer") is None  # it is a connection now, not a setting left out


def test_an_integration_that_names_the_function_itself_still_connects():
    state = _state()
    integration = next(r for r in state["resources"] if r["type"] == "aws_apigatewayv2_integration")
    integration["instances"][0]["attributes"]["integration_uri"] = _function_arn("shop-api")
    assert ("shop-http", "calls", "shop-api") in _edges_by_name(import_state(state))


def test_a_queue_depends_on_its_dead_letter_queue_and_says_which_it_is():
    imported = import_state(_with_payments())
    assert ("payment-jobs", "depends_on", "payment-jobs-dlq") in _edges_by_name(imported)
    queue = next(item for item in imported["nodes"] if item["name"] == "payment-jobs")
    assert queue["properties"]["dlq"] == "payment-jobs-dlq"
    assert "dlq" not in next(item for item in imported["nodes"] if item["name"] == "payment-jobs-dlq")["properties"]


def test_a_dead_letter_queue_is_not_consumed_from_just_because_a_policy_allows_receiving_from_it():
    edges = _edges_by_name(import_state(_with_payments()))
    assert ("payments-worker", "consumes_from", "payment-jobs") in edges  # the trigger and the policy agree
    assert ("payments-worker", "consumes_from", "payment-jobs-dlq") not in edges


def test_a_trigger_from_a_dead_letter_queue_is_still_consumption():
    state = _with_payments()
    state["resources"].append(_resource("aws_lambda_event_source_mapping", "dead", {
        "id": "map-3", "function_name": "payments-worker", "event_source_arn": f"arn:aws:sqs:{_REGION}:{_ACCOUNT}:payment-jobs-dlq"}))
    assert ("payments-worker", "consumes_from", "payment-jobs-dlq") in _edges_by_name(import_state(state))


def test_a_function_run_from_an_image_reads_the_repository_that_holds_it():
    imported = import_state(_with_payments())
    assert ("payments-worker", "reads", "shop/worker") in _edges_by_name(imported)
    worker = next(item for item in imported["nodes"] if item["name"] == "payments-worker")
    assert worker["properties"]["package_type"] == "image"


@pytest.mark.parametrize(("uri", "repository"), [
    (f"{_IMAGE_REPOSITORY}:v1", _IMAGE_REPOSITORY),
    (f"{_IMAGE_REPOSITORY}@sha256:" + "ab" * 32, _IMAGE_REPOSITORY),
    (f"{_IMAGE_REPOSITORY}:v1@sha256:" + "ab" * 32, _IMAGE_REPOSITORY),
    (_IMAGE_REPOSITORY, _IMAGE_REPOSITORY),
    ("registry.local:5000/team/app:v2", "registry.local:5000/team/app"),
    ("registry.local:5000/team/app", "registry.local:5000/team/app"),
])
def test_an_image_address_names_its_repository_without_a_tag_or_a_digest(uri, repository):
    from app.services.terraform_links import image_repository

    assert image_repository(uri) == repository


def test_an_image_in_a_repository_the_state_does_not_hold_makes_no_connection():
    state = _with_payments()
    worker = next(r for r in state["resources"] if r["type"] == "aws_lambda_function" and r["name"] == "worker")
    worker["instances"][0]["attributes"]["image_uri"] = "public.ecr.aws/lambda/python:3.12"
    assert not {edge for edge in _edges_by_name(import_state(state)) if edge[0] == "payments-worker" and edge[1] == "reads"}


def test_what_names_a_customer_key_depends_on_it():
    edges = _edges_by_name(import_state(_with_payments()))
    assert ("orders", "depends_on", "data key") in edges  # a table's encryption block
    assert ("/shop/token", "depends_on", "data key") in edges  # a parameter's key id


def test_a_key_in_another_account_makes_no_connection():
    state = _with_payments()
    next(r for r in state["resources"] if r["type"] == "aws_ssm_parameter")["instances"][0]["attributes"]["key_id"] = (
        "arn:aws:kms:ap-southeast-2:999999999999:key/not-in-this-state")
    assert ("/shop/token", "depends_on", "data key") not in _edges_by_name(import_state(state))


def test_a_key_is_named_by_its_alias_and_without_one_by_what_its_address_calls_it():
    plain = next(item for item in import_state(_with_payments())["nodes"] if item["type"] == "identity.kms@1")
    assert plain["name"] == "data key" and "key_alias" not in plain["properties"]  # the state has no alias: left empty
    named = next(item for item in import_state(_with_payments(alias=True))["nodes"] if item["type"] == "identity.kms@1")
    assert named["name"] == "shop-data" and named["properties"]["key_alias"] == "shop-data"
    assert ("orders", "depends_on", "shop-data") in _edges_by_name(import_state(_with_payments(alias=True)))


def test_a_property_the_state_leaves_empty_is_not_filled_in():
    worker = next(item for item in import_state(_with_payments())["nodes"] if item["name"] == "payments-worker")
    assert "runtime" not in worker["properties"]  # an image function has no runtime in the state; a repository scan reads it


def test_every_connection_an_attribute_can_make_is_one_the_library_allows(library):
    from app.services.terraform_links import _KEY_ATTRIBUTES

    pairs = [("service.function", "reads", "delivery.ecr"), ("messaging.sqs", "depends_on", "messaging.sqs"),
             ("edge.api-gateway", "calls", "service.function")]
    pairs += [(MAPPINGS[kind].type_ref.rsplit("@", 1)[0], "depends_on", "identity.kms") for kind in _KEY_ATTRIBUTES]
    for source, edge, target in pairs:
        assert edge in library[source]["edges"]["allowed_out"], (source, edge)
        assert edge in library[target]["edges"]["allowed_in"], (target, edge)


def test_the_payments_import_is_a_clone_that_resolves_against_the_node_library():
    assert resolve_clone(_as_clone(import_state(_with_payments(alias=True))), load_library())[1] == []


# -- a bucket made outside Terraform: the state names it only in a policy or a trail ----------------------------------------

_OUTSIDE = "arn:aws:s3:::made-by-a-script"


def _state_with_outside_bucket() -> dict:
    state = _lambda_with_policy([{"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": [f"{_OUTSIDE}/*"]}])
    state["resources"].append(_resource("aws_cloudtrail", "trail", {
        "name": "trail", "arn": f"arn:aws:cloudtrail:{_REGION}:{_ACCOUNT}:trail/trail", "s3_bucket_name": "made-by-a-script"}))
    return state


def _bucket_in_clone(imported: dict) -> dict:
    return {**_as_clone(imported), "nodes": [*imported["nodes"], {
        "id": "scan_bucket", "type": "data.s3@1", "name": "made-by-a-script", "environment": "prod",
        "properties": {"bucket_name": "made-by-a-script", "arn": _OUTSIDE, "region": _REGION}}]}


def test_a_connection_to_a_bucket_the_state_does_not_hold_waits_and_is_not_a_dangling_edge():
    from app.services.terraform_state import import_state_with_pending

    imported = import_state_with_pending(_state_with_outside_bucket())
    assert {(item["type"], item["arn"]) for item in imported["pending"]} == {("reads", _OUTSIDE), ("writes", _OUTSIDE)}
    assert len(imported["pending"]) == 3  # the function reads and writes it, and the trail writes to it
    assert "pending" not in import_state(_state_with_outside_bucket())
    ends = {item["id"] for item in imported["nodes"] + imported["groups"]}
    assert all(edge["from"] in ends and edge["to"] in ends for edge in imported["edges"])


def test_once_another_source_has_put_the_bucket_in_the_clone_the_waiting_connections_are_made():
    from app.services.terraform_state import import_state_with_pending, with_pending_resolved

    imported = import_state_with_pending(_state_with_outside_bucket())
    resolved = with_pending_resolved(imported, _bucket_in_clone(imported))
    assert "pending" not in resolved
    names = {item["id"]: item["name"] for item in imported["nodes"] + imported["groups"]} | {"scan_bucket": "made-by-a-script"}
    made = {(names[e["from"]], e["type"], names[e["to"]]) for e in resolved["edges"]}
    assert {("shop-api", "reads", "made-by-a-script"), ("shop-api", "writes", "made-by-a-script"),
            ("trail", "writes", "made-by-a-script")} <= made
    assert with_pending_resolved(imported, _as_clone(imported))["edges"] == imported["edges"]  # no bucket yet: nothing is made

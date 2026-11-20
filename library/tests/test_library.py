"""The library covers both templates and the proving ground, and the validator holds every type to the same bar."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from library.attack import build
from library.resolve import load_library, resolve_clone
from library.validate import validate_paths

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.clone.json"))
S3 = ROOT / "types" / "data.s3.yaml"

PROVING_GROUND = {
    "shop HTTP API", "gatekeeper", "shop-api", "payments-worker", "payments-worker images", "payment-jobs",
    "payment-jobs-dlq", "customers", "orders", "receipts", "webhook signing secret", "service log groups",
    "proving-ground trail", "cloudtrail logs", "shop-api role", "payments-worker role", "gatekeeper role",
    "pg-deployer", "pg-whitehats-readonly", "whitehats-proving-ground", "deploy workflow", "monthly budget",
}


def _load(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text())


@pytest.fixture(scope="module")
def library() -> dict[str, dict]:
    return load_library()


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.name)
def test_every_node_and_group_resolves_to_a_real_type(path: Path, library):
    document = json.loads(path.read_text())
    resolved, problems = resolve_clone(document, library)
    assert problems == []
    assert len(resolved) == len(document["nodes"]) + len(document["groups"])
    assert not any(library[row.type_ref.rsplit("@", 1)[0]].get("generic") for row in resolved)


def test_the_three_examples_are_the_templates_and_the_proving_ground():
    assert [path.name for path in EXAMPLES] == ["proving-ground.clone.json", "template-a.clone.json", "template-b.clone.json"]


def test_the_proving_ground_example_holds_every_piece_of_the_estate():
    names = {node["name"] for node in _load("proving-ground.clone.json")["nodes"]}
    assert PROVING_GROUND <= names


def test_a_generic_node_does_not_count_as_resolved(library):
    document = _load("proving-ground.clone.json")
    document["nodes"][1]["type"] = "generic.network@1"
    _, problems = resolve_clone(document, library)
    assert problems == ["n_http_api (shop HTTP API): resolves only to the Generic type generic.network"]


def test_a_type_the_library_does_not_hold_is_named(library):
    document = _load("proving-ground.clone.json")
    document["nodes"][1]["type"] = "edge.kong@1"
    _, problems = resolve_clone(document, library)
    assert problems == ["n_http_api (shop HTTP API): type 'edge.kong' is not in the library"]


def test_a_pinned_version_must_match_the_library(library):
    document = _load("template-a.clone.json")
    repo = next(node for node in document["nodes"] if node["type"].startswith("repo.bitbucket@"))
    repo["type"] = "repo.bitbucket@1"
    _, problems = resolve_clone(document, library)
    assert problems == [f"{repo['id']} ({repo['name']}): pins repo.bitbucket version 1, the library has version 3"]


@pytest.mark.parametrize("key, value, message", [
    ("colour", "blue", "edge.api-gateway has no property 'colour'"),
    ("api_type", "soap", "'api_type' = \"soap\" is not a valid choice of http, rest, websocket"),
    ("throttling_rps", "ten", "'throttling_rps' = \"ten\" is not a valid number"),
    ("access_logging", "yes", "'access_logging' = \"yes\" is not a valid boolean"),
])
def test_property_values_must_fit_the_type(library, key, value, message):
    document = _load("proving-ground.clone.json")
    document["nodes"][1]["properties"][key] = value
    _, problems = resolve_clone(document, library)
    assert problems == [f"n_http_api (shop HTTP API): {message}"]


def test_any_node_may_say_where_it_is_for_routing(library):
    document = _load("proving-ground.clone.json")
    document["nodes"][1]["properties"].update({
        "arn": "arn:aws:apigateway:ap-southeast-2::/apis/abc123", "account": "111122223333",
        "region": "ap-southeast-2", "tags": {"team": "shop"}, "resource_group": "", "repo": "", "pipeline": "",
        "url": "https://abc123.execute-api.ap-southeast-2.amazonaws.com",
    })
    _, problems = resolve_clone(document, library)
    assert problems == []


@pytest.mark.parametrize("key, value, message", [
    ("account", 111122223333, "'account' = 111122223333 is not a valid string"),
    ("tags", ["team"], "'tags' = [\"team\"] is not a valid map of names to text"),
    ("url", "ftp://host", "'url' = \"ftp://host\" is not a valid url"),
])
def test_routing_properties_must_still_fit(library, key, value, message):
    document = _load("proving-ground.clone.json")
    document["nodes"][1]["properties"][key] = value
    _, problems = resolve_clone(document, library)
    assert problems == [f"n_http_api (shop HTTP API): {message}"]


def test_a_secret_property_is_bound_as_a_credential_not_written_in(library):
    document = _load("template-b.clone.json")
    stripe = next(node for node in document["nodes"] if node["type"] == "saas.stripe@1")
    stripe["properties"] = {"credential": "vault://tenant/fintech/stripe"}
    _, problems = resolve_clone(document, library)
    assert problems == [f"{stripe['id']} (Stripe): 'credential' is a secret; bind it as the node's credential, not as a property"]


def test_an_edge_must_point_the_way_both_types_allow(library):
    document = _load("proving-ground.clone.json")
    document["edges"].append({"from": "n_http_api", "to": "n_shop_api", "type": "routes_through"})
    _, problems = resolve_clone(document, library)
    assert problems == ["n_http_api -routes_through-> n_shop_api: service.function cannot receive a 'routes_through' edge"]


def test_a_generic_relation_is_allowed_where_a_typed_edge_is_not(library):
    document = _load("proving-ground.clone.json")
    document["edges"].append({"from": "n_shop_api", "to": "n_http_api", "type": "publishes_to"})
    _, blocked = resolve_clone(document, library)
    assert blocked == ["n_shop_api -publishes_to-> n_http_api: edge.api-gateway cannot receive a 'publishes_to' edge"]
    document["edges"][-1]["type"] = "generic_relation"
    _, problems = resolve_clone(document, library)
    assert problems == []


def test_a_clone_that_fails_its_schema_is_not_resolved(library):
    document = _load("proving-ground.clone.json")
    del document["nodes"][0]["environment"]
    resolved, problems = resolve_clone(document, library)
    assert resolved == []
    assert any("environment" in problem for problem in problems)


# ------------------------------------------------------------ the validator


def _copy_s3(tmp_path: Path, edit=None, name: str = "data.s3.yaml") -> Path:
    path = tmp_path / name
    shutil.copy(S3, path)
    if edit:
        path.write_text(edit(path.read_text()))
    return path


def test_the_whole_library_passes_every_check():
    assert validate_paths(sorted((ROOT / "types").glob("*.yaml")), whole_library=True) == []


def test_a_retired_attack_id_is_refused_with_its_replacement(tmp_path: Path):
    path = _copy_s3(tmp_path, lambda text: text.replace("[T1530,", "[T1562.008,"))
    assert validate_paths([path]) == [
        f"{path}: attack_techniques: 'T1562.008' (Disable or Modify Cloud Logs) is retired; use T1685.002"]


def test_an_attack_id_mitre_never_published_is_refused(tmp_path: Path):
    path = _copy_s3(tmp_path, lambda text: text.replace("[T1530,", "[T1999,"))
    assert validate_paths([path]) == [f"{path}: attack_techniques: 'T1999' is not in ATT&CK 20.0"]


def test_the_file_name_must_be_the_type_id(tmp_path: Path):
    path = _copy_s3(tmp_path, name="s3.yaml")
    assert validate_paths([path]) == [f"{path}: the file must be named data.s3.yaml"]


def test_a_missing_deploy_guide_is_refused(tmp_path: Path):
    path = _copy_s3(tmp_path, lambda text: text.replace("guides/data.s3.md", "guides/data.s3-missing.md"))
    assert validate_paths([path]) == [f"{path}: deploy guide guides/data.s3-missing.md does not exist"]


def test_a_deploy_guide_outside_the_guides_folder_is_refused(tmp_path: Path):
    path = _copy_s3(tmp_path, lambda text: text.replace("guides/data.s3.md", "guides/../schemas/clone.schema.json"))
    assert validate_paths([path]) == [f"{path}: deploy_guide_template must point inside guides/"]


def test_a_guide_without_a_rollback_section_is_refused(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    shutil.copytree(ROOT / "schemas", lib / "schemas")
    shutil.copytree(ROOT / "data", lib / "data")
    (lib / "guides").mkdir()
    guide = (ROOT / "guides" / "data.s3.md").read_text().replace("## Rollback", "## Undo")
    (lib / "guides" / "data.s3.md").write_text(guide)
    monkeypatch.setattr("library.validate.library_root", lambda: lib)
    path = _copy_s3(tmp_path)
    assert validate_paths([path]) == [f"{path}: deploy guide guides/data.s3.md has no '## Rollback' section"]


def test_a_property_key_may_appear_only_once(tmp_path: Path):
    path = _copy_s3(tmp_path, lambda text: text.replace("  - key: versioning", "  - key: region"))
    assert validate_paths([path]) == [f"{path}: properties: key 'region' appears more than once"]


def test_every_family_needs_exactly_one_generic_type():
    types = sorted((ROOT / "types").glob("*.yaml"))
    without_data = [path for path in types if path.name != "generic.data.yaml"]
    assert validate_paths(without_data, whole_library=True) == [
        "family data needs exactly one Generic type named generic.data, found none"]


def test_only_a_generic_type_may_use_the_generic_prefix(tmp_path: Path):
    types = sorted((ROOT / "types").glob("*.yaml"))
    impostor = _copy_s3(tmp_path, lambda text: text.replace("id: data.s3", "id: generic.s3"), name="generic.s3.yaml")
    assert validate_paths([*types, impostor], whole_library=True) == [
        "generic.s3: only a Generic type may use the generic. prefix"]


# ------------------------------------------------------------ the ATT&CK reference


def test_the_attack_reference_records_what_replaced_a_revoked_technique():
    bundle = {"objects": [
        {"type": "x-mitre-collection", "id": "c", "x_mitre_version": "99.0"},
        {"type": "attack-pattern", "id": "a1", "name": "Old", "revoked": True,
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1000"}]},
        {"type": "attack-pattern", "id": "a2", "name": "New",
         "external_references": [{"source_name": "mitre-attack", "external_id": "T2000"}]},
        {"type": "relationship", "id": "r", "relationship_type": "revoked-by", "source_ref": "a1", "target_ref": "a2"},
    ]}
    assert build(copy.deepcopy(bundle)) == {"attack_version": "99.0", "techniques": {
        "T1000": {"name": "Old", "retired": True, "replaced_by": "T2000"},
        "T2000": {"name": "New", "retired": False, "replaced_by": None},
    }}

"""Clone documents accept the two design templates and reject broken ones."""

import copy
import json
from pathlib import Path

import pytest

from library.clone_document import validate_clone, vault_problem

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

TEMPLATE_A_GROUPS = {
    "Entra tenant",
    "subscription platform-prod",
    "subscription platform-nonprod",
    "resource group prod",
    "resource group nonprod",
    "hub VNet",
    "spoke VNet",
    "AKS cluster",
    "corporate network",
}
TEMPLATE_A_NODES = {
    "Azure Front Door",
    "Azure DNS",
    "APIM",
    "patient-api",
    "scheduling-api",
    "nudges-service",
    "admin-portal",
    "wearable-ingest",
    "report-generator",
    "clinician-web",
    "Azure SQL appointments",
    "Cosmos DB wearable",
    "Blob lab reports",
    "Blob PHI",
    "FHIR service",
    "Azure Cache for Redis",
    "AI Search",
    "Service Bus",
    "Event Hubs",
    "Event Grid",
    "Entra ID",
    "Entra External ID",
    "managed identities",
    "Key Vault",
    "Azure OpenAI",
    "Langfuse",
    "repo patient-api",
    "repo scheduling-api",
    "repo nudges-service",
    "repo admin-portal",
    "repo wearable-ingest",
    "repo report-generator",
    "repo clinician-web",
    "repo bicep",
    "Bitbucket Pipelines",
    "ACR",
    "Application Insights",
    "Log Analytics",
    "Activity Log",
    "NSG flow logs",
    "Microsoft Sentinel",
    "Twilio",
    "SendGrid",
    "wearable platform",
    "Stripe",
    "consultant laptops",
    "client staff",
    "patients",
}
TEMPLATE_A_PATHS = {
    "Azure P2S VPN into the hub",
    "FortiClient into the firm network",
    "public APIs for SaaS",
}

TEMPLATE_B_GROUPS = {
    "AWS Organization",
    "account prod",
    "account staging",
    "account security",
    "account shared-services",
    "prod VPC",
    "staging VPC",
    "prod private subnet",
    "prod EKS cluster",
}
TEMPLATE_B_NODES = {
    "Route 53",
    "CloudFront",
    "API Gateway",
    "ALB",
    "accounts-api",
    "payments-api",
    "ledger-service",
    "kyc-service",
    "web-app",
    "webhook-handlers",
    "reconciliation-job",
    "back-office",
    "Aurora Postgres",
    "DynamoDB sessions",
    "ElastiCache Redis",
    "S3 statements",
    "S3 KYC documents",
    "Redshift",
    "SQS payment jobs",
    "SNS notifications",
    "EventBridge",
    "MSK Kafka",
    "Okta",
    "Cognito",
    "IAM roles",
    "Secrets Manager",
    "KMS",
    "repo payments-api",
    "repo accounts-api",
    "repo ledger-service",
    "repo kyc-service",
    "repo web-app",
    "repo webhook-handlers",
    "repo reconciliation-job",
    "repo back-office",
    "GitHub Actions",
    "ECR",
    "Terraform Cloud",
    "CloudWatch Logs",
    "CloudTrail",
    "VPC Flow Logs",
    "GuardDuty",
    "Datadog",
    "Stripe",
    "Plaid",
    "QuickBooks Online",
    "Persona",
    "Twilio",
    "Segment",
    "Bedrock",
    "Phoenix",
}
TEMPLATE_B_PATHS = {
    "AWS Client VPN to staging",
    "customer-hosted Runner for prod",
    "public APIs for SaaS",
}


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def _names(document: dict, key: str) -> set[str]:
    return {item["name"] for item in document[key]}


def minimal_clone() -> dict:
    """One of each collection, valid, so a single edit is the only defect."""
    return {
        "schema_version": 1,
        "clone": "minimal",
        "environment": "dev",
        "library_version": "2026.10",
        "version": {"number": 1, "author": "tester", "reason": "fixture"},
        "groups": [{
            "id": "g_app",
            "type": "boundary.vpc@1",
            "name": "app vpc",
            "kind": "vpc",
            "environment": "dev",
        }],
        "access_paths": [{
            "id": "ap_public",
            "name": "public APIs",
            "kind": "public-api",
            "environment": "dev",
        }],
        "credential_refs": [{
            "id": "cr_app",
            "vault_uri": "vault://tenant/minimal/app",
            "purpose": "test connection",
        }],
        "nodes": [{
            "id": "n_api",
            "type": "service.backend@1",
            "name": "api",
            "environment": "dev",
            "location": "g_app",
            "access_path": "ap_public",
            "credential": "cr_app",
            "properties": {"runtime": "python 3.12", "db_password": "vault://tenant/minimal/db"},
            "provenance": {"kind": "drawn", "source": "test"},
        }],
        "edges": [{"from": "n_api", "to": "g_app", "type": "contained_in"}],
    }


def test_template_a_lists_every_node_from_the_design():
    document = _load("template-a.clone.json")
    assert validate_clone(document) == []
    assert TEMPLATE_A_GROUPS <= _names(document, "groups")
    assert TEMPLATE_A_NODES <= _names(document, "nodes")
    assert TEMPLATE_A_PATHS <= _names(document, "access_paths")


def test_template_b_lists_every_node_from_the_design():
    document = _load("template-b.clone.json")
    assert validate_clone(document) == []
    assert TEMPLATE_B_GROUPS <= _names(document, "groups")
    assert TEMPLATE_B_NODES <= _names(document, "nodes")
    assert TEMPLATE_B_PATHS <= _names(document, "access_paths")


def test_clone_schema_validator_is_built_once():
    from library.clone_document import _validator

    assert _validator() is _validator()


def test_minimal_clone_is_valid():
    assert validate_clone(minimal_clone()) == []


def test_missing_type_is_rejected():
    document = minimal_clone()
    del document["nodes"][0]["type"]
    errors = validate_clone(document)
    assert any("type" in error and "required" in error for error in errors)


def test_unknown_edge_type_is_rejected():
    document = minimal_clone()
    document["edges"][0]["type"] = "teleports_to"
    errors = validate_clone(document)
    assert any("teleports_to" in error for error in errors)


def test_an_edge_may_say_where_it_came_from_and_only_in_the_three_ways():
    document = minimal_clone()
    document["edges"][0]["provenance"] = {"kind": "imported", "source": "terraform:aws_lambda_function.shop"}
    assert validate_clone(document) == []
    document["edges"][0]["provenance"] = {"kind": "guessed", "source": "x"}
    assert any("guessed" in error for error in validate_clone(document))


def test_secret_value_in_a_property_is_rejected():
    document = minimal_clone()
    document["nodes"][0]["properties"]["build_note"] = "sk_live_notarealkey0001"
    errors = validate_clone(document)
    assert any("build_note" in error for error in errors)


def test_connection_string_with_a_password_is_rejected():
    document = minimal_clone()
    document["nodes"][0]["properties"]["endpoint"] = "postgres://app:s3cret@db.internal/ledger"
    assert any("endpoint" in error for error in validate_clone(document))


def test_ordinary_url_is_allowed():
    document = minimal_clone()
    document["nodes"][0]["properties"]["url"] = "https://github.com/acme/payments-api"
    assert validate_clone(document) == []


def _with_property(key: str, value: object) -> dict:
    document = minimal_clone()
    document["nodes"][0]["properties"][key] = value
    return document


def _rejects_property(key: str, value: str) -> None:
    errors = validate_clone(_with_property(key, value))
    assert any(key in error for error in errors)
    assert value not in "\n".join(errors)


@pytest.mark.parametrize("key", ["secret_key", "passphrase", "connection_string", "credentials"])
def test_secret_named_property_must_be_a_vault_reference(key: str):
    _rejects_property(key, "plain-value")


def test_secret_named_property_accepts_a_vault_reference():
    document = _with_property("secret_key", "vault://tenant/minimal/signing")
    assert validate_clone(document) == []


def test_aws_temporary_key_is_rejected():
    _rejects_property("build_note", "ASIAY34FZKBOKMUTVV7A")


def test_github_fine_grained_pat_is_rejected():
    _rejects_property("build_note", "github_pat_" + "a" * 22 + "_" + "b" * 59)


def test_github_server_token_is_rejected():
    _rejects_property("build_note", "ghs_" + "a" * 36)


def test_stripe_restricted_live_key_is_rejected():
    _rejects_property("build_note", "rk_live_" + "a" * 24)


def test_stripe_restricted_test_key_is_rejected():
    _rejects_property("build_note", "rk_test_" + "b" * 24)


def test_json_web_token_is_rejected():
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    _rejects_property("build_note", token)


def test_azure_account_key_is_rejected():
    _rejects_property("build_note", "AccountKey=" + "A" * 88)


def test_azure_shared_access_key_is_rejected():
    _rejects_property("build_note", "SharedAccessKey=" + "B" * 44)


def test_azure_sas_signature_is_rejected():
    _rejects_property("build_note", "https://dev.blob.core.windows.net/c/o?sig=" + "C" * 44)


def test_runtime_note_is_allowed():
    assert validate_clone(_with_property("runtime", "node 20")) == []


def test_token_endpoint_name_is_allowed():
    document = _with_property("token_endpoint", "https://login.example/token")
    assert validate_clone(document) == []


def test_max_tokens_name_is_allowed():
    assert validate_clone(_with_property("max_tokens", 20)) == []


def test_vault_traversal_is_rejected():
    assert vault_problem("vault://tenant/acme/../other/key") == "has an empty or parent segment"
    document = minimal_clone()
    document["nodes"][0]["notes"] = "vault://tenant/acme/../other/key"
    assert any("parent segment" in error for error in validate_clone(document))


def test_vault_empty_segment_is_rejected():
    assert vault_problem("vault://tenant/acme//key") == "has an empty or parent segment"
    document = minimal_clone()
    document["credential_refs"][0]["vault_uri"] = "vault://tenant/acme//key"
    assert validate_clone(document)


def test_ipv4_route_is_allowed():
    document = minimal_clone()
    document["access_paths"][0]["routes"] = ["10.2.0.0/16"]
    assert validate_clone(document) == []


def test_ipv6_route_is_allowed():
    document = minimal_clone()
    document["access_paths"][0]["routes"] = ["2001:db8::/32"]
    assert validate_clone(document) == []


def test_route_octet_out_of_range_is_rejected():
    document = minimal_clone()
    document["access_paths"][0]["routes"] = ["999.1.1.1/24"]
    assert any("999.1.1.1/24" in error for error in validate_clone(document))


def test_route_prefix_out_of_range_is_rejected():
    document = minimal_clone()
    document["access_paths"][0]["routes"] = ["10.0.0.0/99"]
    assert any("10.0.0.0/99" in error for error in validate_clone(document))


def test_vault_wrong_prefix_is_rejected():
    assert vault_problem("vault://other/acme/key") == "must start with vault://tenant/<company>/"
    document = minimal_clone()
    document["credential_refs"][0]["vault_uri"] = "vault://other/acme/key"
    assert validate_clone(document)


def test_missing_environment_is_rejected():
    document = minimal_clone()
    del document["nodes"][0]["environment"]
    errors = validate_clone(document)
    assert any("environment" in error and "required" in error for error in errors)


def test_duplicate_id_is_rejected():
    document = minimal_clone()
    extra = copy.deepcopy(document["groups"][0])
    extra["name"] = "second copy"
    document["groups"].append(extra)
    errors = validate_clone(document)
    assert any("duplicate id" in error and "g_app" in error for error in errors)


def test_vault_reference_is_not_treated_as_a_secret_value():
    document = minimal_clone()
    document["nodes"][0]["properties"]["db_password"] = "vault://tenant/minimal/db"
    assert validate_clone(document) == []


@pytest.mark.parametrize("value", ["vault://tenant/minimal/db", "cr_app"])
def test_credential_may_be_a_vault_uri_or_a_ref_id(value: str):
    document = minimal_clone()
    document["nodes"][0]["credential"] = value
    assert validate_clone(document) == []


def test_a_group_may_carry_properties_but_never_a_secret_value():
    document = minimal_clone()
    document["groups"][0]["properties"] = {"cloud": "aws", "cidr": ["10.0.0.0/16"]}
    assert validate_clone(document) == []
    document["groups"][0]["properties"]["admin_password"] = "hunter2-but-longer"
    assert validate_clone(document) != []


def test_where_an_item_is_drawn_is_part_of_the_clone():
    document = minimal_clone()
    document["nodes"][0]["layout"] = {"x": 24, "y": 48}
    document["groups"][0].update(layout={"x": 0, "y": 0, "width": 400, "height": 260}, owner="Network team", notes="Holds the app.")
    assert validate_clone(document) == []


@pytest.mark.parametrize("layout", [{"x": 1}, {"x": "left", "y": 2}, {"x": 1, "y": 2, "width": 0}, {"x": 1, "y": 2, "z": 3}])
def test_a_broken_layout_is_rejected(layout: dict):
    document = minimal_clone()
    document["nodes"][0]["layout"] = layout
    assert validate_clone(document) != []


def test_a_group_inside_itself_is_rejected():
    document = minimal_clone()
    first = document["groups"][0]
    second = {**first, "id": "g_second", "name": "second", "parent": first["id"]}
    first["parent"] = "g_second"
    document["groups"].append(second)
    errors = validate_clone(document)
    assert any("sits inside itself" in error for error in errors)

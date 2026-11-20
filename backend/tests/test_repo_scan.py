"""A repository's files become nodes and connections: services and their runtime, what they deploy, and how the pipeline reaches the cloud."""

import json

import pytest
from connector_contract import github
from connector_kit import HttpReplay, load
from library.resolve import load_library, resolve_clone
from library.secrets import find_secret_values
from repo_kit import edge_names, node, repository

from app.services.repo_scan import scan_repositories, scan_repository

TERRAFORM = '''
variable "region" { default = "eu-west-1" }
locals { api = "orders-api" }
data "aws_caller_identity" "current" {}

resource "aws_lambda_function" "api" {
  function_name = local.api
  runtime       = "python3.12"
  memory_size   = 512
  filename      = "${path.module}/../api/dist/api.zip"
  environment {
    variables = { TABLE = "orders", API_PASSWORD = "hunter2-not-a-real-value" }
  }
}

resource "aws_lambda_function" "worker" {
  function_name = "orders-worker"
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.worker.repository_url}:1"
}

resource "aws_ecr_repository" "worker" {
  name                 = "acme/orders-worker"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_sqs_queue" "jobs" {
  name                    = "orders-jobs"
  sqs_managed_sse_enabled = true
  redrive_policy          = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dead.arn, maxReceiveCount = 3 })
}

resource "aws_sqs_queue" "dead" { name = "orders-jobs-dlq" }

resource "aws_dynamodb_table" "orders" {
  name = "orders"
  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = "arn:aws:kms:eu-west-1:123456789012:key/abc"
  }
}

resource "aws_s3_bucket_versioning" "files" {
  bucket = "acme-${data.aws_caller_identity.current.account_id}-files"
  versioning_configuration { status = "Enabled" }
}

resource "aws_iam_role" "deployer" { name = "deployer" }
resource "aws_iam_role" "other" { name = "someone-else" }
'''
WORKFLOW = '''
name: deploy
on: { push: { branches: [main] } }
permissions: { contents: read, id-token: write }
jobs:
  deploy:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: aws-actions/configure-aws-credentials@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: { role-to-assume: "arn:aws:iam::123456789012:role/deployer" }
      - run: bash scripts/deploy.sh
'''
FILES = {
    "terraform/main.tf": TERRAFORM,
    "api/pyproject.toml": '[project]\nname = "orders-api"\nrequires-python = ">=3.12"\ndependencies = ["flask>=3"]\n',
    "api/uv.lock": '[[package]]\nname = "orders-api"\nversion = "1"\n[[package]]\nname = "flask"\nversion = "3.0.0"\n',
    "worker/pyproject.toml": '[project]\nname = "orders-worker"\nrequires-python = ">=3.12"\n',
    "worker/Dockerfile": "FROM public.ecr.aws/lambda/python:3.12-arm64@sha256:" + "ab" * 32 + "\nENV API_TOKEN=hunter2-not-a-real-value\n",
    "tools/pyproject.toml": '[project]\nname = "orders-tools"\n',
    "web/package.json": json.dumps({"name": "storefront", "engines": {"node": ">=20"}, "dependencies": {"react": "^18"}}),
    "web/Dockerfile": "FROM node:20-alpine\nCMD [\"node\", \"server.js\"]\n",
    ".github/workflows/deploy.yml": WORKFLOW,
    "scripts/deploy.sh": 'repository="acme/orders-worker"\ndocker push "registry/${repository}:1"\n',
}


@pytest.fixture(scope="module")
def found():
    return scan_repository(repository(FILES), environment="prod")


def test_every_function_the_terraform_declares_is_a_node_with_its_runtime(found):
    api, worker = node(found, "orders-api"), node(found, "orders-worker")
    assert api["type"] == worker["type"] == "service.function@2"
    assert api["properties"]["runtime"] == "python3.12" and api["properties"]["package_type"] == "zip"
    assert api["properties"]["memory_mb"] == 512 and api["properties"]["region"] == "eu-west-1"
    assert api["properties"]["environment_variables"] == ["API_PASSWORD", "TABLE"]  # names, never values
    assert worker["properties"]["package_type"] == "image"
    assert worker["properties"]["runtime"] == "python3.12"  # an image function has none in Terraform: the Dockerfile says it


def test_a_function_says_what_its_code_is_written_in_from_the_project_that_holds_it(found):
    assert node(found, "orders-api")["notes"].startswith("Python >=3.12 code in api (from api/pyproject.toml): flask;")
    assert "1 other package(s) pinned in api/uv.lock" in node(found, "orders-api")["notes"]
    assert "Built from the image public.ecr.aws/lambda/python:3.12-arm64." in node(found, "orders-worker")["notes"]


def test_queues_tables_buckets_and_the_image_repository_are_found_with_what_the_files_say_of_them(found):
    assert node(found, "orders-jobs")["properties"].items() >= {"dlq": "orders-jobs-dlq", "encryption": "sse_sqs"}.items()
    assert "dlq" not in node(found, "orders-jobs-dlq")["properties"]
    assert node(found, "orders")["properties"] == {"table_name": "orders", "region": "eu-west-1", "point_in_time_recovery": True,
                                                   "encryption": "customer_managed"}
    repository_node = node(found, "acme/orders-worker")
    assert repository_node["properties"]["scan_on_push"] is True and repository_node["properties"]["tag_immutability"] is True


def test_a_bucket_named_with_the_account_is_not_drawn_while_no_account_is_known_and_the_scan_says_so():
    bare = scan_repository(repository({"terraform/main.tf": TERRAFORM}, workflows=False), environment="prod")
    assert not [item for item in bare.nodes if item["type"] == "data.s3@1"]
    assert bare.notes == ["aws_s3_bucket_versioning.files is declared but its name is not fully in the files, so it is not drawn"]


def test_a_bucket_named_with_the_account_is_drawn_once_a_workflow_names_the_account(found):
    # The deploy role's ARN names account 123456789012, which is the account the files leave to the provider.
    bucket = node(found, "acme-123456789012-files")
    assert bucket["type"] == "data.s3@1"
    assert bucket["properties"] == {"bucket_name": "acme-123456789012-files", "arn": "arn:aws:s3:::acme-123456789012-files",
                                    "region": "eu-west-1", "versioning": True}


def test_functions_are_deployed_from_the_repository_and_the_repository_is_built_by_its_workflows(found):
    edges = edge_names(found)
    assert {("orders-api", "deploys_from", "acme/shop"), ("orders-worker", "deploys_from", "acme/shop"),
            ("acme/shop", "built_by", "acme/shop workflows")} <= edges


def test_the_workflows_sign_in_as_the_role_the_terraform_declares_and_push_the_image_a_script_names(found):
    assert ("acme/shop workflows", "authenticates_via", "deployer") in edge_names(found)
    assert ("acme/shop workflows", "pushes_image_to", "acme/orders-worker") in edge_names(found)
    assert not [item for item in found.nodes if item["name"] == "someone-else"]  # a role no workflow assumes is not drawn
    assert node(found, "deployer")["properties"]["arn"] == "arn:aws:iam::123456789012:role/deployer"


def test_a_project_with_a_dockerfile_and_no_function_is_a_service_and_one_nothing_deploys_is_not_drawn(found):
    web = node(found, "storefront")
    assert web["type"] == "service.backend@1"
    assert web["properties"] == {"runtime": "nodejs20.x", "tech_stack": ["node >=20", "react"], "base_image": "node:20-alpine",
                                 "code_repo": "acme/shop", "code_folder": "web", "direct_dependencies": ["react"]}
    assert ("storefront", "deploys_from", "acme/shop") in edge_names(found)
    assert found.not_drawn == {"python project orders-tools, which nothing deploys": 1}


def test_the_workflows_node_gains_what_the_workflow_files_say(found):
    actions = node(found, "acme/shop workflows")["properties"]
    assert actions["cloud_auth"] == "oidc" and actions["actions_pinned_to_sha"] is True
    assert actions["default_token_permissions"] == "read" and actions["self_hosted_runners"] is False
    assert actions["workflows"] == [".github/workflows/deploy.yml"]  # what the connector found stays


def test_the_repository_node_lists_every_manifest_the_scan_read(found):
    assert node(found, "acme/shop")["properties"]["dependency_manifests"] == [
        "api/pyproject.toml", "tools/pyproject.toml", "web/package.json", "worker/pyproject.toml"]


def test_the_scan_says_what_it_did_not_draw(found):
    assert found.notes == []
    off = scan_repository(repository({**FILES, "terraform/extra.tf": 'module "n" {\n count = 0\n source = "./m"\n}\n',
                                      "terraform/m/main.tf": ""}), environment="prod")
    assert off.notes == ["module n is off by default, so what it declares is not drawn"]


def test_no_secret_value_in_any_file_reaches_a_node_an_edge_or_a_note(found):
    text = json.dumps([found.nodes, found.edges, found.not_drawn, found.notes])
    assert "hunter2" not in text
    assert find_secret_values([found.nodes, found.edges]) == []


def test_what_a_scan_finds_resolves_against_the_node_library_and_every_connection_is_one_it_allows(found):
    document = {"schema_version": 1, "clone": "scan", "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "scan", "reason": "scan"}, "access_paths": [], "credential_refs": [],
                "groups": [], "nodes": found.nodes, "edges": found.edges}
    assert resolve_clone(document, load_library())[1] == []
    ids = [item["id"] for item in found.nodes] + [item["id"] for item in found.edges]
    assert len(ids) == len(set(ids))


def test_the_same_files_scanned_twice_give_the_same_result(found):
    again = scan_repository(repository(FILES), environment="prod")
    assert (again.nodes, again.edges, again.not_drawn) == (found.nodes, found.edges, found.not_drawn)


def test_a_repository_with_no_workflows_still_gives_its_services():
    only = scan_repository(repository({k: v for k, v in FILES.items() if k != ".github/workflows/deploy.yml"}, workflows=False),
                           environment="prod")
    assert node(only, "orders-api") and not [item for item in only.edges if item["type"] in ("built_by", "authenticates_via")]


def test_every_item_says_which_file_of_the_repository_it_was_read_from(found):
    sources = {item["provenance"]["source"] for item in found.nodes + found.edges if item["provenance"]["source"].startswith("repo:")}
    assert "repo:acme/shop:terraform/main.tf" in sources and "repo:acme/shop:web/Dockerfile" in sources
    assert "repo:acme/shop:scripts/deploy.sh" in sources and "repo:acme/shop:.github/workflows/deploy.yml" in sources


# -- the real repository, as GitHub answered through the App ---------------------------------------------------------


@pytest.fixture(scope="module")
def proving_ground():
    replay = HttpReplay(load("github", "scan-ok"))
    source = github(replay, lambda _s: None)
    found = scan_repositories(source, environment="prod")
    replay.verify()
    return found


def test_scanning_the_proving_ground_finds_both_lambdas_their_runtimes_the_queue_tables_and_buckets(proving_ground):
    functions = {item["name"]: item["properties"] for item in proving_ground.nodes if item["type"] == "service.function@2"}
    assert {"shop-api", "payments-worker"} <= set(functions)
    assert functions["shop-api"]["runtime"] == "python3.12" and functions["shop-api"]["package_type"] == "zip"
    assert functions["payments-worker"]["runtime"] == "python3.12" and functions["payments-worker"]["package_type"] == "image"
    names = {item["type"]: [] for item in proving_ground.nodes}
    for item in proving_ground.nodes:
        names[item["type"]].append(item["name"])
    assert {"payment-jobs", "payment-jobs-dlq"} == set(names["messaging.sqs@1"])
    assert {"customers", "orders"} == set(names["data.dynamodb@1"])
    assert sorted(name.rpartition("-")[2] for name in names["data.s3@1"]) == ["artifacts", "receipts", "trail"]


def test_scanning_the_proving_ground_finds_the_deploys_from_and_built_by_connections(proving_ground):
    edges = edge_names(proving_ground)
    repo = "deva-ghb/whitehats-proving-ground"
    assert {("shop-api", "deploys_from", repo), ("payments-worker", "deploys_from", repo),
            ("shop-api-authorizer", "deploys_from", repo), (repo, "built_by", f"{repo} workflows")} <= edges
    assert (f"{repo} workflows", "authenticates_via", "pg-deployer") in edges
    assert (f"{repo} workflows", "pushes_image_to", "whitehats-proving-ground/payments-worker") in edges


def test_the_proving_ground_scan_ties_the_gatekeeper_to_its_code_through_the_terraform(proving_ground):
    notes = {item["name"]: item.get("notes", "") for item in proving_ground.nodes if item["type"] == "service.function@2"}
    assert notes["shop-api-authorizer"].startswith("Python >=3.12 code in gatekeeper (from gatekeeper/pyproject.toml)")
    assert notes["shop-api"].startswith("Python >=3.12 code in shop (from shop/pyproject.toml)")
    assert proving_ground.not_drawn == {"python project customer-loader, which nothing deploys": 1}
    assert proving_ground.notes == ["deva-ghb/whitehats-proving-ground: module network_addon is off by default, so what it declares is not drawn"]


def test_the_proving_ground_scan_resolves_against_the_library_holds_no_secret_and_reads_only(proving_ground):
    document = {"schema_version": 1, "clone": "scan", "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "scan", "reason": "scan"}, "access_paths": [], "credential_refs": [],
                "groups": [], "nodes": proving_ground.nodes, "edges": proving_ground.edges}
    assert resolve_clone(document, load_library())[1] == []
    assert find_secret_values([proving_ground.nodes, proving_ground.edges, proving_ground.notes]) == []
    methods = {call["method"] for call in load("github", "scan-ok")["calls"]}
    assert methods == {"GET", "POST"}
    assert [call["url"] for call in load("github", "scan-ok")["calls"] if call["method"] == "POST"] == [
        "https://api.github.com/app/installations/12345678/access_tokens"]  # the App's sign-in is the one POST: it changes nothing

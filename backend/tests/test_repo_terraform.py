"""Reading Terraform far enough to name what it declares: what is known is used, what is not stays unknown."""

import pytest

from app.services.repo_terraform import read_terraform

ROOT = {
    "infra/variables.tf": '''
variable "region" { default = "eu-west-1" }
variable "network" {
  type    = bool
  default = false
}
variable "token" {
  sensitive = true
  default   = "hunter2-not-a-real-token"
}
variable "no_default" {}
''',
    "infra/main.tf": '''
locals {
  project = "shop"
  queue   = "${local.project}-jobs"
  loop_a  = local.loop_b
  loop_b  = local.loop_a
  account = data.aws_caller_identity.current.account_id
  bucket  = "${local.project}-${local.account}-files"
  picked  = var.no_default != null ? var.no_default : "fallback"
  escaped = "$${not_a_reference}"
}
data "aws_caller_identity" "current" {}

module "worker" {
  source     = "./modules/worker"
  queue_name = local.queue
  region     = var.region
  secret     = var.token
}

module "extra" {
  count  = var.network ? 1 : 0
  source = "./modules/worker"
}

module "remote" {
  source = "terraform-aws-modules/s3-bucket/aws"
}
''',
    "infra/modules/worker/main.tf": '''
variable "queue_name" {}
variable "region" { default = "us-east-1" }
variable "secret" { default = "also-not-a-real-secret" }

resource "aws_sqs_queue" "jobs" {
  name       = var.queue_name
  tags_all   = aws_sqs_queue.other.arn
  kms_key    = var.secret
  computed   = md5(var.queue_name)
}

resource "aws_sqs_queue" "other" {
  name = "other-${var.region}"
}

output "queue" { value = aws_sqs_queue.jobs.name }
''',
}


def _tree(account: str | None = "111122223333", extra: dict[str, str] | None = None):
    return read_terraform({**ROOT, **(extra or {})}, account=account)


def _queue(tree, label: str):
    return next(item for item in tree.declared() if item.kind == "aws_sqs_queue" and item.label == label)


def test_a_name_is_followed_through_a_local_a_module_input_and_a_variable():
    assert _queue(_tree(), "jobs").value("name") == "shop-jobs"


def test_a_variable_default_is_used_when_nothing_is_passed_in_and_an_input_wins_when_one_is():
    assert _queue(_tree(), "other").value("name") == "other-eu-west-1"  # the module is given the root's region


def test_a_module_whose_count_is_zero_is_off_and_says_so():
    tree = _tree()
    assert [item for item in tree.declared() if item.module == ("extra",)] == []
    assert tree.switched_off == ["extra"]


def test_a_module_from_outside_the_repository_is_reported_and_not_read():
    tree = _tree()
    tree.declared()
    assert tree.not_read == ["remote"]


def test_a_sensitive_variable_is_never_read_even_for_its_default():
    tree = _tree()
    jobs = _queue(tree, "jobs")
    assert jobs.value("kms_key") is None
    assert tree.roots()[0].variable("token", 0) is None


def test_what_only_the_provider_knows_stays_unknown():
    jobs = _queue(_tree(), "jobs")
    assert jobs.value("computed") is None  # a function call
    assert jobs.value("tags_all") is None  # another resource's computed attribute


def test_text_with_one_unknown_piece_is_unknown_and_never_half_filled():
    scope = _tree(account=None).roots()[0]
    assert scope.evaluate("${local.bucket}") is None  # no account is known


def test_the_account_a_pipeline_signs_in_to_fills_the_account_the_files_leave_to_the_provider():
    scope = _tree(account="111122223333").roots()[0]
    assert scope.evaluate("${local.bucket}") == "shop-111122223333-files"


def test_a_choice_is_made_only_when_its_question_has_an_answer():
    scope = _tree().roots()[0]
    assert scope.evaluate("${local.picked}") == "fallback"
    assert scope.evaluate("${var.network ? \"on\" : \"off\"}") == "off"
    assert scope.evaluate("${var.unknown_thing ? \"on\" : \"off\"}") is None


def test_a_reference_that_loops_ends_as_unknown():
    assert _tree().roots()[0].evaluate("${local.loop_a}") is None


def test_dollar_dollar_brace_is_text_and_not_a_reference():
    assert _tree().roots()[0].evaluate('"${local.escaped}"') == "${not_a_reference}"


def test_a_file_the_parser_cannot_read_is_reported_and_the_rest_is_still_read():
    tree = _tree(extra={"infra/broken.tf": 'resource "aws_sqs_queue" {{{'})
    assert tree.unreadable == ["infra/broken.tf"]
    assert _queue(tree, "jobs").value("name") == "shop-jobs"


@pytest.mark.parametrize("raw", ["${1 + 1}", "${local.project[0]}", "${upper(local.project)}", "${module.worker}"])
def test_an_expression_this_reader_does_not_understand_is_unknown(raw):
    assert _tree().roots()[0].evaluate(raw) is None


def test_the_files_a_package_comes_from_are_found_through_modules_and_data_blocks():
    files = {
        "infra/main.tf": '''
data "archive_file" "gate" {
  source_file = "${path.module}/../gate/handler.py"
}
module "package" {
  source   = "./pkg"
  zip_path = data.archive_file.gate.output_path
}
resource "aws_lambda_function" "gate" {
  function_name = "gate"
  s3_key        = module.package.key
}
''',
        "infra/pkg/main.tf": '''
variable "zip_path" {}
resource "aws_s3_object" "zip" { source = var.zip_path }
output "key" { value = aws_s3_object.zip.key }
''',
    }
    tree = read_terraform(files)
    function = next(item for item in tree.declared() if item.kind == "aws_lambda_function")
    assert "gate/handler.py" in function.code_paths(("s3_key",))

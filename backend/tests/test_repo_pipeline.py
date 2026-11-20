"""Workflows say how a repository reaches the cloud, what it pins and which scripts it runs. Names only, never a value."""

from app.services.repo_pipeline import read_pipeline

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
DEPLOY = f'''
name: deploy
on:
  push:
    branches: [main]
permissions:
  contents: read
  id-token: write
jobs:
  deploy:
    runs-on: ubuntu-24.04
    env:
      PG_API_TOKEN: ${{{{ secrets.PG_API_TOKEN }}}}
      LITERAL_PASSWORD: hunter2-not-a-real-value
    steps:
      - uses: actions/checkout@{SHA} # v7
      - uses: aws-actions/configure-aws-credentials@{SHA}
        with:
          role-to-assume: arn:aws:iam::123456789012:role/deployer
          aws-region: eu-west-1
      - run: bash scripts/deploy.sh
      - run: |
          docker build -t app .
          docker push 123456789012.dkr.ecr.eu-west-1.amazonaws.com/team/app:latest
'''


def _pipeline(files: dict[str, str]):
    return read_pipeline(frozenset(files), files.get)


def test_a_deploy_workflow_gives_its_role_account_pinning_and_the_scripts_and_pushes_it_runs():
    files = {".github/workflows/deploy.yml": DEPLOY, "scripts/deploy.sh": "echo deploy"}
    pipeline = _pipeline(files)
    [workflow] = pipeline.workflows
    assert workflow.name == "deploy" and workflow.triggers == ("push",)  # YAML reads the key `on` as true
    assert pipeline.roles == ["arn:aws:iam::123456789012:role/deployer"] and pipeline.accounts == {"123456789012"}
    assert pipeline.cloud_auth == "oidc" and pipeline.pinned_to_sha is True
    assert workflow.permissions == "read"  # id-token write signs in to the cloud; it is not what the token may do
    assert workflow.scripts == ("scripts/deploy.sh",)
    assert len(workflow.pushes) == 1 and "docker push" in workflow.pushes[0]
    assert pipeline.secret_names == ["PG_API_TOKEN"]


def test_a_value_written_into_a_workflow_is_not_kept_anywhere():
    pipeline = _pipeline({".github/workflows/deploy.yml": DEPLOY})
    assert "hunter2" not in repr(pipeline)


def test_an_action_used_by_tag_or_by_a_short_commit_is_unpinned_and_the_workflow_says_which():
    text = ("on: push\njobs:\n  a:\n    runs-on: [self-hosted, linux]\n    steps:\n      - uses: actions/checkout@v4\n"
            "      - uses: actions/setup-python@deadbeef\n      - uses: ./local\n")
    pipeline = _pipeline({".github/workflows/ci.yml": text})
    assert pipeline.pinned_to_sha is False
    assert pipeline.workflows[0].unpinned == ("actions/checkout@v4", "actions/setup-python@deadbeef")  # a short commit can be reused
    assert pipeline.workflows[0].self_hosted is True
    assert pipeline.workflows[0].permissions == "" and pipeline.cloud_auth == "none"


def test_a_workflow_that_uses_no_action_says_nothing_about_pinning():
    assert _pipeline({".github/workflows/ci.yml": "on: [push, pull_request]\njobs:\n  a:\n    steps:\n      - run: make\n"}).pinned_to_sha is None


def test_a_stored_cloud_key_is_found_by_the_name_of_the_secret_it_is_kept_in():
    text = "on: push\njobs:\n  a:\n    steps:\n      - uses: aws-actions/configure-aws-credentials@v4\n        with:\n          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}\n"
    pipeline = _pipeline({".github/workflows/deploy.yml": text})
    assert pipeline.cloud_auth == "stored_keys" and pipeline.secret_names == ["AWS_ACCESS_KEY_ID"]


def test_a_role_an_expression_fills_in_is_not_a_known_role():
    text = "on: push\njobs:\n  a:\n    steps:\n      - uses: aws-actions/configure-aws-credentials@v4\n        with:\n          role-to-assume: ${{ vars.ROLE }}\n"
    assert _pipeline({".github/workflows/deploy.yml": text}).roles == []


def test_the_built_in_token_is_not_a_secret_a_person_set_and_a_build_push_action_counts_as_a_push():
    text = ("on: push\njobs:\n  a:\n    steps:\n      - uses: docker/build-push-action@v6\n        with:\n          push: true\n"
            "          tags: reg.example/team/app:1\n          token: ${{ secrets.GITHUB_TOKEN }}\n")
    workflow = _pipeline({".github/workflows/build.yaml": text}).workflows[0]
    assert workflow.secrets == () and workflow.pushes == ("reg.example/team/app:1",)


def test_a_workflow_that_is_not_yaml_is_reported_and_the_others_are_still_read():
    pipeline = _pipeline({".github/workflows/bad.yml": "a: [unclosed", ".github/workflows/ok.yml": "on: push\njobs: {}\n",
                          "docs/workflows/not-here.yml": "on: push"})
    assert pipeline.unreadable == (".github/workflows/bad.yml",) and [w.path for w in pipeline.workflows] == [".github/workflows/ok.yml"]

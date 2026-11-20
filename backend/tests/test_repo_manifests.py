"""Manifests, lock files and Dockerfiles become projects: language, version, dependencies, and the image that runs them."""

import json

import pytest

from app.services.repo_manifests import dependency_name, dockerfiles, image_runtime, read_projects

PYPROJECT = '''
[project]
name = "shop-api"
requires-python = ">=3.12"
dependencies = ["Requests[security]>=2.31", "pydantic==2.7.0 ; python_version >= '3.12'", "uvicorn"]
'''
UV_LOCK = '''
version = 1
[[package]]
name = "shop-api"
version = "0.1.0"
[[package]]
name = "requests"
version = "2.32.0"
[[package]]
name = "pydantic"
version = "2.7.0"
'''


def _projects(files: dict[str, str]):
    return read_projects(frozenset(files), files.get)


def test_a_pyproject_gives_its_name_language_version_and_direct_dependencies_by_name():
    [project], skipped = _projects({"shop/pyproject.toml": PYPROJECT})
    assert (project.folder, project.name, project.language, project.version) == ("shop", "shop-api", "python", ">=3.12")
    assert project.dependencies == ("pydantic", "requests", "uvicorn")
    assert project.stack == ["python >=3.12", "pydantic", "requests", "uvicorn"]
    assert skipped == []


def test_a_lock_file_pins_every_package_but_the_project_itself():
    [project], _ = _projects({"shop/pyproject.toml": PYPROJECT, "shop/uv.lock": UV_LOCK})
    assert project.lock_file == "shop/uv.lock"
    assert project.locked == ("pydantic==2.7.0", "requests==2.32.0")


def test_a_poetry_project_lists_its_dependencies_by_name_and_python_is_the_language():
    text = '[tool.poetry]\nname = "etl"\n[tool.poetry.dependencies]\npython = "^3.11"\nPandas = "^2"\nclick = "^8"\n'
    [project], _ = _projects({"pyproject.toml": text})
    assert (project.folder, project.version, project.dependencies) == ("", "^3.11", ("click", "pandas"))


def test_requirements_beside_a_pyproject_are_not_a_second_project_but_alone_they_are_one():
    reqs = "# pinned\nflask==3.0.0\n--index-url https://user:hunter2-not-real@packages.example/simple\n-r other.txt\nGunicorn>=21\n"
    assert len(_projects({"app/pyproject.toml": PYPROJECT, "app/requirements.txt": reqs})[0]) == 1
    [project], _ = _projects({"app/requirements.txt": reqs})
    assert project.name == "app" and project.dependencies == ("flask", "gunicorn")
    assert "hunter2" not in repr(project)  # an index address with a login is an option line, and is never read


def test_a_package_json_gives_node_its_engine_and_its_locked_packages():
    package = json.dumps({"name": "web", "engines": {"node": ">=20"}, "dependencies": {"react": "^18", "axios": "^1"}})
    lock = json.dumps({"packages": {"": {"name": "web"}, "node_modules/react": {"version": "18.3.1"},
                                    "node_modules/a/node_modules/b": {"version": "1.0.0"}}})
    [project], _ = _projects({"web/package.json": package, "web/package-lock.json": lock})
    assert (project.language, project.version, project.dependencies) == ("node", ">=20", ("axios", "react"))
    assert project.locked == ("b==1.0.0", "react==18.3.1")


@pytest.mark.parametrize("files", [{"x/pyproject.toml": "this is [not toml"}, {"x/package.json": "{nope"},
                                   {"x/pyproject.toml": "[tool.ruff]\nline-length = 120\n"}, {"x/package.json": "[]"}])
def test_a_manifest_that_holds_no_project_gives_none(files):
    assert _projects(files) == ([], [])


def test_manifests_this_scan_does_not_read_are_listed_so_nothing_is_silently_missed():
    _, skipped = _projects({"svc/go.mod": "module x", "svc/Cargo.toml": "[package]"})
    assert skipped == ["svc/Cargo.toml", "svc/go.mod"]


def test_a_file_too_big_to_read_is_listed_as_not_read():
    projects, skipped = read_projects(frozenset({"big/pyproject.toml"}), lambda path: None)
    assert projects == [] and skipped == ["big/pyproject.toml"]


def test_the_last_stage_of_a_dockerfile_is_the_image_that_runs():
    text = ("# build\nFROM --platform=linux/amd64 golang:1.22 AS build\nRUN go build \\\n   -o /app\n"
            "FROM public.ecr.aws/lambda/python:3.12-arm64@sha256:" + "ab" * 32 + "\nCOPY --from=build /app /app\n")
    [image] = dockerfiles(frozenset({"worker/Dockerfile"}), {"worker/Dockerfile": text}.get)
    assert image.base.startswith("public.ecr.aws/lambda/python:3.12-arm64@sha256:")
    assert image.runtime == "python3.12"


def test_a_dockerfile_from_scratch_has_no_base_and_is_not_listed():
    assert dockerfiles(frozenset({"Dockerfile"}), {"Dockerfile": "FROM scratch\nCOPY app /app\n"}.get) == []


@pytest.mark.parametrize(("image", "runtime"), [
    ("public.ecr.aws/lambda/python:3.12-arm64", "python3.12"),
    ("public.ecr.aws/lambda/nodejs:20", "nodejs20.x"),
    ("python:3.11-slim", "python3.11"),
    ("registry.local:5000/team/node:18-alpine", "nodejs18.x"),
    ("python:latest", ""), ("python", ""), ("ubuntu:24.04", ""), ("scratch", ""),
])
def test_an_image_says_its_runtime_only_when_it_is_a_language_image_with_a_version(image, runtime):
    assert image_runtime(image) == runtime


@pytest.mark.parametrize(("line", "name"), [("requests>=2", "requests"), ("Django[argon2]==5", "django"),
                                            ("pkg @ https://x.example/pkg.zip", "pkg"), ("A_b-c ; python_version<'3.9'", "a_b-c")])
def test_a_requirement_names_its_package(line, name):
    assert dependency_name(line) == name

"""A repository held in memory, for the scan tests: the same RepositoryFiles the GitHub connector hands the scan."""

from __future__ import annotations

from app.connectors.github import RepositoryFiles

FULL_NAME = "acme/shop"
URL = f"https://github.com/{FULL_NAME}"


def repository(files: dict[str, str], *, workflows: bool = True) -> RepositoryFiles:
    node = {"id": "github_repo_0123456789abcdef", "type": "repo.github@1", "name": FULL_NAME, "environment": "shared",
            "provenance": {"kind": "imported", "source": "github"},
            "properties": {"url": URL, "auth_method": "github_app", "default_branch": "main", "repo": FULL_NAME,
                           "branch_protection": False, "codeowners": False, "branches": ["main"],
                           "dependency_manifests": sorted(path for path in files if path.endswith("pyproject.toml"))}}
    actions = {"id": "github_actions_0123456789abcdef", "type": "delivery.github-actions@1", "name": f"{FULL_NAME} workflows",
               "environment": "shared", "provenance": {"kind": "imported", "source": "github"},
               "properties": {"repository": URL, "workflows": sorted(p for p in files if p.startswith(".github/workflows/")),
                              "url": f"{URL}/actions", "repo": FULL_NAME}} if workflows else None
    return RepositoryFiles(FULL_NAME, frozenset(files), files.get, node, actions)


def edge_names(found) -> set[tuple[str, str, str]]:
    names = {node["id"]: node["name"] for node in found.nodes}
    return {(names[edge["from"]], edge["type"], names[edge["to"]]) for edge in found.edges}


def node(found, name: str) -> dict:
    return next(item for item in found.nodes if item["name"] == name)

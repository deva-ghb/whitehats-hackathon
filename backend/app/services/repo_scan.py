"""Scan one repository's files into the nodes and connections of an estate (P5.2).

Reads the Terraform, the manifests and lock files, the Dockerfiles and the workflows. The result is the same kind of
thing as a Terraform state import (nodes, groups, edges), and goes through the same review. Pure: the files come in
through `RepositoryFiles.read`, so a recorded repository and a live one are scanned by the same code.
"""

from __future__ import annotations

import posixpath
from collections.abc import Collection
from dataclasses import dataclass, field, replace

from ..connectors.github import GitHub, RepositoryFiles
from .repo_estate import CODE_ATTRIBUTES, Estate, Placed, declared_estate
from .repo_manifests import Project, dockerfiles, read_projects
from .repo_pipeline import Pipeline, read_pipeline
from .repo_terraform import Tree, read_terraform
from .terraform_state import ident

FUNCTION_TYPE = "service.function@2"
SERVICE_TYPE = "service.backend@1"
ROLE_TYPE = "identity.iam-role@1"
ECR_TYPE = "delivery.ecr@1"
INFRASTRUCTURE_FOLDER = "terraform/"  # code a function's Terraform names inside here is Terraform, not the function's own


@dataclass
class Found:
    """What a scan found: nodes and edges, and in plain words what was read and not drawn."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    not_drawn: dict[str, int] = field(default_factory=dict)  # what was read and left out, by what it is
    notes: list[str] = field(default_factory=list)

    def incoming(self) -> dict[str, list[dict]]:
        return {"nodes": self.nodes, "groups": [], "edges": self.edges}


def scan_repository(repo: RepositoryFiles, *, environment: str) -> Found:
    return _Scan(repo, environment).run()


def scan_repositories(source: GitHub, *, environment: str, only: Collection[str] | None = None) -> Found:
    """Scan every repository the installation can see, or only the named ones. Only reads."""
    total = Found()
    repositories = [repo for repo in source.repositories() if only is None or repo["full_name"] in only]
    for repo in repositories:
        found = scan_repository(source.repository_files(repo), environment=environment)
        total.nodes += found.nodes
        total.edges += found.edges
        for key, count in found.not_drawn.items():
            total.not_drawn[key] = total.not_drawn.get(key, 0) + count
        total.notes += [f"{repo['full_name']}: {note}" for note in found.notes]
    return total


class _Scan:
    def __init__(self, repo: RepositoryFiles, environment: str):
        self.repo, self.environment = repo, environment
        self.source = f"repo:{repo.full_name}"
        self.found = Found()
        self.pipeline = read_pipeline(repo.paths, repo.read)
        account = next(iter(self.pipeline.accounts)) if len(self.pipeline.accounts) == 1 else None
        files = {path: text for path in sorted(repo.paths) if path.endswith(".tf") and (text := repo.read(path)) is not None}
        self.tree: Tree = read_terraform(files, account=account)
        projects, self.unread = read_projects(repo.paths, repo.read)
        images = dockerfiles(repo.paths, repo.read)
        self.projects = [replace(p, dockerfile=next((i for i in images if posixpath.dirname(i.path) == p.folder), None))
                         for p in projects]
        self.estate: Estate = declared_estate(self.tree.declared(), environment=environment, region=self.tree.region(),
                                              source=self.source)

    def run(self) -> Found:
        repo_node, actions = self._repository_nodes()
        self.found.nodes += [repo_node, *([actions] if actions else [])]
        if actions is not None:
            self._edge("built_by", repo_node, actions, self._workflow_path())
        used = self._services(repo_node)
        for project in self.projects:
            if project not in used:
                key = f"{project.language} project {project.name}, which nothing deploys"
                self.found.not_drawn[key] = self.found.not_drawn.get(key, 0) + 1
        self._workflows(actions)
        self.found.nodes += [placed.body for placed in self.estate.nodes if placed.body["type"] != ROLE_TYPE]
        self.found.notes += self._notes()
        return self.found

    # -- the repository and its workflows --------------------------------------------------

    def _repository_nodes(self) -> tuple[dict, dict | None]:
        """The nodes the GitHub connector made, with what the files add to them."""
        node = {**self.repo.node, "properties": {**self.repo.node["properties"]}}
        manifests = {project.manifest for project in self.projects}
        if manifests:
            node["properties"]["dependency_manifests"] = sorted({*node["properties"].get("dependency_manifests", []), *manifests})
        if self.repo.actions is None:
            return node, None
        actions = {**self.repo.actions, "properties": {**self.repo.actions["properties"]}}
        actions["properties"].update(_pipeline_facts(self.pipeline))
        return node, actions

    def _workflow_path(self) -> str:
        return self.pipeline.workflows[0].path if self.pipeline.workflows else ""

    # -- services: a manifest or a Dockerfile, tied to what the Terraform deploys -----------

    def _services(self, repo_node: dict) -> list[Project]:
        used: list[Project] = []
        for placed in self.estate.of_type(FUNCTION_TYPE):
            project = self._project_for(placed)
            if project is not None:
                used.append(project)
                _describe_function(placed.body, project, self.repo.full_name)
            self._edge("deploys_from", placed.body, repo_node, placed.file)
        for project in self.projects:
            if project not in used and project.dockerfile is not None:
                service = self._service(project)
                self.found.nodes.append(service)
                self._edge("deploys_from", service, repo_node, project.dockerfile.path)
                used.append(project)
        return used

    def _project_for(self, placed: Placed) -> Project | None:
        """The project whose folder holds the function's code, as its Terraform says, else the project with its name."""
        for path in sorted(placed.declared.code_paths(CODE_ATTRIBUTES)) if placed.declared else []:
            holders = [p for p in self.projects if p.folder and not path.startswith(INFRASTRUCTURE_FOLDER)
                       and path.startswith(p.folder + "/")]
            if holders:
                return max(holders, key=lambda project: len(project.folder))
        return next((project for project in self.projects if project.name == placed.body["name"]), None)

    def _service(self, project: Project) -> dict:
        image = project.dockerfile
        return {"id": ident("n", f"{self.source}:service:{project.folder or project.name}"), "type": SERVICE_TYPE,
                "name": project.name, "environment": self.environment, "notes": _stack_text(project),
                "provenance": {"kind": "imported", "source": f"{self.source}:{image.path}"},
                "properties": {"runtime": image.runtime or project.language, "tech_stack": project.stack,
                               **({"packages": project.packages} if project.packages else {}),
                               **({"base_image": image.base} if image.base else {}),
                               **_code_place(self.repo.full_name, project)}}

    # -- workflows: how they reach the cloud -------------------------------------------------

    def _workflows(self, actions: dict | None) -> None:
        if actions is None:
            return
        for arn in self.pipeline.roles:
            role = self._role(arn)
            if role["id"] not in {node["id"] for node in self.found.nodes}:
                self.found.nodes.append(role)
            self._edge("authenticates_via", actions, role, self._signs_in(arn))
        pushed = self._pushed_images()
        for placed in self.estate.of_type(ECR_TYPE):
            name = placed.body["properties"].get("repository_name")
            if name in pushed:
                self._edge("pushes_image_to", actions, placed.body, pushed[name])

    def _role(self, arn: str) -> dict:
        """The role a workflow assumes: the one the Terraform declares under that name, else one made from the ARN."""
        name = arn.rpartition("/")[2]
        for placed in self.estate.of_type(ROLE_TYPE):
            if placed.body["name"] == name:
                return {**placed.body, "properties": {**placed.body["properties"], "arn": arn}}
        return {"id": ident("n", f"{self.source}:role:{name}"), "type": ROLE_TYPE, "name": name, "environment": self.environment,
                "provenance": {"kind": "imported", "source": f"{self.source}:{self._signs_in(arn)}"},
                "properties": {"role_name": name, "arn": arn}}

    def _signs_in(self, arn: str) -> str:
        return next((workflow.path for workflow in self.pipeline.workflows if arn in workflow.roles), "")

    def _pushed_images(self) -> dict[str, str]:
        """Image repositories that a workflow's step, or a script the step runs, pushes to, with the file that says so."""
        names = [placed.body["properties"]["repository_name"] for placed in self.estate.of_type(ECR_TYPE)
                 if "repository_name" in placed.body["properties"]]
        out: dict[str, str] = {}
        for workflow in self.pipeline.workflows:
            texts = [(workflow.path, text) for text in workflow.pushes]
            texts += [(script, body) for script in workflow.scripts
                      if (body := self.repo.read(script)) is not None and "docker push" in body]
            for path, text in texts:
                for name in names:
                    if name in text:
                        out.setdefault(name, path)
        return out

    def _edge(self, kind: str, source: dict, target: dict, file: str) -> None:
        self.found.edges.append({
            "id": ident("e", f"{kind}_{source['id']}_{target['id']}"), "from": source["id"], "to": target["id"], "type": kind,
            "provenance": {"kind": "imported", "source": f"{self.source}:{file}" if file else self.source}})

    def _notes(self) -> list[str]:
        notes = [f"module {name} is off by default, so what it declares is not drawn" for name in self.tree.switched_off]
        notes += [f"module {name} comes from outside the repository and was not read" for name in self.tree.not_read]
        notes += [f"{path} could not be read" for path in [*self.tree.unreadable, *self.pipeline.unreadable]]
        notes += [f"{path} is a manifest this scan does not read yet" for path in self.unread]
        notes += [f"{address} is declared but its name is not fully in the files, so it is not drawn" for address in self.estate.not_named]
        return notes


def _pipeline_facts(pipeline: Pipeline) -> dict:
    """The properties of a workflows node that the workflow files say. A fact the files do not give is left out."""
    if not pipeline.workflows:
        return {}
    facts: dict = {"cloud_auth": pipeline.cloud_auth, "self_hosted_runners": any(w.self_hosted for w in pipeline.workflows)}
    if pipeline.pinned_to_sha is not None:
        facts["actions_pinned_to_sha"] = pipeline.pinned_to_sha
    grants = {workflow.permissions for workflow in pipeline.workflows} - {""}
    if grants:
        facts["default_token_permissions"] = "write" if "write" in grants else "read"
    if pipeline.secret_names:
        facts["secret_names"] = pipeline.secret_names
    return facts


def _describe_function(body: dict, project: Project, repo: str) -> None:
    """Fill what the state leaves empty (an image function has no runtime) and say what the code is written in."""
    image = project.dockerfile
    if "runtime" not in body["properties"] and image is not None and image.runtime:
        body["properties"]["runtime"] = image.runtime
    body["notes"] = _stack_text(project)
    if project.packages:  # the exact versions the code uses, so a new advisory for one of them reaches this node (KB.1)
        body["properties"]["packages"] = project.packages
    if image is not None and image.base:  # the image it is built from, which the image scan reads (SC.1, VS.5c)
        body["properties"]["base_image"] = image.base
    body["properties"].update(_code_place(repo, project))


def _code_place(repo: str, project: Project) -> dict:
    """Where the node's code lives, so a scan plan can tell which service a changed file belongs to (SCAN.1), and the
    packages its manifest names, so its inventory can tell chosen parts from hidden ones (SCAN.2)."""
    place = {"code_repo": repo, "code_folder": project.folder} if project.folder else {"code_repo": repo}
    return {**place, "direct_dependencies": list(project.dependencies)}


def _stack_text(project: Project) -> str:
    language = f"{project.language.capitalize()} {project.version}".strip()
    packages = ", ".join(project.dependencies) if project.dependencies else "no third-party packages"
    lock = (f"{len(project.locked)} other package(s) pinned in {project.lock_file}" if project.locked
            else f"{project.lock_file} pins nothing else" if project.lock_file else "no lock file")
    image = f" Built from the image {project.dockerfile.base.partition('@')[0]}." if project.dockerfile else ""
    return f"{language} code in {project.folder or 'the top folder'} (from {project.manifest}): {packages}; {lock}.{image}"

"""Read a repository's GitHub Actions workflows: how it signs in to a cloud, what it pins, and which scripts it runs.

Only names and structure are kept. A `secrets.NAME` reference gives the name and never a value, and a value written
into a workflow is not copied anywhere.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import yaml

WORKFLOW_FOLDER = ".github/workflows"
CLOUD_SIGN_IN = "aws-actions/configure-aws-credentials"
IMAGE_BUILD = "docker/build-push-action"
BUILT_IN_SECRETS = frozenset({"GITHUB_TOKEN"})
SHA_LENGTH = 40  # a commit id, which is what pinning an action means


@dataclass(frozen=True)
class Workflow:
    path: str
    name: str
    triggers: tuple[str, ...]
    roles: tuple[str, ...]  # the roles it asks the cloud to let it assume, as written
    keys_stored: bool  # it signs in with an access key kept as a secret
    unpinned: tuple[str, ...]  # actions used by a name or tag instead of a commit
    pinned: int  # actions used by commit
    permissions: str  # "read" or "write" for what the workflow's own token may do, "" when it says nothing
    self_hosted: bool
    secrets: tuple[str, ...]
    scripts: tuple[str, ...]  # files of the repository its steps run
    pushes: tuple[str, ...] = field(default=())  # text of every step that pushes an image: commands and image tags


@dataclass(frozen=True)
class Pipeline:
    workflows: tuple[Workflow, ...]
    unreadable: tuple[str, ...]

    @property
    def roles(self) -> list[str]:
        return sorted({role for workflow in self.workflows for role in workflow.roles})

    @property
    def accounts(self) -> set[str]:
        """The cloud accounts the workflows sign in to, from the account part of each role's ARN."""
        return {parts[4] for role in self.roles if len(parts := role.split(":")) > 5 and parts[4].isdigit()}

    @property
    def cloud_auth(self) -> str:
        if any(workflow.keys_stored for workflow in self.workflows):
            return "stored_keys"
        return "oidc" if self.roles else "none"

    @property
    def pinned_to_sha(self) -> bool | None:
        """Whether every action is used by commit; None when the workflows use no action."""
        pinned = sum(workflow.pinned for workflow in self.workflows)
        unpinned = sum(len(workflow.unpinned) for workflow in self.workflows)
        return None if not (pinned or unpinned) else unpinned == 0

    @property
    def secret_names(self) -> list[str]:
        return sorted({name for workflow in self.workflows for name in workflow.secrets})


def read_pipeline(paths: frozenset[str], read: Callable[[str], str | None]) -> Pipeline:
    found, unreadable = [], []
    for path in sorted(paths):
        if posixpath.dirname(path) != WORKFLOW_FOLDER or not path.endswith((".yml", ".yaml")):
            continue
        text = read(path)
        document = _load(text) if text is not None else None
        if document is None:
            unreadable.append(path)
            continue
        found.append(_workflow(path, document, paths))
    return Pipeline(tuple(found), tuple(unreadable))


def _load(text: str) -> dict | None:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return document if isinstance(document, dict) else None


def _workflow(path: str, document: dict, paths: frozenset[str]) -> Workflow:
    steps = [step for job in _jobs(document) for step in job.get("steps") or [] if isinstance(step, dict)]
    uses = [(step["uses"], step.get("with") if isinstance(step.get("with"), dict) else {})
            for step in steps if isinstance(step.get("uses"), str)]
    roles = tuple(sorted({str(arguments["role-to-assume"]) for name, arguments in uses
                          if name.startswith(CLOUD_SIGN_IN) and _is_text(arguments.get("role-to-assume"))}))
    pinned = [name for name, _ in uses if _is_pinned(name)]
    unpinned = [name for name, _ in uses if not name.startswith(("./", "docker://")) and not _is_pinned(name)]
    return Workflow(
        path=path, name=str(document.get("name") or posixpath.basename(path)), triggers=_triggers(document),
        roles=roles, keys_stored=any("aws-access-key-id" in arguments for _, arguments in uses) or _names_key(document),
        unpinned=tuple(unpinned), pinned=len(pinned), permissions=_permissions(document.get("permissions")),
        self_hosted=any("self-hosted" in _labels(job.get("runs-on")) for job in _jobs(document)),
        secrets=tuple(sorted(set(_secret_names(document)) - BUILT_IN_SECRETS)),
        scripts=tuple(_scripts(steps, paths)), pushes=tuple(_pushes(steps, uses)),
    )


def _jobs(document: dict) -> list[dict]:
    jobs = document.get("jobs")
    return [job for job in jobs.values() if isinstance(job, dict)] if isinstance(jobs, dict) else []


def _triggers(document: dict) -> tuple[str, ...]:
    # YAML 1.1 reads the key `on` as the boolean true.
    trigger = document.get("on", document.get(True))
    if isinstance(trigger, str):
        return (trigger,)
    if isinstance(trigger, list):
        return tuple(str(item) for item in trigger)
    return tuple(sorted(str(key) for key in trigger)) if isinstance(trigger, dict) else ()


def _is_pinned(action: str) -> bool:
    name, _at, ref = action.partition("@")
    return bool(ref) and len(ref) == SHA_LENGTH and all(char in "0123456789abcdef" for char in ref.lower()) \
        and not name.startswith(("./", "docker://"))


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "${{" not in value  # a value an expression fills in is not known


def _labels(runs_on: object) -> list[str]:
    if isinstance(runs_on, str):
        return [runs_on]
    if isinstance(runs_on, list):
        return [str(item) for item in runs_on]
    return [str(item) for item in runs_on.get("labels") or []] if isinstance(runs_on, dict) else []


def _permissions(value: object) -> str:
    """What the workflow's token is allowed. Signing in with OIDC needs `id-token: write`, which is not a token permission."""
    if isinstance(value, str):
        return "write" if value == "write-all" else "read" if value == "read-all" else ""
    if not isinstance(value, dict) or not value:
        return ""
    granted = {name: level for name, level in value.items() if name != "id-token"}
    return "write" if "write" in granted.values() else "read"


def _names_key(document: dict) -> bool:
    return any(isinstance(text, str) and "AWS_ACCESS_KEY_ID" in text for text in _strings(document))


def _strings(value: object) -> Iterator[str | object]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    else:
        yield value


def _secret_names(document: dict) -> list[str]:
    """The NAME of every `secrets.NAME` an expression reads."""
    names = []
    for text in _strings(document):
        if not isinstance(text, str):
            continue
        at = text.find("secrets.")
        while at >= 0:
            end = at + len("secrets.")
            while end < len(text) and (text[end].isalnum() or text[end] == "_"):
                end += 1
            if end > at + len("secrets."):
                names.append(text[at + len("secrets."):end])
            at = text.find("secrets.", end)
    return names


def _scripts(steps: list[dict], paths: frozenset[str]) -> list[str]:
    """Files of the repository that a step's command names, in the order they are first run."""
    found: list[str] = []
    for step in steps:
        for word in str(step.get("run") or "").split():
            candidate = posixpath.normpath(word.strip("\"'")) if word[:1] not in "-$" else ""
            if candidate in paths and candidate not in found:
                found.append(candidate)
    return found


def _pushes(steps: list[dict], uses: list[tuple[str, dict]]) -> list[str]:
    """The text of every step that pushes an image: a `docker push` command, or the tags a build-and-push action is given."""
    out = [str(step["run"]) for step in steps if "docker push" in str(step.get("run") or "")]
    for name, arguments in uses:
        if name.startswith(IMAGE_BUILD) and arguments.get("push") in (True, "true"):
            out.append(str(arguments.get("tags") or ""))
    return out

"""Which projects a connection's data belongs to (checklist AP.3, the Scope part of the contract in 3A).

A connection is set up once per account and attached to projects, each with a scope. A scope
names the slices of the connection a project wants: AWS accounts, regions, tags or resource
groups; repositories; pipelines. A key that is absent does not narrow anything, so an empty
scope takes everything the connection reads. Pure: no database, so the rules are easy to test.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .base import Discovery

LIST_KEYS = ("accounts", "regions", "resource_groups", "repos", "pipelines")
SCOPE_KEYS = (*LIST_KEYS, "tags")

# What a scope may narrow, by connector category. A category not listed here cannot be attached.
SCOPE_KEYS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "Cloud logs": ("accounts", "regions", "tags", "resource_groups"),
    "Code": ("repos",),
    "CI/CD": ("repos", "pipelines"),
}
# Company-wide sources: every project of the account uses them, so they are never attached (spec 0A).
ACCOUNT_WIDE_CATEGORIES = ("Identity", "Endpoint", "Ticketing")


@dataclass(frozen=True)
class Resource:
    """One real thing a connection reads, named the same way in every project that holds it."""

    identity: str  # the ARN, the repository URL, the pipeline URL
    account: str = ""
    region: str = ""
    tags: Mapping[str, str] = field(default_factory=dict)
    resource_group: str = ""
    repo: str = ""
    pipeline: str = ""


def check_scope(category: str, raw: object) -> dict:
    """A scope as stored, or ValueError with a message a person can act on."""
    allowed = SCOPE_KEYS_BY_CATEGORY.get(category)
    if allowed is None:
        raise ValueError(f"A {category} connection is not attached to projects.")
    if not isinstance(raw, Mapping):
        raise ValueError("The scope must be a set of named filters.")  # noqa: TRY004 - bad input, answered as 400
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValueError(f"A {category} connection cannot be narrowed by {', '.join(unknown)}.")
    scope: dict = {}
    for key, value in raw.items():
        scope[key] = _tags(value) if key == "tags" else _names(key, value)
    return scope


def _names(key: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{key} must be a list of names.")
    return sorted({item.strip() for item in value})


def _tags(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
        isinstance(k, str) and k.strip() and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError("tags must map each tag name to the value it must have.")
    return {k.strip(): v for k, v in value.items()}


def in_scope(scope: Mapping, resource: Resource) -> bool:
    """True when the resource passes every filter the scope names."""
    singles = {"accounts": resource.account, "regions": resource.region,
               "resource_groups": resource.resource_group, "repos": resource.repo, "pipelines": resource.pipeline}
    for key in LIST_KEYS:
        wanted = scope.get(key)
        if wanted is not None and singles[key] not in wanted:
            return False
    tags = scope.get("tags") or {}
    return all(resource.tags.get(name) == value for name, value in tags.items())


def matching(attachments: Iterable[tuple[int, Mapping]], resource: Resource) -> list[int]:
    """The projects, among (project id, scope) attachments, that this resource belongs to."""
    return sorted({project_id for project_id, scope in attachments if in_scope(scope, resource)})


def resource_of(node: Mapping) -> Resource:
    """A discovered clone node as a Resource, read from its properties."""
    props = node.get("properties") or {}
    return Resource(
        identity=str(props.get("arn") or props.get("url") or node.get("id", "")),
        account=str(props.get("account", "")), region=str(props.get("region", "")),
        tags=dict(props.get("tags") or {}), resource_group=str(props.get("resource_group", "")),
        repo=str(props.get("repo", "")), pipeline=str(props.get("pipeline", "")),
    )


def split(discovery: Discovery, attachments: list[tuple[int, Mapping]]) -> dict[int, Discovery]:
    """Each attached project's share of a discovery. An edge goes where both its ends went."""
    nodes: dict[int, list[dict]] = {project_id: [] for project_id, _ in attachments}
    for node in discovery.nodes:
        for project_id in matching(attachments, resource_of(node)):
            nodes[project_id].append(node)
    out = {}
    for project_id, kept in nodes.items():
        ids = {node["id"] for node in kept}
        edges = [edge for edge in discovery.edges if edge["from"] in ids and edge["to"] in ids]
        out[project_id] = Discovery(nodes=kept, edges=edges)
    return out

"""Save and read a company's clone. Another company gets nothing back."""

from __future__ import annotations

from functools import cache

from library import LIBRARY_VERSION
from library.clone_document import validate_clone
from library.resolve import load_library, resolve_clone
from sqlalchemy.orm import Session

from ..models import AccessPath, Clone, CloneVersion, CredentialRef, Edge, Group, Node
from ..projects import main_project_id

_GRAPH = (
    (Node, "nodes"),
    (Edge, "edges"),
    (Group, "groups"),
    (AccessPath, "access_paths"),
    (CredentialRef, "credential_refs"),
)


@cache
def library() -> dict[str, dict]:
    """The node library ships with the app and only changes with a release, so it is read once."""
    return load_library()


def document_problems(document: dict, company: str) -> list[str]:
    """Why the Studio would refuse this document: a bad shape, another company's vault, or edges the library forbids."""
    errors = validate_clone(document) + other_company_vaults(document, company)
    if not errors:
        _resolved, errors = resolve_clone(document, library())
    return errors


def blank_document(slug: str, environment: str, author: str, reason: str) -> dict:
    """An empty clone at the current library version, the same shape the Studio starts a new clone with."""
    return {"schema_version": 1, "clone": slug, "environment": environment, "library_version": LIBRARY_VERSION,
            "version": {"number": 1, "author": author, "reason": reason},
            "nodes": [], "edges": [], "groups": [], "access_paths": [], "credential_refs": []}


def create_clone(
    db: Session, org_id: int, document: dict, author: str, reason: str, *, project_id: int | None = None,
) -> Clone:
    """Store the first version of a clone in one project of this company, Main unless another is named."""
    clone = Clone(
        org_id=org_id,
        project_id=project_id or main_project_id(db, org_id),
        slug=document["clone"],
        environment=document["environment"],
        library_version=document["library_version"],
        schema_version=document.get("schema_version", 1),
    )
    db.add(clone)
    db.flush()
    _add_version(db, clone, document, author, reason, number=1)
    db.commit()
    db.refresh(clone)
    return clone


def save_version(
    db: Session, org_id: int, clone_id: int, document: dict, author: str, reason: str,
) -> CloneVersion | None:
    """Append a version. Returns None when the clone is not this company's."""
    clone = _owned(db, org_id, clone_id)
    if clone is None:
        return None
    number = max(version.number for version in clone.versions) + 1
    version = _add_version(db, clone, document, author, reason, number)
    clone.environment = document["environment"]
    clone.library_version = document["library_version"]
    db.commit()
    db.refresh(version)
    return version


# The first version of a map the connections started says so (STUDIO.6); a map drawn by hand never does.
STARTED_FROM_CONNECTIONS = "map from its connections"


def from_connections(clone: Clone) -> bool:
    first = min(clone.versions, key=lambda version: version.number, default=None)
    return first is not None and first.reason.endswith(STARTED_FROM_CONNECTIONS)


def list_clones(db: Session, org_id: int, project_id: int) -> list[dict]:
    """Every clone in one project with its latest version: the map the connections draw first, then by name."""
    clones = db.query(Clone).filter_by(org_id=org_id, project_id=project_id).order_by(Clone.slug).all()
    out = []
    for clone in clones:
        latest = max(clone.versions, key=lambda version: version.number)
        out.append({"id": clone.id, "clone": clone.slug, "environment": latest.environment, "version": latest.number,
                    "author": latest.author, "reason": latest.reason, "from_connections": from_connections(clone)})
    return sorted(out, key=lambda entry: not entry["from_connections"])


def list_versions(db: Session, org_id: int, clone_id: int) -> list[dict] | None:
    """Version number, author, reason and time, oldest first. None when the clone is not this company's."""
    clone = _owned(db, org_id, clone_id)
    if clone is None:
        return None
    return [
        {"number": version.number, "author": version.author, "reason": version.reason,
         "created_at": version.created_at.isoformat(timespec="seconds") + "Z"}
        for version in clone.versions
    ]


def other_company_vaults(document: object, company: str) -> list[str]:
    """Vault references must name this company. The path shape is checked by validate_clone."""
    errors = []
    for path, text in _walk_text(document, ""):
        if not text.startswith("vault://tenant/"):
            continue
        parts = text.split("/")
        owner = parts[3] if len(parts) > 3 else ""
        if owner != company:
            where = f"{path}: " if path else ""
            errors.append(f"{where}vault reference belongs to another company")
    return errors


def read_clone(db: Session, org_id: int, clone_id: int, number: int | None = None) -> dict | None:
    """Return one version as a clone document, or None if this company does not own it."""
    clone = _owned(db, org_id, clone_id)
    if clone is None:
        return None
    version = _pick(clone, number)
    if version is None:
        return None
    return _document(clone, version, _rows(db, version.id))


def _owned(db: Session, org_id: int, clone_id: int) -> Clone | None:
    return db.query(Clone).filter_by(id=clone_id, org_id=org_id).one_or_none()


def _pick(clone: Clone, number: int | None) -> CloneVersion | None:
    if number is None:
        return max(clone.versions, key=lambda version: version.number)
    return next((version for version in clone.versions if version.number == number), None)


def _add_version(db: Session, clone: Clone, document: dict, author: str, reason: str, number: int) -> CloneVersion:
    version = CloneVersion(
        org_id=clone.org_id,
        project_id=clone.project_id,
        clone_id=clone.id,
        number=number,
        author=author,
        reason=reason,
        environment=document["environment"],
        library_version=document["library_version"],
        schema_version=document.get("schema_version", 1),
    )
    db.add(version)
    db.flush()
    _write_graph(db, clone.org_id, clone.project_id, version.id, document)
    return version


def _write_graph(db: Session, org_id: int, project_id: int, version_id: int, document: dict) -> None:
    for item in document.get("nodes", []):
        db.add(_node(org_id, project_id, version_id, item))
    for item in document.get("edges", []):
        db.add(_edge(org_id, project_id, version_id, item))
    for item in document.get("groups", []):
        db.add(_group(org_id, project_id, version_id, item))
    for item in document.get("access_paths", []):
        db.add(_path(org_id, project_id, version_id, item))
    for item in document.get("credential_refs", []):
        db.add(_credential(org_id, project_id, version_id, item))


def _node(org_id: int, project_id: int, version_id: int, item: dict) -> Node:
    return Node(
        org_id=org_id, project_id=project_id, clone_version_id=version_id, node_key=item["id"],
        type=item["type"], name=item["name"],
        environment=item["environment"], owner=item.get("owner") or "", location=item.get("location") or "",
        access_path=item.get("access_path") or "",
        credential=item.get("credential") or "", criticality=item.get("criticality") or "",
        data_classification=item.get("data_classification") or [], compliance_scope=item.get("compliance_scope") or [],
        properties=item.get("properties") or {}, provenance=item.get("provenance") or {},
        tags=item.get("tags") or {}, notes=item.get("notes") or "", last_confirmed_at=item.get("last_confirmed_at") or "",
        layout=item.get("layout") or {},
    )


def _edge(org_id: int, project_id: int, version_id: int, item: dict) -> Edge:
    return Edge(
        org_id=org_id, project_id=project_id, clone_version_id=version_id, from_key=item["from"], to_key=item["to"],
        type=item["type"], provenance=item.get("provenance") or {}, properties=item.get("properties") or {},
    )


def _group(org_id: int, project_id: int, version_id: int, item: dict) -> Group:
    return Group(
        org_id=org_id, project_id=project_id, clone_version_id=version_id, group_key=item["id"],
        type=item["type"], name=item["name"],
        kind=item["kind"], environment=item["environment"], parent_key=item.get("parent") or "",
        owner=item.get("owner") or "", provenance=item.get("provenance") or {}, notes=item.get("notes") or "",
        layout=item.get("layout") or {}, properties=item.get("properties") or {},
    )


def _path(org_id: int, project_id: int, version_id: int, item: dict) -> AccessPath:
    return AccessPath(
        org_id=org_id, project_id=project_id, clone_version_id=version_id, path_key=item["id"],
        name=item["name"], kind=item["kind"],
        environment=item["environment"], client=item.get("client") or "", routes=item.get("routes") or [],
        credential=item.get("credential") or "",
    )


def _credential(org_id: int, project_id: int, version_id: int, item: dict) -> CredentialRef:
    return CredentialRef(
        org_id=org_id, project_id=project_id, clone_version_id=version_id, ref_key=item["id"],
        vault_uri=item["vault_uri"],
        purpose=item.get("purpose") or "",
    )


def _rows(db: Session, version_id: int) -> dict[str, list]:
    found = {}
    for model, key in _GRAPH:
        found[key] = db.query(model).filter_by(clone_version_id=version_id).order_by(model.id).all()
    return found


def _walk_text(value: object, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            found.extend(_walk_text(item, f"{path}/{key}" if path else str(key)))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(_walk_text(item, f"{path}/{index}"))
        return found
    return []


def _document(clone: Clone, version: CloneVersion, rows: dict[str, list]) -> dict:
    return {
        "schema_version": version.schema_version,
        "clone": clone.slug,
        "environment": version.environment,
        "library_version": version.library_version,
        "version": {"number": version.number, "author": version.author, "reason": version.reason},
        "nodes": [_node_out(row) for row in rows["nodes"]],
        "edges": [_edge_out(row) for row in rows["edges"]],
        "groups": [_group_out(row) for row in rows["groups"]],
        "access_paths": [_path_out(row) for row in rows["access_paths"]],
        "credential_refs": [_credential_out(row) for row in rows["credential_refs"]],
    }


def _node_out(row: Node) -> dict:
    body = {"id": row.node_key, "type": row.type, "name": row.name, "environment": row.environment}
    _put(body, "owner", row.owner)
    _put(body, "location", row.location)
    _put(body, "access_path", row.access_path)
    _put(body, "credential", row.credential)
    _put(body, "criticality", row.criticality)
    if row.data_classification:
        body["data_classification"] = row.data_classification
    if row.compliance_scope:
        body["compliance_scope"] = row.compliance_scope
    if row.properties:
        body["properties"] = row.properties
    if row.provenance:
        body["provenance"] = row.provenance
    if row.tags:
        body["tags"] = row.tags
    _put(body, "notes", row.notes)
    _put(body, "last_confirmed_at", row.last_confirmed_at)
    if row.layout:
        body["layout"] = row.layout
    return body


def _edge_out(row: Edge) -> dict:
    body = {"from": row.from_key, "to": row.to_key, "type": row.type}
    if row.provenance:
        body["provenance"] = row.provenance
    if row.properties:
        body["properties"] = row.properties
    return body


def _group_out(row: Group) -> dict:
    body = {
        "id": row.group_key, "type": row.type, "name": row.name, "kind": row.kind, "environment": row.environment,
    }
    _put(body, "parent", row.parent_key)
    _put(body, "owner", row.owner)
    if row.provenance:
        body["provenance"] = row.provenance
    _put(body, "notes", row.notes)
    if row.layout:
        body["layout"] = row.layout
    if row.properties:
        body["properties"] = row.properties
    return body


def _path_out(row: AccessPath) -> dict:
    body = {"id": row.path_key, "name": row.name, "kind": row.kind, "environment": row.environment}
    _put(body, "client", row.client)
    if row.routes:
        body["routes"] = row.routes
    _put(body, "credential", row.credential)
    return body


def _credential_out(row: CredentialRef) -> dict:
    return {"id": row.ref_key, "vault_uri": row.vault_uri, "purpose": row.purpose}


def _put(body: dict, key: str, value: str) -> None:
    if value:
        body[key] = value

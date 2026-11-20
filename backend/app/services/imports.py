"""Keep an import's change set, take a person's decisions on it, and apply the accepted part to the clone (PG.3).

The comparison and the apply rules live in changesets.py. This module is the storage around them:
it never opens a Terraform file and never calls a cloud. Applying makes the next clone version
through the same checks as a save from the Studio.
"""

from __future__ import annotations

from collections import Counter

from library.secrets import find_secret_values
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import ChangeSet, Clone
from . import changesets
from .clones import document_problems, library, read_clone, save_version
from .terraform_state import with_pending_resolved

# The importer that owns each source's items: provenance sources start with this word.
OWNERS = {"terraform_state": ("terraform",), "connections": ("aws", "github", "cloudwatch"), "repository": ("repo",)}
EMPTY_DOCUMENT_REASON = "Started for an import"


class ImportRefused(Exception):
    """The import cannot go ahead. `problems` are plain sentences a person can act on."""

    def __init__(self, *problems: str):
        super().__init__(problems[0] if problems else "")
        self.problems = list(problems)


def is_generic(type_ref: str) -> bool:
    return bool(library().get(type_ref.rsplit("@", 1)[0], {}).get("generic"))


def declared_properties(type_ref: str) -> set[str]:
    return {prop["key"] for prop in library().get(type_ref.rsplit("@", 1)[0], {}).get("properties", [])}


def open_change_set(
    db: Session, clone: Clone, incoming: changesets.Incoming, *, source: str, label: str, author: str,
    left_out: dict[str, int] | None = None, owners: tuple[str, ...] | None = None,
) -> ChangeSet | None:
    """Compare an import with the clone's latest version and keep the difference. None when nothing differs.

    `owners` narrows whose items may be proposed for removal (one connection's own, not every connection's).
    """
    document = read_clone(db, clone.org_id, clone.id)
    if source == "connections":
        incoming = changesets.align(incoming, document)
    elif source == "repository":
        incoming = changesets.enrich(incoming, document)
    elif source == "terraform_state":
        # Matched by ARN in this direction too (R-055): what a connection discovered first is not added a second time.
        incoming = changesets.align(with_pending_resolved(incoming, document), document)
    items = changesets.build(incoming, document, owners=owners or OWNERS[source], is_generic=is_generic,
                             rejected=_rejected_before(db, clone.id))
    if not items:
        return None
    # The importers drop secret-looking values; this is the second lock on the same door.
    if find_secret_values(items):
        raise ImportRefused("The import holds a value that looks like a secret, so nothing was kept. "
                            "Secret values never go into a clone.")
    found = ChangeSet(
        org_id=clone.org_id, project_id=clone.project_id, clone_id=clone.id, source=source, label=label[:200],
        created_by=author[:120], base_version=document["version"]["number"], items=items, left_out=left_out or {},
    )
    db.add(found)
    db.commit()
    db.refresh(found)
    return found


def _rejected_before(db: Session, clone_id: int) -> dict[str, dict]:
    earlier = db.scalars(select(ChangeSet).where(ChangeSet.clone_id == clone_id, ChangeSet.status == "applied")
                         .order_by(ChangeSet.id)).all()
    return {item["key"]: item["item"] for found in earlier for item in found.items if item["decision"] == "reject"}


def list_change_sets(db: Session, org_id: int, project_id: int, clone_id: int | None = None) -> list[dict]:
    query = select(ChangeSet, Clone.slug).join(Clone, Clone.id == ChangeSet.clone_id).where(
        ChangeSet.org_id == org_id, ChangeSet.project_id == project_id)
    if clone_id is not None:
        query = query.where(ChangeSet.clone_id == clone_id)
    rows = db.execute(query.order_by(ChangeSet.id.desc())).all()
    return [summary(found, slug) for found, slug in rows]


def summary(found: ChangeSet, clone_name: str) -> dict:
    """One line per change set for a list: what it is, where it stands, how much is left to decide."""
    items = found.items
    return {
        "id": found.id, "clone_id": found.clone_id, "clone": clone_name, "source": found.source, "label": found.label,
        "created_by": found.created_by, "created_at": found.created_at.isoformat(), "status": found.status,
        "changes": len(items), "undecided": sum(1 for item in items if item["decision"] == "undecided"),
        "accepted": sum(1 for item in items if item["decision"] == "accept"),
        "rejected": sum(1 for item in items if item["decision"] == "reject"),
        "applied_version": found.applied_version,
    }


def detail(db: Session, found: ChangeSet) -> dict:
    """The whole change set for review: each item with what it waits for, and the clone's make-up before and after."""
    clone = db.get(Clone, found.clone_id)
    document = read_clone(db, found.org_id, found.clone_id)
    here = {entry["id"] for entry in document["nodes"] + document["groups"]}
    places = {entry["id"]: entry for entry in document["nodes"] + document["groups"]}
    places.update({item["item"]["id"]: item["item"] for item in found.items if item["kind"] != "edge"})
    names = _readable_names(places)
    coming = here | {item["item"]["id"] for item in found.items
                     if item["kind"] != "edge" and item["decision"] == "accept" and item["action"] != "remove"}
    items = [_view(item, coming, names) for item in found.items]
    body = summary(found, clone.slug)
    body.update({"items": items, "left_out": found.left_out, "base_version": found.base_version,
                 "clone_version": document["version"]["number"], "environment": clone.environment})
    if found.status == "open":
        after, _report = _result(document, found.items)
        body["now"], body["after"] = _makeup(document), _makeup(after)
    return body


def _readable_names(places: dict[str, dict]) -> dict[str, str]:
    """Each place's name, with its kind of thing added where two places share a name.

    A state can call an HTTP API and the function behind it by the same name; "shop-api calls shop-api" says nothing.
    """
    counts = Counter(entry["name"] for entry in places.values())
    kinds = library()

    def named(entry: dict) -> str:
        if counts[entry["name"]] == 1:
            return entry["name"]
        kind = kinds.get(entry["type"].rsplit("@", 1)[0], {}).get("display_name", entry["type"])
        return f"{entry['name']} ({kind})"

    return {ident: named(entry) for ident, entry in places.items()}


def _view(item: dict, coming: set[str], names: dict[str, str]) -> dict:
    needs = changesets.waits_for(item, coming) if item["action"] == "add" else []
    out = {**item, "waits_for": [names.get(need, need) for need in needs]}
    if item["kind"] == "edge":
        body = item["item"]
        out["ends"] = [names.get(body["from"], body["from"]), names.get(body["to"], body["to"])]
    return out


def _makeup(document: dict) -> dict:
    counts = changesets.provenance_counts(document)
    total = sum(counts.values())
    imported = counts["imported"] + counts["discovered"]
    return {"counts": counts, "total": total, "from_import": imported,
            "share": round(100 * imported / total, 1) if total else 0.0}


def _result(document: dict, items: list[dict]) -> tuple[dict, dict]:
    """The clone as it would be after every accepted item, and the counts."""
    return changesets.apply_accepted(document, items, declared=declared_properties, is_generic=is_generic)


def decide(db: Session, found: ChangeSet, keys: list[str], decision: str, type_ref: str | None) -> None:
    """Record accept, reject or undecided on the named items. A held item is accepted with a real library type."""
    _require_open(found)
    if type_ref is not None:
        chosen = library().get(type_ref.rsplit("@", 1)[0])
        if chosen is None or chosen.get("generic") or type_ref != f"{chosen['id']}@{chosen['version']}":
            raise ImportRefused(f"{type_ref} is not a type the node library holds, or it is a Generic type.")
    try:
        found.items = changesets.decide(found.items, keys, decision, type_ref)
    except KeyError as exc:
        raise ImportRefused(f"This import has no change called {exc.args[0]}.") from None
    except ValueError as exc:
        raise ImportRefused(str(exc)) from None
    db.commit()


def apply(db: Session, found: ChangeSet, *, company: str, author: str) -> dict:
    """Make the next clone version from everything accepted. Refused, with the reasons, when the library would refuse it."""
    _require_open(found)
    document = read_clone(db, found.org_id, found.clone_id)
    after, report = _result(document, found.items)
    if not (report["added"] or report["updated"] or report["removed"]):
        raise ImportRefused("Nothing is accepted yet. Accept at least one change, or discard this import.")
    problems = document_problems(after, company)
    if problems:
        raise ImportRefused(*problems)
    reason = (f"Import {found.label}: {report['added']} added, {report['updated']} updated, "
              f"{report['removed']} removed")
    version = save_version(db, found.org_id, found.clone_id, after, author, reason)
    found.items = changesets.settle_left_out(found.items, report.pop("left_out"))
    found.status, found.applied_version, found.applied_at = "applied", version.number, utcnow()
    db.commit()
    return {"clone_id": found.clone_id, "version": version.number, "report": report}


def discard(db: Session, found: ChangeSet) -> None:
    _require_open(found)
    found.status = "discarded"
    db.commit()


def _require_open(found: ChangeSet) -> None:
    if found.status != "open":
        raise ImportRefused(f"This import is already {found.status}.")

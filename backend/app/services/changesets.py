"""What an import would change in a clone, and what accepting part of it does.

An importer (Terraform state, a connection's discovery) hands over nodes, groups and edges.
Here they are compared with the clone as it stands and turned into one item per change, each
waiting for a person to accept or reject it. Nothing in this module reads or writes a
database: the caller keeps the items and the clone.

Rules that keep an import safe to repeat and safe next to hand work:
- an import only ever removes what an import put there (provenance), never what a person drew;
- an update changes the imported fields and leaves what a person added (criticality, notes, extra properties);
- an item the library has only a Generic type for is held: it cannot go into a clone until a person
  picks a real type (the clone save refuses Generic types), or rejects it;
- an item a person rejected, unchanged, is not proposed again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy

IMPORTED_KINDS = ("imported", "discovered")
DECISIONS = ("undecided", "accept", "reject")
_PLACES = ("group", "node")
_ROUTING = ("arn", "url", "account", "region", "tags", "resource_group", "repo", "pipeline")

Incoming = dict[str, list[dict]]
IsGeneric = Callable[[str], bool]


def align(incoming: Incoming, document: dict) -> Incoming:
    """Drop what the clone already holds under another id (the same ARN) and point connections at the clone's own ids.

    A Terraform state and a connection's discovery name the same resource differently; the ARN says they are one.
    """
    known = {}
    for field in ("groups", "nodes"):
        for body in document.get(field, []):
            arn = (body.get("properties") or {}).get("arn")
            if arn:
                known[arn] = body["id"]
    same = {}
    for field in ("groups", "nodes"):
        for body in incoming.get(field, []):
            arn = (body.get("properties") or {}).get("arn")
            if arn in known and known[arn] != body["id"]:
                same[body["id"]] = known[arn]
    if not same:
        return incoming
    edges = [{**edge, "from": same.get(edge["from"], edge["from"]), "to": same.get(edge["to"], edge["to"])}
             for edge in incoming.get("edges", [])]
    return {"groups": [body for body in incoming.get("groups", []) if body["id"] not in same],
            "nodes": [body for body in incoming.get("nodes", []) if body["id"] not in same],
            "edges": [edge for edge in edges if edge["from"] != edge["to"]]}


def enrich(incoming: Incoming, document: dict) -> Incoming:
    """Fit a repository scan to a clone that already holds some of what it found.

    A scan names a thing that a Terraform state or a connection may already have put in the clone: the same id, the same
    ARN, or the same kind of thing under the same name. Such a thing is not added again, and the scan does not overwrite
    it: it only fills what the clone's own item leaves empty (a worker's runtime, a note on what the code is written in).
    Connections are pointed at the clone's item.
    """
    existing = {}
    for field in ("groups", "nodes"):
        for body in document.get(field, []):
            props = body.get("properties") or {}
            existing.setdefault(("id", body["id"]), body)
            if props.get("arn"):
                existing.setdefault(("arn", props["arn"]), body)
            existing.setdefault(("name", body["type"].rsplit("@", 1)[0], body["name"]), body)
    same: dict[str, str] = {}
    out: dict[str, list[dict]] = {"groups": [], "nodes": [], "edges": []}
    for field in ("groups", "nodes"):
        for body in incoming.get(field, []):
            props = body.get("properties") or {}
            match = (existing.get(("id", body["id"])) or existing.get(("arn", props.get("arn")))
                     or existing.get(("name", body["type"].rsplit("@", 1)[0], body["name"])))
            if match is None:
                out[field].append(body)
                continue
            same[body["id"]] = match["id"]
            out[field].append(_filled(match, body))
    out["edges"] = [{**edge, "from": same.get(edge["from"], edge["from"]), "to": same.get(edge["to"], edge["to"])}
                    for edge in incoming.get("edges", [])]
    return out


def _filled(existing: dict, scanned: dict) -> dict:
    """The clone's own item, with the properties and the note it lacks taken from the scan. Nothing it has is replaced."""
    filled = deepcopy(existing)
    properties = filled.setdefault("properties", {})
    for key, value in (scanned.get("properties") or {}).items():
        if properties.get(key) in (None, "", []):
            properties[key] = value
    if scanned.get("notes") and not filled.get("notes"):
        filled["notes"] = scanned["notes"]
    if not properties:
        del filled["properties"]
    return filled


def item_key(kind: str, body: dict) -> str:
    """One stable key per node, group or edge, the same in every import."""
    if kind == "edge":
        return f"edge:{body['from']}|{body['type']}|{body['to']}"
    return f"{kind}:{body['id']}"


def build(incoming: Incoming, document: dict, *, owners: Iterable[str], is_generic: IsGeneric,
          rejected: dict[str, dict] | None = None) -> list[dict]:
    """The change items between an import and the clone document, in the order a person reviews them."""
    owned = tuple(owners)
    skip = rejected or {}
    items: list[dict] = []
    for kind, field in (("group", "groups"), ("node", "nodes")):
        items.extend(_place_items(kind, incoming.get(field, []), document.get(field, []), owned, is_generic))
    items.extend(_edge_items(incoming.get("edges", []), document, owned, items))
    return [item for item in items if not _was_rejected(item, skip)]


def _place_items(kind: str, wanted: list[dict], existing: list[dict], owners: tuple[str, ...],
                 is_generic: IsGeneric) -> list[dict]:
    have = {body["id"]: body for body in existing}
    seen = {body["id"] for body in wanted}
    items = []
    for body in wanted:
        before = have.get(body["id"])
        if before is None:
            items.append(_item(kind, "add", body, held=is_generic(body["type"])))
            continue
        after = merge(before, body, is_generic)
        if after != before:
            items.append({**_item(kind, "update", body), "changes": changes(before, after)})
    for body in existing:
        if body["id"] not in seen and is_owned(body, owners):
            items.append(_item(kind, "remove", body))
    return items


def _edge_items(wanted: list[dict], document: dict, owners: tuple[str, ...], places: list[dict]) -> list[dict]:
    have = {item_key("edge", edge): edge for edge in document.get("edges", [])}
    seen = {item_key("edge", edge) for edge in wanted}
    going = {item["item"]["id"] for item in places if item["action"] == "remove"}
    items = []
    for body in wanted:
        if item_key("edge", body) not in have:
            items.append(_item("edge", "add", body))
    for key, body in have.items():
        # An edge that ends at a removed node goes with it, so it is not asked about separately.
        if key not in seen and is_owned(body, owners) and not {body["from"], body["to"]} & going:
            items.append(_item("edge", "remove", body))
    return items


def _item(kind: str, action: str, body: dict, *, held: bool = False) -> dict:
    return {"key": item_key(kind, body), "kind": kind, "action": action, "item": body, "held": held,
            "decision": "undecided", "type": None}


def is_owned(body: dict, owners: Iterable[str]) -> bool:
    """Whether an import put this item there: its provenance names one of the owners (`terraform`, `aws`)."""
    provenance = body.get("provenance") or {}
    return provenance.get("kind") in IMPORTED_KINDS and str(provenance.get("source", "")).split(":")[0] in tuple(owners)


def merge(existing: dict, incoming: dict, is_generic: IsGeneric) -> dict:
    """The item after an update: imported fields win, everything a person added stays."""
    merged = {**existing, "name": incoming["name"], "provenance": incoming.get("provenance", existing.get("provenance"))}
    # A person who gave a Generic item a real type keeps it when the importer still only knows Generic.
    keeps_type = is_generic(incoming["type"]) and not is_generic(existing["type"])
    merged["type"] = existing["type"] if keeps_type else incoming["type"]
    # A Generic item only carries a "what is this" text the chosen type has no place for.
    properties = {**(existing.get("properties") or {}), **({} if keeps_type else incoming.get("properties") or {})}
    if properties:
        merged["properties"] = properties
    if incoming.get("parent"):
        merged["parent"] = incoming["parent"]
    if incoming.get("notes") and not existing.get("notes"):
        merged["notes"] = incoming["notes"]  # a note a person wrote is never replaced; an empty place takes the import's
    return {key: value for key, value in merged.items() if value is not None}


def changes(before: dict, after: dict) -> list[dict]:
    """Field by field, what an update changes: name, type, notes and each property."""
    found = []
    for field in ("name", "type", "notes"):
        if before.get(field) != after.get(field):
            found.append({"field": field, "before": before.get(field), "after": after.get(field)})
    old, new = before.get("properties") or {}, after.get("properties") or {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            found.append({"field": key, "before": old.get(key), "after": new.get(key)})
    if before.get("parent") != after.get("parent"):
        found.append({"field": "parent", "before": before.get("parent"), "after": after.get("parent")})
    # How the item came in (R-054): live discovery is `discovered`, a file import `imported`. An item a connection put
    # there before discovery was told apart is corrected once, as a visible change.
    old_kind, new_kind = (before.get("provenance") or {}).get("kind"), (after.get("provenance") or {}).get("kind")
    if old_kind and new_kind and old_kind != new_kind:
        found.append({"field": "provenance", "before": old_kind, "after": new_kind})
    return found


def _was_rejected(item: dict, rejected: dict[str, dict]) -> bool:
    return item["key"] in rejected and rejected[item["key"]] == item["item"]


def decide(items: list[dict], keys: list[str], decision: str, type_ref: str | None = None) -> list[dict]:
    """Items with the decision set on the named keys. A held item can only be accepted with a type."""
    if decision not in DECISIONS:
        raise ValueError(f"A decision is one of {', '.join(DECISIONS)}.")
    known = {item["key"] for item in items}
    missing = [key for key in keys if key not in known]
    if missing:
        raise KeyError(missing[0])
    named = set(keys)
    out = []
    for item in items:
        if item["key"] not in named:
            out.append(item)
            continue
        chosen = (type_ref or item["type"]) if item["held"] else None
        if decision == "accept" and item["held"] and not chosen:
            raise ValueError(f"{item['item']['name']} has no library type yet. Pick one before accepting it.")
        out.append({**item, "decision": decision, "type": chosen})
    return out


def retype(body: dict, type_ref: str, declared: set[str]) -> dict:
    """The held item with its real type. Properties the new type does not declare, such as the Generic
    'what is this' text, are left behind; where a thing is (arn, region) always stays."""
    properties = {key: value for key, value in (body.get("properties") or {}).items()
                  if key in declared or key in _ROUTING}
    out = {**body, "type": type_ref}
    out.pop("properties", None)
    if properties:
        out["properties"] = properties
    return out


def apply_accepted(document: dict, items: list[dict], *, declared: Callable[[str], set[str]],
                   is_generic: IsGeneric) -> tuple[dict, dict]:
    """The clone document after every accepted item, and a count of what happened.

    An edge whose end was not accepted, and a group or location that points at one, is left out
    rather than left dangling. The counts say how many.
    """
    doc = deepcopy(document)
    accepted = [item for item in items if item["decision"] == "accept"]
    report = {"added": 0, "updated": 0, "removed": 0, "left_out_edges": 0, "left_out": []}
    for item in accepted:
        if item["kind"] != "edge":
            _apply_place(doc, item, report, declared, is_generic)
    for item in accepted:
        if item["kind"] == "edge":
            _apply_edge(doc, item, report)
    report["left_out"] = _drop_dangling(doc)
    report["left_out_edges"] = len(report["left_out"])
    return doc, report


def _apply_place(doc: dict, item: dict, report: dict, declared: Callable[[str], set[str]], is_generic: IsGeneric) -> None:
    field = f"{item['kind']}s"
    body = item["item"]
    if item["held"]:
        body = retype(body, item["type"], declared(item["type"]))
    if item["action"] == "remove":
        doc[field] = [entry for entry in doc[field] if entry["id"] != body["id"]]
        report["removed"] += 1
        return
    current = next((entry for entry in doc[field] if entry["id"] == body["id"]), None)
    if current is None:
        doc[field].append(body)
        report["added"] += 1
        return
    merged = merge(current, body, is_generic)
    doc[field] = [merged if entry["id"] == body["id"] else entry for entry in doc[field]]
    report["updated"] += 1


def _apply_edge(doc: dict, item: dict, report: dict) -> None:
    body = item["item"]
    same = [edge for edge in doc["edges"] if item_key("edge", edge) == item["key"]]
    if item["action"] == "remove":
        doc["edges"] = [edge for edge in doc["edges"] if item_key("edge", edge) != item["key"]]
        report["removed"] += 1
    elif not same:
        doc["edges"].append(body)
        report["added"] += 1


def _drop_dangling(doc: dict) -> list[str]:
    """Remove references to items that are not in the document. Returns the keys of the edges that went."""
    ends = {entry["id"] for entry in doc["nodes"] + doc["groups"]}
    groups = {entry["id"] for entry in doc["groups"]}
    kept = [edge for edge in doc["edges"] if edge["from"] in ends and edge["to"] in ends]
    dropped = [item_key("edge", edge) for edge in doc["edges"] if edge not in kept]
    doc["edges"] = kept
    for entry in doc["nodes"]:
        if entry.get("location") and entry["location"] not in groups:
            del entry["location"]
    for entry in doc["groups"]:
        if entry.get("parent") and entry["parent"] not in groups:
            del entry["parent"]
    return dropped


def settle_left_out(items: list[dict], left_out: list[str]) -> list[dict]:
    """After an apply: an accepted edge left out because an end was rejected counts as rejected, so the
    next import does not offer it again. One left out because an end is still undecided stays on offer."""
    turned_down = {item["item"]["id"] for item in items if item["kind"] != "edge" and item["decision"] == "reject"}
    out = []
    for item in items:
        body = item["item"]
        if item["key"] in left_out and {body["from"], body["to"]} & turned_down:
            item = {**item, "decision": "reject"}
        out.append(item)
    return out


def waits_for(item: dict, accepted_ends: set[str]) -> list[str]:
    """For an edge or a group: the ids it needs that are neither in the clone nor accepted yet."""
    body = item["item"]
    needs = [body["from"], body["to"]] if item["kind"] == "edge" else [body["parent"]] if body.get("parent") else []
    return [need for need in needs if need not in accepted_ends]


def provenance_counts(document: dict) -> dict[str, int]:
    """Nodes, groups and edges by where they came from. Nothing recorded counts as `not recorded`."""
    counts = {"imported": 0, "discovered": 0, "drawn": 0, "not recorded": 0}
    for collection in ("nodes", "groups", "edges"):
        for body in document.get(collection, []):
            kind = (body.get("provenance") or {}).get("kind")
            counts[kind if kind in counts else "not recorded"] += 1
    return counts

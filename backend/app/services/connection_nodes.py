"""A connection is a node on the canvas, and what it finds lands inside it (STUDIO.4).

A connection node is a container of a `connection.<name>` library type. It holds nothing but the name of the connector
(`properties.connector`): never a role, an external id or a secret, those stay with the connection on the server.
Bringing in is the ordinary import (changesets.py), narrowed to one connection: its items that sit at the top of what it
found are put inside its node, a resource the connection no longer finds goes away, and a resource a person drew by hand
is never touched. Items that need a library type are left in the change set for a person to decide.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..connectors import routing
from ..models import ChangeSet, Clone, Connector, Project
from . import changesets, connector_status, imports
from .clones import (
    STARTED_FROM_CONNECTIONS,
    blank_document,
    create_clone,
    document_problems,
    from_connections,
    read_clone,
    save_version,
)

TYPE_PREFIX = "connection."
# The word each connector's items carry in their provenance: what a bring-in may add to, update and take away.
OWNERS = {"aws": ("aws",), "github": ("github",), "cloudwatch": ("cloudwatch",)}


def type_id(key: str) -> str:
    """The library type of a connector's node: `azure_monitor` is `connection.azure-monitor`."""
    return TYPE_PREFIX + key.replace("_", "-")


def connection_group(document: dict, key: str) -> dict | None:
    """The node that stands for this connector in the clone, if one was placed."""
    for group in document.get("groups", []):
        if group["type"].split("@")[0] == type_id(key):
            return group
    return None


# STUDIO.7: a connection node placed for a person by the Connect button. Not drawn by hand, and not owned by the connection,
# so a bring-in never proposes taking the node itself away.
PLACED = {"kind": "discovered", "source": "WhiteHats"}
# The clone schema's group kind for each connector's node; the Studio's connection.js says the same.
KINDS = {"aws": "account", "cloudwatch": "account", "github": "organisation"}
NAMES = {"aws": "AWS connection", "cloudwatch": "CloudWatch logs connection", "github": "GitHub connection"}
# A project names its environments in words; a clone uses the schema's short ones.
ENVIRONMENTS = {"production": "prod", "prod": "prod", "staging": "staging", "development": "dev", "dev": "dev"}


def _project_clone(db: Session, project: Project, *, company: str, author: str) -> Clone:
    """The project's map: the first clone its connections started, or a new empty one named after the project.

    A clone drawn by hand is never the connections' map: it has no cloud addresses to match on, so reading into it
    would draw everything twice. It stays in the project, with its history, beside the new map.
    """
    clones = db.scalars(select(Clone).where(Clone.project_id == project.id).order_by(Clone.id)).all()
    mapped = next((clone for clone in clones if from_connections(clone)), None)
    if mapped is not None:
        return mapped
    taken = {clone.slug for clone in clones}
    slug = project.slug if project.slug not in taken else f"{project.slug}-live"
    environment = next((ENVIRONMENTS[name] for name in project.environments or [] if name in ENVIRONMENTS), "prod")
    document = blank_document(slug, environment, author, f"Started the {project.name} {STARTED_FROM_CONNECTIONS}")
    problems = document_problems(document, company)
    if problems:
        raise imports.ImportRefused(*problems)
    return create_clone(db, project.org_id, document, author, document["version"]["reason"], project_id=project.id)


def place(db: Session, project: Project, connector: Connector, *, company: str, author: str) -> dict:
    """Make sure the project's map exists and holds this connection's node; returns the clone and the node.

    Nothing is drawn by a person: the node is placed for the connection, the same shape the Studio places by hand.
    """
    if connector.key not in OWNERS:
        raise imports.ImportRefused(f"{connector.name} cannot be shown on the map in this version.")
    clone = _project_clone(db, project, company=company, author=author)
    document = read_clone(db, clone.org_id, clone.id)
    group = connection_group(document, connector.key)
    if group is not None:
        return {"clone": clone.id, "node": group["id"], "version": document["version"]["number"], "placed": False}
    group = {"id": f"g_connection_{connector.key}", "type": f"{type_id(connector.key)}@1", "name": NAMES[connector.key],
             "kind": KINDS[connector.key], "environment": document["environment"], "provenance": PLACED,
             "properties": {"connector": connector.key}}
    reason = f"Placed the {connector.name} connection"
    number = document["version"]["number"] + 1
    document = {**document, "groups": [*document["groups"], group],
                "version": {"number": number, "author": author, "reason": reason}}
    problems = document_problems(document, company)
    if problems:
        raise imports.ImportRefused(*problems)
    save_version(db, clone.org_id, clone.id, document, author, reason)
    return {"clone": clone.id, "node": group["id"], "version": number, "placed": True}


WEBHOOK_ID = "github_webhook"
WEBHOOK_TYPE = "delivery.github-webhook@1"


def _with_webhook(incoming: changesets.Incoming, connector: Connector) -> changesets.Incoming:
    """The GitHub connection's push listener, as a node, when the App is installed and the server can check what GitHub signs.

    The path is the platform's own, never a secret. Without a webhook secret on the server nothing is listening, so no node says so.
    """
    if not (connector.config or {}).get("installation_id") or not settings.github_webhook_secret:
        return incoming
    node = {"id": WEBHOOK_ID, "type": WEBHOOK_TYPE, "name": "GitHub push listener", "environment": "shared",
            "provenance": {"kind": "discovered", "source": "github"},
            "properties": {"path": "/api/github/webhook", "events": ["push", "pull_request"], "signed": True}}
    return {**incoming, "nodes": [*incoming.get("nodes", []), node]}


def _inside(incoming: changesets.Incoming, group_id: str) -> changesets.Incoming:
    """What the connection found, with everything that sat at the top now sitting in the connection's node."""
    groups = [{**g, "parent": g.get("parent") or group_id} for g in incoming.get("groups", [])]
    nodes = [{**n, "location": n.get("location") or group_id} for n in incoming.get("nodes", [])]
    return {"groups": groups, "nodes": nodes, "edges": list(incoming.get("edges", []))}


def waiting_label(connector: Connector) -> str:
    return f"Waiting for you: what {connector.name} no longer finds or cannot type"


def _drop_untouched_waiting(db: Session, clone: Clone, connector: Connector) -> None:
    """A refresh replaces this connection's waiting import, unless a person has started deciding it."""
    for found in db.scalars(select(ChangeSet).where(
            ChangeSet.clone_id == clone.id, ChangeSet.status == "open", ChangeSet.label == waiting_label(connector))).all():
        if all(item["decision"] == "undecided" for item in found.items):
            imports.discard(db, found)


def bring_in(db: Session, clone: Clone, connector: Connector, *, company: str, author: str,
             hold_removals: bool = False, why: str = "") -> dict:
    """Save what this connection found (as this project's scope lets it) into the clone, inside the connection's node.

    With `hold_removals` (the automatic refresh, STUDIO.6), what the connection no longer finds is not removed: it
    waits in the change set for a person. `why` says what led to the read, in words, for the version's reason.
    Returns how many things were added, updated and removed, how many wait for a library type or a person, and the
    clone version.
    """
    if connector.key not in OWNERS:
        raise imports.ImportRefused(f"{connector.name} cannot be read into the canvas in this version.")
    document = read_clone(db, clone.org_id, clone.id)
    if document is None:
        raise imports.ImportRefused("This clone has no saved version yet.")
    group = connection_group(document, connector.key)
    if group is None:
        raise imports.ImportRefused(f"This clone has no {connector.name} node. Place one from the library first.")
    found = connector_status.project_view(db, clone.org_id, clone.project_id, only=connector.key)["document"]
    incoming = {key: found.get(key, []) for key in ("nodes", "groups", "edges")}
    if connector.key == "github":
        incoming = _with_webhook(incoming, connector)
    incoming = _inside(incoming, group["id"])
    if hold_removals:
        _drop_untouched_waiting(db, clone, connector)
    label = f"What {connector.name} found" + (f" ({why})" if why else "")
    made = imports.open_change_set(db, clone, incoming, source="connections", label=label[:200],
                                   author=author, owners=OWNERS[connector.key])
    version = document["version"]["number"]
    if made is None:
        return {"added": 0, "updated": 0, "removed": 0, "held": 0, "waiting": 0, "version": version,
                "change_set": None}
    accepted = [item["key"] for item in made.items
                if not item["held"] and not (hold_removals and item["action"] == "remove")]
    held = sum(1 for item in made.items if item["held"])
    waiting = sum(1 for item in made.items if hold_removals and item["action"] == "remove")
    if not accepted:
        if hold_removals:
            made.label = waiting_label(connector)
            db.commit()
        return {"added": 0, "updated": 0, "removed": 0, "held": held, "waiting": waiting, "version": version,
                "change_set": made.id}
    imports.decide(db, made, accepted, "accept", None)
    done = imports.apply(db, made, company=company, author=author)
    report = done["report"]
    if hold_removals and (held or waiting):
        # What was not applied waits in an open import of its own, where a person confirms or keeps each item.
        # It is compared with the version just saved, so it holds only what that version left out (the session
        # keeps objects after a commit, so the clone's list of versions is read again).
        db.commit()
        db.expire_all()
        imports.open_change_set(db, clone, incoming, source="connections", label=waiting_label(connector),
                                author=author, owners=OWNERS[connector.key])
    return {"added": report["added"], "updated": report["updated"], "removed": report["removed"], "held": held,
            "waiting": waiting, "version": done["version"], "change_set": made.id}


def choices(db: Session, connector: Connector, project) -> dict:
    """What a person can narrow this connection to, and what this project has chosen so far.

    The choices come from what the connection has already found (all of it, before any project's scope), plus what its
    own setup names, so a person picks from real regions and repositories rather than typing them.
    """
    found = (connector.state or {}).get("discovery") or {}
    regions, repos = set(), set()
    for node in found.get("nodes", []):
        props = node.get("properties") or {}
        if props.get("region"):
            regions.add(str(props["region"]))
        if props.get("repo"):
            repos.add(str(props["repo"]))
    config = connector.config or {}
    regions.update(name.strip() for name in ",".join([config.get("region", ""), config.get("regions", "")]).split(",") if name.strip())
    scope = next((scope for project_id, scope in routing.attachments(db, connector) if project_id == project.id), None)
    return {"connector": connector.key, "attached": scope is not None, "scope": scope or {},
            "regions": sorted(regions), "repos": sorted(repos), "found_at": found.get("at")}


def forget(db: Session, connector: Connector, project_ids: list[int] | None = None) -> list[int]:
    """Take this connection off its projects' maps (STUDIO.8): its node, and everything it brought in, as a new
    version. Disconnecting means WhiteHats no longer sees those things, so the map must not keep showing them as if it
    did. A thing a person drew by hand is never taken away: one that sat inside the connection's node moves to the top.
    The connection's waiting imports are discarded. Returns the clone versions saved."""
    if connector.key not in OWNERS:
        return []
    owners = set(OWNERS[connector.key])
    clones = db.scalars(select(Clone).where(Clone.org_id == connector.org_id))
    saved = []
    for clone in clones:
        if project_ids is not None and clone.project_id not in project_ids:
            continue
        document = read_clone(db, clone.org_id, clone.id)
        if document is None:
            continue
        group = connection_group(document, connector.key)
        theirs = lambda item: (item.get("provenance") or {}).get("source") in owners
        gone_groups = {g["id"] for g in document["groups"] if theirs(g)} | ({group["id"]} if group else set())
        # a group of the connection's holds what sits in it, at any depth
        changed = True
        while changed:
            more = {g["id"] for g in document["groups"] if g.get("parent") in gone_groups and theirs(g)}
            changed = not more <= gone_groups
            gone_groups |= more
        gone_nodes = {n["id"] for n in document["nodes"] if theirs(n)}
        if not gone_groups and not gone_nodes:
            continue
        kept_groups = [{**g, "parent": None} if g.get("parent") in gone_groups else g
                       for g in document["groups"] if g["id"] not in gone_groups]
        kept_nodes = [{**n, "location": None} if n.get("location") in gone_groups else n
                      for n in document["nodes"] if n["id"] not in gone_nodes]
        for item in (*kept_groups, *kept_nodes):
            for key in ("parent", "location"):
                if key in item and item[key] is None:
                    del item[key]
        gone = gone_nodes | gone_groups
        kept_edges = [e for e in document["edges"] if e["from"] not in gone and e["to"] not in gone]
        after = {**document, "groups": kept_groups, "nodes": kept_nodes, "edges": kept_edges}
        after.pop("version", None)
        for found in db.scalars(select(ChangeSet).where(ChangeSet.clone_id == clone.id, ChangeSet.status == "open")):
            if found.label.startswith((f"What {connector.name} found", waiting_label(connector))):
                imports.discard(db, found)
        version = save_version(db, clone.org_id, clone.id, after, f"{connector.name} connection",
                               f"{connector.name} was disconnected: what it brought in left the map "
                               f"({len(gone_nodes)} things, {len(gone_groups)} areas)")
        if version is not None:
            saved.append(version.number)
    return saved

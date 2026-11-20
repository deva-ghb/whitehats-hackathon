"""A connection is a node on the canvas, and what it finds lands inside it (STUDIO.4)."""

from itertools import count

from library.resolve import load_library, resolve_clone
from sqlalchemy import select
from test_connector_status import EXTERNAL_ID, fake_aws  # noqa: F401 - a moto AWS account, used as a fixture

from app.connectors import routing
from app.db import SessionLocal
from app.models import Connector, Organization, Project

_slugs = count(1)
DRAWN = {"kind": "drawn", "source": "Clone Studio"}


def _document(slug, *, aws=True, hand_drawn=False) -> dict:
    groups = [{"id": "g_connection_aws", "type": "connection.aws@1", "name": "AWS connection", "kind": "account",
               "environment": "prod", "provenance": DRAWN, "properties": {"connector": "aws"}}] if aws else []
    nodes = [{"id": "n_my_service", "type": "service.backend@1", "name": "my-service", "environment": "prod",
              "provenance": DRAWN, "location": "g_connection_aws"}] if hand_drawn else []
    return {"schema_version": 1, "clone": slug, "environment": "prod", "library_version": "2026.10",
            "version": {"number": 1, "author": "Test", "reason": "blank"},
            "nodes": nodes, "edges": [], "groups": groups, "access_paths": [], "credential_refs": []}


def _clone(client, headers, **kind) -> int:
    document = _document(f"connected-{next(_slugs)}", **kind)
    made = client.post("/api/clones", headers=headers, json={"author": "Test", "reason": "blank", "document": document})
    assert made.status_code == 200, made.text
    return made.json()["id"]


def _read(client, headers, clone_id) -> dict:
    return client.get(f"/api/clones/{clone_id}", headers=headers).json()["document"]


def _aws(client, ceo) -> dict:
    return next(row for row in client.get("/api/connectors", headers=ceo).json() if row["key"] == "aws")


def _connect(client, ceo, fake) -> dict:
    aws = _aws(client, ceo)
    saved = client.put(f"/api/connectors/{aws['id']}", headers=ceo, json={"values": {
        "role_arn": fake["role_arn"], "external_id": EXTERNAL_ID, "region": fake["region"]}})
    assert saved.status_code == 200, saved.text
    assert client.post(f"/api/connectors/{aws['id']}/discover", headers=ceo).status_code == 200
    return aws


def _narrow(key: str, scope: dict | None) -> None:
    """Attach the connection to the main project with this scope (the route needs an account or project admin), or detach it."""
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
        connector = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == key))
        if scope is None:
            routing.attach(db, connector, project, {}, "Test")  # back to everything, as a connection starts
        else:
            routing.attach(db, connector, project, scope, "Test")
        db.commit()


def _bring_in(client, headers, clone_id, key="aws"):
    return client.post(f"/api/clones/{clone_id}/connections/{key}/bring-in", headers=headers)


def _ancestors(document, item_id) -> list[str]:
    """The containers an item sits in, nearest first."""
    items = {item["id"]: item for item in [*document["nodes"], *document["groups"]]}
    out, at = [], items[item_id]
    while at.get("location") or at.get("parent"):
        at = items[at.get("location") or at.get("parent")]
        out.append(at["id"])
    return out


def test_a_library_type_exists_for_every_connector_and_a_connection_node_holds_only_the_connector_name():
    library = load_library()
    for key in ("aws", "github", "cloudwatch", "okta", "entra", "google", "azure_monitor", "gcp_logging", "jira", "crowdstrike"):
        found = library[f"connection.{key.replace('_', '-')}"]
        assert found["family"] == "boundaries"
        assert [prop["key"] for prop in found["properties"]] == ["connector"]  # no role, external id or secret


def test_what_a_connection_finds_lands_inside_its_node_and_a_second_look_changes_nothing(client, ceo, engineer, fake_aws):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        clone_id = _clone(client, engineer)
        first = _bring_in(client, engineer, clone_id)
        assert first.status_code == 200, first.text
        done = first.json()
        assert done["added"] > 0 and done["held"] > 0 and done["version"] == 2  # the security group has no library type yet
        document = _read(client, engineer, clone_id)
        found = [node for node in document["nodes"] if node["provenance"]["kind"] != "drawn"]
        assert {"fintech-receipts", "orders", "payment-jobs", "admin-box"} <= {node["name"] for node in found}
        assert all(_ancestors(document, node["id"])[-1] == "g_connection_aws" for node in found)  # all of it, down to the top
        assert [g["id"] for g in document["groups"] if g["type"].startswith("connection.")] == ["g_connection_aws"]
        assert not any(node["type"].startswith("generic.") for node in document["nodes"])
        assert resolve_clone(document, load_library())[1] == []
        again = _bring_in(client, engineer, clone_id).json()
        assert (again["added"], again["updated"], again["removed"]) == (0, 0, 0)
        assert _read(client, engineer, clone_id)["version"]["number"] == done["version"]
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_a_bring_in_never_touches_what_another_connection_found(client, ceo, engineer, fake_aws):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        document = _document(f"connected-{next(_slugs)}")
        document["nodes"].append({"id": "n_repo", "type": "repo.github@1", "name": "deva-ghb/shop", "environment": "shared",
                                  "provenance": {"kind": "imported", "source": "github"}})
        made = client.post("/api/clones", headers=engineer, json={"author": "Test", "reason": "blank", "document": document})
        assert made.status_code == 200, made.text
        clone_id = made.json()["id"]
        assert _bring_in(client, engineer, clone_id).json()["added"] > 0
        kept = {node["id"]: node for node in _read(client, engineer, clone_id)["nodes"]}
        assert "n_repo" in kept and kept["n_repo"].get("location") is None  # AWS's bring-in leaves GitHub's repository alone
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_a_clone_without_the_node_is_told_to_place_one(client, ceo, engineer, fake_aws):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        refused = _bring_in(client, engineer, _clone(client, engineer, aws=False))
        assert refused.status_code == 422
        assert refused.json()["detail"] == ["This clone has no AWS node. Place one from the library first."]
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_narrowing_the_scope_takes_what_fell_out_of_it_and_never_a_hand_drawn_node(client, ceo, engineer, fake_aws):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        clone_id = _clone(client, engineer, hand_drawn=True)
        assert _bring_in(client, engineer, clone_id).json()["added"] > 0
        everything = {node["name"] for node in _read(client, engineer, clone_id)["nodes"]}
        assert {"my-service", "orders", "admin-box"} <= everything
        # Attach to this project, reading only a region the account has nothing in.
        _narrow("aws", {"regions": ["eu-north-1"]})
        gone = _bring_in(client, engineer, clone_id).json()
        assert gone["removed"] > 0
        left = {node["name"] for node in _read(client, engineer, clone_id)["nodes"]}
        assert left == {"my-service"}  # what a person drew stays; what the connection found is gone
        assert [g["id"] for g in _read(client, engineer, clone_id)["groups"]] == ["g_connection_aws"]
    finally:
        _narrow("aws", None)  # the project's scope is shared by every test in the session
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_the_choices_are_the_regions_the_connection_has_found_and_what_this_project_chose(client, ceo, engineer, fake_aws):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        listed = client.get("/api/connections/aws/choices", headers=engineer).json()
        assert fake_aws["region"] in listed["regions"] and listed["connector"] == "aws"
        _narrow("aws", {"regions": [fake_aws["region"]]})
        chosen = client.get("/api/connections/aws/choices", headers=engineer).json()
        assert chosen["attached"] is True and chosen["scope"] == {"regions": [fake_aws["region"]]}
    finally:
        _narrow("aws", None)  # the project's scope is shared by every test in the session
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_who_may_set_a_connection_up_is_unchanged_and_an_engineer_cannot(client, ceo, engineer):
    aws = _aws(client, ceo)
    refused = client.put(f"/api/connectors/{aws['id']}", headers=engineer, json={"values": {"role_arn": "x"}})
    assert refused.status_code == 403
    assert client.put(f"/api/connectors/{aws['id']}/scope", headers=engineer, json={"scope": {}}).status_code == 403


def test_an_unknown_connector_or_clone_is_a_plain_not_found(client, engineer):
    assert _bring_in(client, engineer, _clone(client, engineer), key="nothing").status_code == 404
    assert _bring_in(client, engineer, 99999999).status_code == 404


def test_a_connector_that_cannot_be_read_yet_is_refused_in_plain_words(client, engineer):
    clone_id = _clone(client, engineer)
    refused = _bring_in(client, engineer, clone_id, key="okta")
    assert refused.status_code == 422
    assert refused.json()["detail"] == ["Okta cannot be read into the canvas in this version."]


def test_a_push_listener_node_is_added_only_when_the_app_is_installed_and_the_server_can_check_what_github_signs(monkeypatch):
    from types import SimpleNamespace

    from app.services import connection_nodes

    empty = {"nodes": [], "groups": [], "edges": []}
    installed = SimpleNamespace(config={"installation_id": "1"})
    monkeypatch.setattr(connection_nodes, "settings", SimpleNamespace(github_webhook_secret="a-secret-of-some-length"))
    added = connection_nodes._with_webhook(empty, installed)["nodes"]
    assert [node["name"] for node in added] == ["GitHub push listener"]
    assert added[0]["type"] == "delivery.github-webhook@1" and added[0]["properties"]["signed"] is True
    assert "a-secret-of-some-length" not in str(added)  # the node names the path GitHub calls, never the secret
    assert connection_nodes._with_webhook(empty, SimpleNamespace(config={}))["nodes"] == []
    monkeypatch.setattr(connection_nodes, "settings", SimpleNamespace(github_webhook_secret=""))
    assert connection_nodes._with_webhook(empty, installed)["nodes"] == []


def test_disconnecting_takes_the_connection_and_what_it_found_off_the_map_but_never_a_hand_drawn_node(
        client, ceo, engineer, fake_aws):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    connected = True
    try:
        clone_id = _clone(client, engineer, hand_drawn=True)
        assert _bring_in(client, engineer, clone_id).json()["added"] > 0
        assert len(_read(client, engineer, clone_id)["nodes"]) > 1
        assert client.delete(f"/api/connectors/{aws['id']}", headers=ceo).status_code == 200
        connected = False
        after = _read(client, engineer, clone_id)
        assert after["groups"] == [] and after["edges"] == []
        assert [(n["name"], n.get("location")) for n in after["nodes"]] == [("my-service", None)]
        history = client.get(f"/api/clones/{clone_id}/versions", headers=engineer).json()
        rows = history if isinstance(history, list) else history["versions"]
        newest = max(rows, key=lambda v: v["number"])
        assert newest["reason"].startswith("AWS was disconnected")
    finally:
        if connected:
            client.delete(f"/api/connectors/{aws['id']}", headers=ceo)

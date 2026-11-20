"""STUDIO.6: the map builds and keeps itself from the connections. Attaching starts it, a change in the account or
an hour reads it again, additions go in by themselves, and what is no longer found waits for a person."""

import json
from datetime import timedelta
from itertools import count

import pytest
from conftest import remove_project
from sqlalchemy import select
from test_connection_nodes import _connect
from test_connector_status import fake_aws  # noqa: F401 - a moto AWS account, used as a fixture

from app.connectors import routing
from app.db import SessionLocal, utcnow
from app.models import ChangeSet, Clone, CloneVersion, Connector, Organization, Project
from app.services import auto_map
from app.services.clones import read_clone

_names = count(1)


@pytest.fixture
def project():
    """A new project of the demo company with no map, reading the AWS connection; it lets go afterwards."""
    slug = f"auto-map-{next(_names)}"
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        made = Project(org_id=org.id, name="Auto map", slug=slug)
        db.add(made)
        db.flush()
        aws = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        routing.attach(db, aws, made, {}, "Test")
        db.commit()
        ids = (made.id, org.id)
    yield ids[0]
    with SessionLocal() as db:
        aws = db.scalar(select(Connector).where(Connector.key == "aws", Connector.org_id == ids[1]))
        routing.detach(db, aws, db.get(Project, ids[0]))
        db.commit()
    remove_project(ids[0])


def _refresh(project_id: int, why: str = "test") -> dict:
    with SessionLocal() as db:
        aws = db.scalar(select(Connector).where(Connector.key == "aws", Connector.org_id == db.get(Project, project_id).org_id))
        return auto_map.refresh(db, db.get(Project, project_id), aws, why=why)


def _document(clone_id: int) -> dict:
    with SessionLocal() as db:
        clone = db.get(Clone, clone_id)
        return read_clone(db, clone.org_id, clone.id)


def _versions(clone_id: int) -> list[CloneVersion]:
    with SessionLocal() as db:
        rows = db.scalars(select(CloneVersion).where(CloneVersion.clone_id == clone_id).order_by(CloneVersion.number)).all()
        db.expunge_all()
        return list(rows)


def test_a_project_with_no_map_gets_one_drawn_by_its_connection(client, ceo, fake_aws, project):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        done = _refresh(project, "AWS was attached")
        assert done["added"] > 0
        names = {node["name"] for node in _document(done["clone"])["nodes"]}
        assert {"payment-jobs", "fintech-receipts"} <= names
        versions = _versions(done["clone"])
        assert [v.author for v in versions[1:]] == ["AWS connection"] * (len(versions) - 1)  # nobody drew anything
        assert "AWS was attached" in versions[-1].reason
        with SessionLocal() as db:  # what waits is only what the version left out: no type, or no longer found
            for waiting in db.scalars(select(ChangeSet).where(ChangeSet.clone_id == done["clone"], ChangeSet.status == "open")):
                assert all(item["held"] or item["action"] == "remove" for item in waiting.items), waiting.items
        again = _refresh(project)
        assert again["version"] == done["version"] and again["added"] == again["updated"] == 0  # nothing new, no version
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_what_the_connection_no_longer_finds_waits_for_a_person(client, ceo, fake_aws, project):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        done = _refresh(project)
        fake_aws["delete_queue"]("payment-jobs")
        later = _refresh(project, "AWS saw DeleteQueue")
        assert later["removed"] == 0 and later["waiting"] >= 1
        assert "payment-jobs" in {node["name"] for node in _document(done["clone"])["nodes"]}  # still on the map
        _refresh(project, "the hourly read")  # a second read leaves one waiting import, not two
        with SessionLocal() as db:
            waiting = db.scalars(select(ChangeSet).where(ChangeSet.clone_id == done["clone"], ChangeSet.status == "open")).all()
            assert len(waiting) == 1 and waiting[0].label.startswith("Waiting for you")
            assert any(item["action"] == "remove" and item["item"]["name"] == "payment-jobs" for item in waiting[0].items)
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def _trail(event: dict) -> dict:
    return {"source": f"cloudtrail:ap-southeast-2/{event['eventName']}", "message": json.dumps(event)}


def test_a_write_in_the_account_asks_for_a_read_and_a_read_does_not():
    wrote = _trail({"eventName": "CreateQueue", "readOnly": False, "managementEvent": True})
    looked = _trail({"eventName": "ListQueues", "readOnly": True, "managementEvent": True})
    data = _trail({"eventName": "PutObject", "readOnly": False, "managementEvent": False})
    assert auto_map.changed_the_setup([looked, wrote]) == "AWS changed CreateQueue"
    assert auto_map.changed_the_setup([looked, data, {"source": "logs:/aws/lambda/x", "message": "{}"}]) == ""
    # The server's own management agent checks in every few minutes: housekeeping, not a change to the estate.
    agent = _trail({"eventName": "UpdateInstanceInformation", "readOnly": False, "managementEvent": True})
    assert auto_map.changed_the_setup([agent]) == "" and auto_map.changed_the_setup([agent, wrote]) == "AWS changed CreateQueue"


def test_only_a_security_change_asks_for_an_audit_and_it_is_not_replaced_by_housekeeping():
    from types import SimpleNamespace

    param = _trail({"eventName": "PutParameter", "readOnly": False, "managementEvent": True})
    opened = _trail({"eventName": "AuthorizeSecurityGroupIngress", "readOnly": False, "managementEvent": True})
    refused = _trail({"eventName": "AttachUserPolicy", "readOnly": False, "managementEvent": True,
                      "errorCode": "AccessDenied"})
    assert auto_map.changed_the_setup([param]) == "AWS changed PutParameter"  # the map is read; no audit
    assert auto_map.changed_the_setup([param, opened]) == "AWS saw AuthorizeSecurityGroupIngress"
    assert auto_map.changed_the_setup([refused]) == "AWS changed AttachUserPolicy"  # refused by AWS: nothing changed
    c = SimpleNamespace(state={})
    auto_map.mark_due(c, "AWS saw AuthorizeSecurityGroupIngress")
    auto_map.mark_due(c, "AWS changed PutParameter")
    assert c.state["map_due"] == "AWS saw AuthorizeSecurityGroupIngress"


def test_a_push_that_changes_how_the_estate_is_built_asks_for_a_read():
    assert auto_map.touched_the_build(["shop/app.py", "terraform/queues.tf"]) == "a push changed terraform/queues.tf"
    assert auto_map.touched_the_build([".github/workflows/deploy.yml"])
    assert auto_map.touched_the_build(["shop/app.py", "README.md"]) == ""


def test_the_pass_reads_what_asked_and_what_is_an_hour_old(client, ceo, fake_aws, project, monkeypatch):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    read = []
    monkeypatch.setattr(auto_map, "refresh", lambda db, p, c, why: read.append((p.id, why)) or {})
    try:
        with SessionLocal() as db:
            row = db.get(Connector, aws["id"])
            settled = {k: v for k, v in row.state.items() if k != "map_due"}  # connecting asked for a read: done
            row.state = {**settled, "map_at": utcnow().isoformat()}
            db.commit()
            auto_map.run_due(db)
            assert read == []  # read a moment ago, nothing asked
            auto_map.mark_due(db.get(Connector, aws["id"]), "AWS saw CreateQueue")
            db.commit()
            auto_map.run_due(db)
            assert (project, "AWS saw CreateQueue") in read
            assert "map_due" not in db.get(Connector, aws["id"]).state
            read.clear()
            auto_map.run_due(db, now=utcnow() + timedelta(hours=1, minutes=1))
            assert (project, "the hourly read") in read
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_connecting_asks_for_the_maps_to_be_drawn(client, ceo, fake_aws):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        with SessionLocal() as db:
            assert db.get(Connector, aws["id"]).state.get("map_due") == "AWS was connected"
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_a_hand_drawn_map_is_kept_beside_a_new_map_from_the_connections(client, ceo, fake_aws, project):  # noqa: F811
    from app.services.clones import blank_document, create_clone, list_clones

    with SessionLocal() as db:
        target = db.get(Project, project)
        drawn = create_clone(db, target.org_id, blank_document(target.slug, "prod", "Kavya Iyer", "Drawn by hand"),
                             "Kavya Iyer", "Drawn by hand", project_id=project)
        drawn_id = drawn.id
    aws = _connect(client, ceo, fake_aws)
    try:
        done = _refresh(project)
        assert done["clone"] != drawn_id  # the hand-drawn map is never read into: it has no addresses to match
        with SessionLocal() as db:
            listed = list_clones(db, db.get(Project, project).org_id, project)
        assert [entry["id"] for entry in listed] == [done["clone"], drawn_id]  # the connections' map opens first
        assert listed[0]["from_connections"] and listed[0]["clone"].endswith("-live")
        assert _refresh(project)["clone"] == done["clone"]
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)

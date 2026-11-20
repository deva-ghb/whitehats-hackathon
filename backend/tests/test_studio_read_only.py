"""The map is drawn by the connections, not by hand (STUDIO.7).

With the installation's switch off, a person cannot save a drawing, and the Connect button starts the project's map and
places the connection on it. Bringing in what a connection found is the connection drawing, so an Account owner, who may
set a connection up but not edit the model, can do it too.
"""

from itertools import count

import pytest
from conftest import remove_project
from library.resolve import load_library, resolve_clone
from sqlalchemy import select
from test_connection_nodes import _ancestors, _connect, _document
from test_connector_status import fake_aws  # noqa: F401 - a moto AWS account, used as a fixture

from app.config import settings
from app.connectors import routing
from app.db import SessionLocal
from app.models import Connector, Organization, Project
from app.services.connection_nodes import PLACED

_names = count(1)


@pytest.fixture
def hand_editing_off():
    object.__setattr__(settings, "studio_hand_editing", False)  # Settings is frozen everywhere else
    yield
    object.__setattr__(settings, "studio_hand_editing", True)


@pytest.fixture
def project():
    """A fresh project of the demo company with no map yet, reading the AWS connection."""
    slug = f"studio-seven-{next(_names)}"
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        made = Project(org_id=org.id, name="Studio seven", slug=slug)
        db.add(made)
        db.flush()
        aws = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        routing.attach(db, aws, made, {}, "Test")
        db.commit()
        project_id = made.id
        org_id = org.id
    yield slug
    with SessionLocal() as db:
        aws = db.scalar(select(Connector).where(Connector.key == "aws", Connector.org_id == org_id))
        routing.detach(db, aws, db.get(Project, project_id))
        db.commit()
    remove_project(project_id)


def test_with_the_switch_off_a_person_cannot_save_a_drawing(client, engineer, hand_editing_off):
    document = _document(f"by-hand-{next(_names)}")
    made = client.post("/api/clones", headers=engineer, json={"reason": "blank", "document": document})
    assert made.status_code == 409 and "drawn from your connections" in made.json()["detail"]
    assert client.post("/api/clones/import", headers=engineer, json=document).status_code == 409
    existing = client.get("/api/clones", headers=engineer).json()["clones"]
    if existing:
        clone_id = existing[0]["id"]
        saved = client.post(f"/api/clones/{clone_id}/versions", headers=engineer,
                            json={"reason": "moved", "document": client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]})
        assert saved.status_code == 409


def test_the_web_app_is_told_whether_hand_editing_is_on(client, ceo, hand_editing_off):
    assert client.get("/api/auth/me", headers=ceo).json()["org"]["studio_hand_editing"] is False


def test_connect_starts_the_project_map_and_places_the_connection_once(client, ceo, project, hand_editing_off):
    placed = client.post(f"/api/connections/aws/place?project={project}", headers=ceo)
    assert placed.status_code == 200, placed.text
    done = placed.json()
    assert done["placed"] is True and done["node"] == "g_connection_aws" and done["version"] == 2
    document = client.get(f"/api/clones/{done['clone']}?project={project}", headers=ceo).json()["document"]
    assert document["clone"] == project and document["environment"] == "prod"
    group = next(g for g in document["groups"] if g["id"] == "g_connection_aws")
    assert group["type"] == "connection.aws@1" and group["properties"] == {"connector": "aws"}
    assert group["provenance"] == PLACED  # placed for the connection, not drawn by a person
    assert resolve_clone(document, load_library())[1] == []
    again = client.post(f"/api/connections/aws/place?project={project}", headers=ceo).json()
    assert again == {**done, "placed": False}  # a second press changes nothing


def test_an_account_owner_brings_in_what_the_connection_found_without_edit_rights(client, ceo, project, fake_aws, hand_editing_off):  # noqa: F811
    aws = _connect(client, ceo, fake_aws)
    try:
        clone_id = client.post(f"/api/connections/aws/place?project={project}", headers=ceo).json()["clone"]
        brought = client.post(f"/api/clones/{clone_id}/connections/aws/bring-in?project={project}", headers=ceo)
        assert brought.status_code == 200, brought.text
        assert brought.json()["added"] > 0
        document = client.get(f"/api/clones/{clone_id}?project={project}", headers=ceo).json()["document"]
        found = [node for node in document["nodes"] if node["provenance"]["kind"] == "discovered"]
        assert found and all(_ancestors(document, node["id"])[-1] == "g_connection_aws" for node in found)
        assert "g_connection_aws" in {g["id"] for g in document["groups"]}  # a bring-in never takes the connection away
    finally:
        client.delete(f"/api/connectors/{aws['id']}", headers=ceo)


def test_someone_who_may_neither_edit_nor_manage_connections_cannot_place(client, engineer, project):
    # The demo engineer is not a member of this project, so the project is not theirs to change.
    assert client.post(f"/api/connections/aws/place?project={project}", headers=engineer).status_code == 403


def test_connecting_from_a_project_s_map_attaches_the_connection_to_that_project(client, ceo, hand_editing_off):
    # A new company's connections feed its Main project. Connecting from another project's map must feed that map,
    # or the connection reads everything and the map the person is looking at stays empty.
    slug = f"studio-seven-{next(_names)}"
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        made = Project(org_id=org.id, name="Not attached", slug=slug)
        db.add(made)
        db.commit()
        project_id = made.id
    try:
        assert client.post(f"/api/connections/aws/place?project={slug}", headers=ceo).status_code == 200
        with SessionLocal() as db:
            aws = db.scalar(select(Connector).where(Connector.key == "aws", Connector.org_id == db.get(Project, project_id).org_id))
            assert project_id in [pid for pid, _ in routing.attachments(db, aws)]
    finally:
        with SessionLocal() as db:
            aws = db.scalar(select(Connector).where(Connector.key == "aws", Connector.org_id == db.get(Project, project_id).org_id))
            routing.detach(db, aws, db.get(Project, project_id))
            db.commit()
        remove_project(project_id)

"""A company can save clone versions and cannot read another company's clone."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Node, Organization, SensePoint
from app.services.clones import create_clone, read_clone, save_version

DOC = {
    "schema_version": 1,
    "clone": "fintech",
    "environment": "prod",
    "library_version": "2026.10",
    "nodes": [{
        "id": "n_api",
        "type": "service.backend@1",
        "name": "payments-api",
        "environment": "prod",
        "location": "g_app",
        "properties": {"runtime": "node 20"},
    }],
    "edges": [{"from": "n_api", "to": "g_app", "type": "contained_in"}],
    "groups": [{
        "id": "g_app", "type": "boundary.vpc@1", "name": "app vpc", "kind": "vpc", "environment": "prod",
    }],
    "access_paths": [{"id": "ap_public", "name": "public APIs", "kind": "public-api", "environment": "prod"}],
    "credential_refs": [{"id": "cr_app", "vault_uri": "vault://tenant/fintech/app", "purpose": "read"}],
}


def _db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _org(db: Session, name: str) -> Organization:
    org = Organization(name=name, slug=name.lower())
    db.add(org)
    db.commit()
    return org


def test_create_version_and_read_back():
    db = _db()
    org = _org(db, "Acme")
    clone = create_clone(db, org.id, DOC, author="ada", reason="first drawing")
    renamed = {**DOC, "nodes": [{**DOC["nodes"][0], "name": "payments-api-v2"}]}
    version = save_version(db, org.id, clone.id, renamed, author="ada", reason="rename the api")
    assert version is not None and version.number == 2
    latest = read_clone(db, org.id, clone.id)
    first = read_clone(db, org.id, clone.id, number=1)
    assert latest["nodes"][0]["name"] == "payments-api-v2"
    assert latest["version"]["reason"] == "rename the api"
    assert first["nodes"][0]["name"] == "payments-api"
    assert first["version"]["number"] == 1 and first["edges"][0]["type"] == "contained_in"
    assert first["credential_refs"][0]["vault_uri"] == "vault://tenant/fintech/app"
    assert db.query(Node).filter_by(org_id=org.id, node_key="n_api").count() == 2


def test_one_company_cannot_read_anothers_clone():
    db = _db()
    owner = _org(db, "Acme")
    other = _org(db, "Globex")
    clone = create_clone(db, owner.id, DOC, author="ada", reason="first drawing")
    db.add(SensePoint(
        org_id=owner.id, clone_version_id=clone.versions[0].id, node_key="n_api", mode="code", reason="the repo",
    ))
    db.commit()
    assert read_clone(db, other.id, clone.id) is None
    assert save_version(db, other.id, clone.id, DOC, author="eve", reason="not mine") is None
    assert db.query(Node).filter_by(org_id=other.id).count() == 0
    assert db.query(SensePoint).filter_by(org_id=other.id).count() == 0

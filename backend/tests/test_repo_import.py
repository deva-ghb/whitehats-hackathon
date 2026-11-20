"""Scanning a project's repositories as the source of an import: who may, what it needs, and what review shows."""

import pytest
from conftest import PASSWORD, login
from connector_contract import github
from connector_kit import HttpReplay, load
from library.resolve import load_library, resolve_clone
from sqlalchemy import select

from app import connectors
from app.connectors import routing
from app.connectors.base import SourceError
from app.connectors.github import GitHub
from app.db import SessionLocal
from app.models import Connector, Organization, Project, ProjectMember, User
from app.projects import ensure_main_project
from app.security import hash_password
from app.services.tenancy import delete_tenant_data

REPO = "deva-ghb/whitehats-proving-ground"


def _remove(db) -> None:
    org = db.scalar(select(Organization).where(Organization.slug == "repoco"))
    if org is not None:
        delete_tenant_data(db, org)
        db.delete(org)
        db.commit()


@pytest.fixture
def account(client):
    with SessionLocal() as db:
        _remove(db)
        org = Organization(name="Repo Co", slug="repoco", mfa_required=False)
        db.add(org)
        db.flush()
        main = ensure_main_project(db, org)
        row = Connector(org_id=org.id, key="github", name="GitHub", category="Code")
        db.add(row)
        engineer = User(org_id=org.id, email="eng@repoco.test", name="Repo Engineer", role="analyst",
                        password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at)
        viewer = User(org_id=org.id, email="view@repoco.test", name="Repo Viewer", role="analyst",
                      password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at)
        db.add_all([engineer, viewer])
        db.flush()
        db.add_all([ProjectMember(project_id=main.id, user_id=engineer.id, role="engineer"),
                    ProjectMember(project_id=main.id, user_id=viewer.id, role="viewer")])
        db.commit()
        ids = {"org": org.id, "main": main.id, "github": row.id}
    yield ids
    with SessionLocal() as db:
        _remove(db)


def _connect(account, *, attach: bool = True, scope: dict | None = None) -> None:
    with SessionLocal() as db:
        row = db.get(Connector, account["github"])
        row.status, row.config = "checked", {"installation_id": "12345678", "account": "deva-ghb"}
        if attach:
            routing.attach(db, row, db.get(Project, account["main"]), scope or {}, "test")
        db.commit()


@pytest.fixture
def recorded(monkeypatch):
    """Every scan reads the recorded proving-ground repository, one fresh replay each, and never the network."""
    def make(_db, row):
        return github(HttpReplay(load("github", "scan-ok")), lambda _s: None)

    monkeypatch.setattr(connectors, "for_row", make)


def _new_clone(client, headers) -> int:
    body = {"author": "t", "reason": "blank", "document": {
        "schema_version": 1, "clone": "scan-target", "environment": "prod", "library_version": "2026.10",
        "version": {"number": 1, "author": "t", "reason": "blank"}, "nodes": [], "edges": [], "groups": [],
        "access_paths": [], "credential_refs": []}}
    return client.post("/api/clones", headers=headers, json=body).json()["id"]


def test_a_scan_says_why_it_cannot_start_until_github_is_connected_and_attached(client, account):
    engineer = login(client, "eng@repoco.test")
    ready = client.get("/api/imports/sources", headers=engineer).json()["repository"]
    assert ready == {"ready": False, "repos": [], "why":
                     "GitHub is not connected yet. Install the WhiteHats GitHub App on the repositories, then read them."}
    _connect(account, attach=False)
    ready = client.get("/api/imports/sources", headers=engineer).json()["repository"]
    assert ready["ready"] is False and ready["why"].startswith("GitHub is connected for this account but not attached")
    _connect(account, scope={"repos": [REPO]})
    assert client.get("/api/imports/sources", headers=engineer).json()["repository"] == {"ready": True, "why": "", "repos": [REPO]}


def test_starting_a_scan_before_github_is_connected_is_refused_in_plain_words_and_starts_no_clone(client, account):
    engineer = login(client, "eng@repoco.test")
    made = client.post("/api/imports", headers=engineer, json={"source": "repository", "new_clone": {
        "name": "from-repo", "environment": "prod"}})
    assert made.status_code == 422 and made.json()["detail"][0].startswith("GitHub is not connected yet.")
    assert "from-repo" not in {row["clone"] for row in client.get("/api/clones", headers=engineer).json()["clones"]}


def test_a_scan_is_reviewed_like_any_import_applied_and_scanned_again_it_is_empty(client, account, recorded):
    _connect(account)
    engineer = login(client, "eng@repoco.test")
    clone_id = _new_clone(client, engineer)
    made = client.post("/api/imports", headers=engineer, json={"source": "repository", "clone_id": clone_id})
    assert made.status_code == 200, made.text
    detail = client.get(f"/api/imports/{made.json()['id']}", headers=engineer).json()
    assert detail["source"] == "repository" and detail["label"] == f"What {REPO} holds"
    assert detail["left_out"] == {
        "python project customer-loader, which nothing deploys": 1,
        f"{REPO}: module network_addon is off by default, so what it declares is not drawn": 1}
    assert not [item for item in detail["items"] if item["held"]]
    keys = [item["key"] for item in detail["items"]]
    assert client.put(f"/api/imports/{detail['id']}/decisions", headers=engineer,
                      json={"keys": keys, "decision": "accept"}).status_code == 200
    applied = client.post(f"/api/imports/{detail['id']}/apply", headers=engineer)
    assert applied.status_code == 200, applied.text
    document = client.get(f"/api/clones/{clone_id}", headers=engineer).json()["document"]
    assert {"shop-api", "payments-worker", "payment-jobs", "customers", "orders", REPO} <= {n["name"] for n in document["nodes"]}
    assert {"deploys_from", "built_by", "authenticates_via", "pushes_image_to"} == {e["type"] for e in document["edges"]}
    # What GitHub itself lists (the repository, its pipelines) is discovered; what the files say is imported (R-054).
    kinds = {item["name"]: item["provenance"]["kind"] for item in document["nodes"]}
    assert kinds[REPO] == "discovered" and kinds["shop-api"] == "imported"
    assert {kind for kind in kinds.values()} == {"discovered", "imported"}
    assert all(edge["provenance"]["kind"] in ("imported", "discovered") for edge in document["edges"])
    assert resolve_clone(document, load_library())[1] == []
    again = client.post("/api/imports", headers=engineer, json={"source": "repository", "clone_id": clone_id})
    assert again.json()["id"] is None


def test_a_scan_reads_only_the_repositories_the_project_s_scope_names(client, account, recorded):
    _connect(account, scope={"repos": ["acme/another-repo"]})
    engineer = login(client, "eng@repoco.test")
    made = client.post("/api/imports", headers=engineer, json={"source": "repository", "clone_id": _new_clone(client, engineer)})
    assert made.status_code == 422
    assert made.json()["detail"] == ["The GitHub App can see none of this project's repositories, so there is nothing to read."]


class _SignedOut(GitHub):
    """A connector GitHub turns away at sign-in."""

    def repositories(self):
        raise SourceError("GitHub denied the WhiteHats App's sign-in (401: Bad credentials).")


def test_a_github_failure_is_answered_in_words_and_changes_nothing(client, account, monkeypatch):
    _connect(account)
    monkeypatch.setattr(connectors, "for_row", lambda _db, _row: _SignedOut({"installation_id": "1"}))
    engineer = login(client, "eng@repoco.test")
    made = client.post("/api/imports", headers=engineer, json={"source": "repository", "clone_id": _new_clone(client, engineer)})
    assert made.status_code == 422 and made.json()["detail"] == ["GitHub denied the WhiteHats App's sign-in (401: Bad credentials)."]
    assert client.get("/api/imports", headers=engineer).json()["imports"] == []


def test_only_someone_who_can_edit_the_model_starts_a_scan_but_anyone_in_the_project_sees_if_one_can_start(client, account, recorded):
    _connect(account)
    engineer, viewer = login(client, "eng@repoco.test"), login(client, "view@repoco.test")
    assert client.get("/api/imports/sources", headers=viewer).json()["repository"]["ready"] is True
    refused = client.post("/api/imports", headers=viewer, json={"source": "repository", "clone_id": _new_clone(client, engineer)})
    assert refused.status_code == 403

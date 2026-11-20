"""Clone API: create, read, versions, export and import. An Engineer edits; the matrix says who else may."""

import json
from pathlib import Path

from library.clone_document import validate_clone

TEMPLATE_B = Path(__file__).resolve().parents[3] / "library" / "examples" / "template-b.clone.json"


def _document(slug: str) -> dict:
    return {
        "schema_version": 1,
        "clone": slug,
        "environment": "dev",
        "library_version": "2026.10",
        "version": {"number": 1, "author": "Anita Rao", "reason": "first drawing"},
        "groups": [{
            "id": "g_app", "type": "boundary.vpc@1", "name": "app vpc", "kind": "vpc", "environment": "dev",
        }],
        "access_paths": [{
            "id": "ap_public", "name": "public APIs", "kind": "public-api", "environment": "dev",
        }],
        "credential_refs": [{
            "id": "cr_app", "vault_uri": "vault://tenant/acme/app", "purpose": "test connection",
        }],
        "nodes": [{
            "id": "n_api", "type": "service.backend@1", "name": "api", "environment": "dev",
            "location": "g_app", "access_path": "ap_public", "credential": "cr_app",
            "properties": {"runtime": "python 3.12"},
            "provenance": {"kind": "drawn", "source": "test"},
        }],
        "edges": [{"from": "n_api", "to": "g_app", "type": "contained_in"}],
    }


def _save(slug: str, author: str = "Anita Rao", reason: str = "first drawing") -> dict:
    return {"author": author, "reason": reason, "document": _document(slug)}


def test_create_clone(client, engineer):
    response = client.post("/api/clones", headers=engineer, json=_save("created"))
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1


def test_read_clone(client, engineer):
    created = client.post("/api/clones", headers=engineer, json=_save("readable")).json()
    response = client.get(f"/api/clones/{created['id']}", headers=engineer)
    assert response.status_code == 200
    assert response.json()["document"]["nodes"][0]["name"] == "api"


def test_list_versions(client, engineer):
    created = client.post("/api/clones", headers=engineer, json=_save("listed")).json()
    response = client.get(f"/api/clones/{created['id']}/versions", headers=engineer)
    assert response.status_code == 200
    [first] = response.json()["versions"]
    assert (first["number"], first["author"], first["reason"]) == (1, "Kavya Iyer", "first drawing")
    assert first["created_at"].endswith("Z")


def test_save_version_records_author_and_reason(client, engineer):
    created = client.post("/api/clones", headers=engineer, json=_save("versioned")).json()
    body = _save("versioned", reason="rename the api")
    body["document"]["nodes"][0]["name"] = "api v2"
    saved = client.post(f"/api/clones/{created['id']}/versions", headers=engineer, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    older = client.get(f"/api/clones/{created['id']}/versions/1", headers=engineer).json()
    assert older["nodes"][0]["name"] == "api"
    listed = client.get(f"/api/clones/{created['id']}/versions", headers=engineer).json()
    second = listed["versions"][1]
    assert (second["number"], second["author"], second["reason"]) == (2, "Kavya Iyer", "rename the api")


def test_a_version_is_signed_by_the_person_saving_it_not_by_the_request(client, engineer):
    forged = _save("signed", author="Anita Rao")
    created = client.post("/api/clones", headers=engineer, json=forged).json()
    again = client.post(f"/api/clones/{created['id']}/versions", headers=engineer, json=forged)
    assert again.status_code == 200, again.text
    listed = client.get(f"/api/clones/{created['id']}/versions", headers=engineer).json()["versions"]
    assert [row["author"] for row in listed] == ["Kavya Iyer", "Kavya Iyer"]


def test_owner_notes_and_layout_survive_a_save(client, engineer):
    created = client.post("/api/clones", headers=engineer, json=_save("placed")).json()
    body = _save("placed", reason="Moved the api")
    document = body["document"]
    document["nodes"][0].update(owner="Platform team", notes="Takes every public call.", layout={"x": 40, "y": 56})
    document["groups"][0].update(owner="Network team", notes="Holds the app.", layout={"x": 0, "y": 0, "width": 480, "height": 320})
    saved = client.post(f"/api/clones/{created['id']}/versions", headers=engineer, json=body)
    assert saved.status_code == 200, saved.text
    back = client.get(f"/api/clones/{created['id']}", headers=engineer).json()["document"]
    assert back["nodes"][0]["owner"] == "Platform team"
    assert back["nodes"][0]["notes"] == "Takes every public call."
    assert back["nodes"][0]["layout"] == {"x": 40, "y": 56}
    assert back["groups"][0]["owner"] == "Network team"
    assert back["groups"][0]["layout"] == {"x": 0, "y": 0, "width": 480, "height": 320}
    first = client.get(f"/api/clones/{created['id']}/versions/1", headers=engineer).json()
    assert "layout" not in first["nodes"][0] and "owner" not in first["nodes"][0]


def test_a_group_inside_itself_is_refused(client, engineer):
    body = _save("loop")
    body["document"]["groups"] = [
        {"id": "g_a", "type": "boundary.vpc@1", "name": "a", "kind": "vpc", "environment": "dev", "parent": "g_b"},
        {"id": "g_b", "type": "boundary.subnet@1", "name": "b", "kind": "subnet", "environment": "dev", "parent": "g_a"},
    ]
    body["document"]["nodes"][0]["location"] = "g_a"
    body["document"]["edges"] = []
    refused = client.post("/api/clones", headers=engineer, json=body)
    assert refused.status_code == 422
    assert "sits inside itself" in refused.text


def test_export_clone_document(client, engineer):
    created = client.post("/api/clones", headers=engineer, json=_save("exported")).json()
    response = client.get(f"/api/clones/{created['id']}/export", headers=engineer)
    assert response.status_code == 200
    assert validate_clone(response.json()) == []
    assert response.json()["clone"] == "exported"


def test_import_clone_document(client, engineer):
    document = _document("imported")
    response = client.post("/api/clones/import", headers=engineer, json=document)
    assert response.status_code == 200, response.text
    exported = client.get(f"/api/clones/{response.json()['id']}/export", headers=engineer).json()
    assert exported["version"]["author"] == "Kavya Iyer"
    assert exported["version"]["reason"] == "first drawing"


def test_import_rejects_a_secret_value(client, engineer):
    document = _document("secret")
    secret = "sk_live_" + "a" * 16
    document["nodes"][0]["properties"]["build_note"] = secret
    response = client.post("/api/clones/import", headers=engineer, json=document)
    assert response.status_code == 422
    assert secret not in response.text
    assert "build_note" in response.text


def test_import_rejects_a_vault_for_another_company(client, engineer):
    document = _document("foreign")
    document["credential_refs"][0]["vault_uri"] = "vault://tenant/globex/app"
    response = client.post("/api/clones/import", headers=engineer, json=document)
    assert response.status_code == 422
    assert "another company" in response.text


def test_import_rejects_a_vault_that_climbs_out(client, engineer):
    document = _document("climb")
    document["credential_refs"][0]["vault_uri"] = "vault://tenant/acme/../globex/app"
    response = client.post("/api/clones/import", headers=engineer, json=document)
    assert response.status_code == 422


def test_another_company_cannot_read_the_clone(client, engineer, operator):
    created = client.post("/api/clones", headers=engineer, json=_save("private")).json()
    assert client.get(f"/api/clones/{created['id']}", headers=operator).status_code == 404
    assert client.get(f"/api/clones/{created['id']}/export", headers=operator).status_code == 404
    assert client.get(f"/api/clones/{created['id']}/versions", headers=operator).status_code == 404


def test_export_of_template_b(client, engineer):
    document = json.loads(TEMPLATE_B.read_text().replace("vault://tenant/fintech/", "vault://tenant/acme/"))
    imported = client.post("/api/clones/import", headers=engineer, json=document)
    assert imported.status_code == 200, imported.text
    exported = client.get(f"/api/clones/{imported.json()['id']}/export", headers=engineer)
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert validate_clone(body) == []
    assert {node["name"] for node in body["nodes"]} == {node["name"] for node in document["nodes"]}
    assert {group["name"] for group in body["groups"]} == {group["name"] for group in document["groups"]}
    assert all(ref["vault_uri"].startswith("vault://tenant/acme/") for ref in body["credential_refs"])


EXAMPLES = sorted((TEMPLATE_B.parent).glob("*.clone.json"))


def _for_acme(path: Path, slug: str) -> dict:
    """An example as the demo company would import it: its own vault paths, its own clone name."""
    text = path.read_text()
    for company in ("fintech", "preventive", "proving-ground"):
        text = text.replace(f"vault://tenant/{company}/", "vault://tenant/acme/")
    document = json.loads(text)
    document["clone"] = slug
    return document


def test_every_example_comes_back_from_storage_unchanged(client, engineer):
    for index, path in enumerate(EXAMPLES):
        original = _for_acme(path, f"round-trip-{index}")
        stored = client.post("/api/clones/import", headers=engineer, json=original)
        assert stored.status_code == 200, (path.name, stored.text)
        exported = client.get(f"/api/clones/{stored.json()['id']}/export", headers=engineer).json()
        for collection in ("nodes", "edges", "groups", "access_paths", "credential_refs"):
            assert exported[collection] == original[collection], (path.name, collection)


def _two_nodes(slug: str, edge_type: str) -> dict:
    document = _document(slug)
    document["nodes"] = [
        {"id": "n_db", "type": "data.aurora-postgres@1", "name": "orders-db", "environment": "dev",
         "properties": {}, "provenance": {"kind": "drawn", "source": "test"}},
        {"id": "n_repo", "type": "repo.github@1", "name": "shop-repo", "environment": "dev",
         "properties": {}, "provenance": {"kind": "drawn", "source": "test"}},
    ]
    document["edges"] = [{"from": "n_db", "to": "n_repo", "type": edge_type}]
    document["groups"], document["access_paths"], document["credential_refs"] = [], [], []
    return document


def test_the_server_refuses_the_edges_the_studio_refuses(client, engineer):
    """Parity with studio.spec.js: Aurora cannot start, and a GitHub repository cannot receive, Publishes to."""
    refused = client.post("/api/clones", headers=engineer, json={
        "author": "Kavya Iyer", "reason": "edge rule check", "document": _two_nodes("edge-refused", "publishes_to")})
    assert refused.status_code == 422
    problems = " ".join(refused.json()["detail"])
    assert "data.aurora-postgres cannot start a 'publishes_to' edge" in problems
    assert "repo.github cannot receive a 'publishes_to' edge" in problems
    allowed = client.post("/api/clones", headers=engineer, json={
        "author": "Kavya Iyer", "reason": "edge rule check", "document": _two_nodes("edge-generic", "generic_relation")})
    assert allowed.status_code == 200, allowed.text

"""What the Studio reads: the clones of a project and every type in the node library."""

import json
from pathlib import Path

from library.resolve import load_library

EXAMPLE = Path(__file__).resolve().parents[3] / "library" / "examples" / "proving-ground.clone.json"


def _document(slug: str) -> dict:
    document = json.loads(EXAMPLE.read_text().replace("vault://tenant/proving-ground/", "vault://tenant/acme/"))
    document["clone"] = slug
    return document


def test_the_clone_list_names_each_clone_with_its_latest_version(client, engineer, ceo):
    created = client.post("/api/clones/import", headers=engineer, json=_document("studio-list")).json()
    saved = client.post(f"/api/clones/{created['id']}/versions", headers=engineer, json={
        "author": "Ravi", "reason": "second drawing", "document": _document("studio-list")})  # the author is the signed-in engineer
    assert saved.status_code == 200, saved.text
    for who in (engineer, ceo):
        listed = client.get("/api/clones", headers=who)
        assert listed.status_code == 200, listed.text
        row = next(c for c in listed.json()["clones"] if c["id"] == created["id"])
        assert row == {"id": created["id"], "clone": "studio-list", "environment": "prod", "version": 2,
                       "author": "Kavya Iyer", "reason": "second drawing", "from_connections": False}


def test_another_company_does_not_see_the_clones(client, engineer, operator):
    client.post("/api/clones/import", headers=engineer, json=_document("studio-private"))
    assert client.get("/api/clones", headers=operator).status_code == 403


def test_the_library_listing_is_every_type_as_the_library_holds_it(client, ceo):
    listed = client.get("/api/library/types", headers=ceo)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    library = load_library()
    assert {t["id"] for t in body["types"]} == set(library)
    s3 = next(t for t in body["types"] if t["id"] == "data.s3")
    assert s3 == library["data.s3"]
    assert body["library_version"] == "2026.10"

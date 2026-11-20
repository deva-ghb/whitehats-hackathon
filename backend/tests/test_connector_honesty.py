"""A connector says what it really does: reading, credentials only, or coming soon (checklist PT.1)."""


COMING_SOON = ("okta", "entra", "google", "azure_monitor", "gcp_logging", "crowdstrike", "jira")


def test_only_aws_and_github_can_be_connected_and_the_rest_say_coming_soon(client, ceo):
    rows = client.get("/api/connectors", headers=ceo).json()
    by_key = {row["key"]: row for row in rows}
    assert set(by_key) == {"aws", "cloudwatch", "github", *COMING_SOON}
    for key in ("aws", "cloudwatch", "github"):
        assert by_key[key]["status"] == "available", key
    for key in COMING_SOON:
        assert by_key[key]["status"] == "coming_soon", key
        refused = client.put(f"/api/connectors/{by_key[key]['id']}", headers=ceo, json={"values": {
            "site": "https://acme.atlassian.net", "project": "SEC", "token": "unit-test-token",
            "region": "us-1", "client_id": "cid", "domain": "acme.okta.com", "tenant_id": "t",
            "workspace_id": "w", "project_id": "p",
        }})
        assert refused.status_code == 409, key
        assert refused.json()["detail"].endswith("is coming soon. Only AWS and GitHub can be connected in this version.")
        assert client.post(f"/api/connectors/{by_key[key]['id']}/sync", headers=ceo).status_code == 409, key
        stored = next(row for row in client.get("/api/connectors", headers=ceo).json() if row["key"] == key)
        assert stored["status"] == "coming_soon"
        assert stored["has_secret"] is False


def test_a_coming_soon_source_is_never_read_in_the_background(client, monkeypatch):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Connector, Organization
    from app.services import cloudlogs

    assert client
    read = []
    monkeypatch.setattr(cloudlogs, "sync", lambda db, c: read.append(c.key) or 0)
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        azure = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "azure_monitor"))
        before = (azure.status, azure.last_sync_at)
        azure.status = "reading"
        db.commit()
        try:
            cloudlogs.sync_all(db)
        finally:
            azure.status, azure.last_sync_at = before
            db.commit()
    assert "azure_monitor" not in read


def test_github_is_connected_by_the_app_and_never_by_a_token(client, ceo):
    rows = client.get("/api/connectors", headers=ceo).json()
    github = next(row for row in rows if row["key"] == "github")
    assert github["fields"] == []
    refused = client.put(f"/api/connectors/{github['id']}", headers=ceo, json={"values": {
        "org": "acme", "token": "unit-test-token"}})
    assert refused.status_code == 400
    assert "installing the WhiteHats GitHub App" in refused.json()["detail"]
    again = next(row for row in client.get("/api/connectors", headers=ceo).json() if row["key"] == "github")
    assert again["status"] == "available" and again["has_secret"] is False


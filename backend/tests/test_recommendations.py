"""REC.1: what the customer could switch on so WhiteHats sees more, shown in the Clone Studio."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Connector, Organization, Project
from app.services import cloud_settings, recommendations


def test_a_network_without_a_readable_traffic_log_and_amazons_services_switched_off_are_recommended(
        client, ceo, monkeypatch):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id).order_by(Project.id))
        c = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        c.state = {"flow_logs": [
            {"vpc": "vpc-1", "name": "shop", "region": "ap-southeast-2", "destination": "none"},
            {"vpc": "vpc-2", "name": "admin", "region": "ap-southeast-2", "destination": "s3"},
            {"vpc": "vpc-3", "name": "read", "region": "ap-southeast-2", "destination": "logs"}]}
        cloud_settings.row_for(db, c).status = {"status": "done",
                                                "amazon": {"guardduty": "off", "securityhub": "unreadable"}}
        db.flush()
        pid = project.id
        monkeypatch.setattr(recommendations.routing, "projects_for", lambda *a: [pid])
        items = recommendations.for_project(db, org.id, project.id)
        db.rollback()
    assert [(i["id"], i["severity"]) for i in items] == [
        ("flow-off-vpc-1", "high"), ("flow-bucket-vpc-2", "medium"), ("role-update", "medium"), ("guardduty-off", "low")]
    assert items[0]["title"] == "Switch on traffic logging for network shop" and items[0]["resource"] == "vpc-1"
    assert all(i["why"] and i["how"] and i["cost"] for i in items)
    assert "items" in client.get("/api/recommendations", headers=ceo).json()

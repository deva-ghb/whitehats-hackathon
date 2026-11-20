"""COV.1: coverage is measured against the map: of the things a WhiteHat should watch, the share its data reaches."""

from sqlalchemy import select

from app.db import SessionLocal, utcnow
from app.models import Clone, CloneVersion, Collector, Connector, Node, Organization, Project
from app.services import coverage


def _map(db, project, nodes):
    clone = Clone(org_id=project.org_id, project_id=project.id, slug="cov-test", environment="prod",
                  library_version="1")
    db.add(clone)
    db.flush()
    version = CloneVersion(org_id=project.org_id, project_id=project.id, clone_id=clone.id, number=1, author="t",
                           reason="t", environment="prod", library_version="1")
    db.add(version)
    db.flush()
    for i, (kind, name, props) in enumerate(nodes):
        db.add(Node(org_id=project.org_id, project_id=project.id, clone_version_id=version.id, node_key=f"n{i}",
                    type=f"{kind}@1", name=name, environment="prod", properties=props))
    db.flush()


def test_coverage_counts_what_the_data_reaches_and_is_none_with_nothing_to_watch(client):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = Project(org_id=org.id, name="Coverage test", slug="coverage-test")  # its own map; all rolled back
        db.add(project)
        db.flush()
        aws = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        aws.last_sync_at, aws.state = None, {}  # not reading yet (all of this is rolled back)
        for col in db.scalars(select(Collector).where(Collector.org_id == org.id)):
            col.last_heartbeat = None  # none online yet
        _map(db, project, [
            ("compute.vm", "admin-box", {"arn": "arn:aws:ec2:x:1:instance/i-abc", "instance_id": "i-abc"}),
            ("compute.vm", "build-box", {"arn": "arn:aws:ec2:x:1:instance/i-def"}),
            ("boundary.vpc", "main", {"arn": "arn:aws:ec2:x:1:vpc/vpc-1", "vpc_id": "vpc-1"}),
            ("data.s3", "receipts", {"arn": "arn:aws:s3:::receipts"}),
            ("boundary.aws-account", "proving ground", {})])
        before = coverage.measure(db, org.id, project.id, ["cloud", "network", "identity", "insider"])
        assert before["cloud"] == {"percent": 0, "seen": 0, "total": 4,
                                   "dark": ["admin-box", "build-box", "main", "receipts"]}
        assert before["insider"]["percent"] is None  # nothing on the map for it to watch
        aws.last_sync_at, aws.state = utcnow(), {"flow_logs": [{"vpc": "vpc-1", "destination": "logs"}]}
        db.add(Collector(org_id=org.id, name="net", kind="network", mode="docker", host="i-abc", token_hash="cov",
                         status="running", last_heartbeat=utcnow(), interval_s=15))
        db.flush()
        after = coverage.measure(db, org.id, project.id, ["cloud", "network", "identity"])
        db.rollback()
    assert after["cloud"]["percent"] == 100
    assert (after["network"]["seen"], after["network"]["total"], after["network"]["dark"]) == (2, 3, ["build-box"])
    assert after["identity"]["percent"] == 100  # the account's sign-ins come with its activity log

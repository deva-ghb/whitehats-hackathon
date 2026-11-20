"""Cloud Posture reads AWS activity: each risky change is raised for the resource it touched, a second resource gets its
own signal, and undoing the change in AWS closes the signal (so the map's building goes back to normal)."""

import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Agent, Organization, Signal
from app.whitehats.base import Context
from app.whitehats.builtin import CloudPosture, resource_of


def _event(event_name, **params):
    return {"message": json.dumps({"eventName": event_name, "awsRegion": "ap-southeast-2", "recipientAccountId": "1",
                                   "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::1:user/dev"},
                                   "requestParameters": params})}


def _batches(*batches):
    """Run each batch through Cloud Posture in one session; return (raised titles+resources, open signals)."""
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        agent = db.scalar(select(Agent).where(Agent.org_id == org.id, Agent.key == "cloud"))
        raised = []
        for lines in batches:
            ctx = Context(db=db, org_id=org.id, agent=agent)
            CloudPosture().on_log_batch(ctx, "aws", lines)
            db.flush()
            raised += [(s.title, s.resource) for s in ctx.raised]
        still_open = [(s.title, s.resource) for s in db.scalars(select(Signal).where(
            Signal.org_id == org.id, Signal.source == "whitehat:cloud", Signal.closed_at.is_(None),
            Signal.resource.in_(["trail-a", "bucket-a", "bucket-b", "sg-1"])))]
        db.rollback()
        return raised, still_open


def test_the_resource_a_change_touched_is_named_as_the_map_names_it():
    assert resource_of({"requestParameters": {"name": "arn:aws:cloudtrail:ap-southeast-2:1:trail/trail-a"}}) == "trail-a"
    assert resource_of({"requestParameters": {"bucketName": "bucket-a"}}) == "bucket-a"
    assert resource_of({"requestParameters": {"queueUrl": "https://sqs.x/1/jobs"}}) == "jobs"
    assert resource_of({"resources": [{"ARN": "arn:aws:kms:x:1:key/k"}]}) == "arn:aws:kms:x:1:key/k"
    assert resource_of({}) == ""


def test_two_buckets_made_public_raise_two_signals_and_logging_switched_off_one(client):
    raised, _still_open = _batches([
        _event("StopLogging", name="trail-a"),
        _event("PutBucketPublicAccessBlock", bucketName="bucket-a", PublicAccessBlockConfiguration={
            "BlockPublicAcls": False, "IgnorePublicAcls": False, "BlockPublicPolicy": False,
            "RestrictPublicBuckets": False}),
        _event("PutBucketPolicy", bucketName="bucket-b"),
        _event("PutBucketPolicy", bucketName="bucket-b"),  # the same change again is not raised twice
    ])
    assert raised == [("Cloud audit logging was switched off", "trail-a"),
                      ("Public access settings on a bucket changed", "bucket-a"),
                      ("A storage bucket policy changed", "bucket-b")]


def test_undoing_a_change_in_aws_closes_its_signal_and_a_safer_setting_raises_nothing(client):
    on = {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}
    raised, still_open = _batches(
        [_event("StopLogging", name="trail-a"),
         _event("PutBucketPublicAccessBlock", bucketName="bucket-a", PublicAccessBlockConfiguration={
             **on, "BlockPublicPolicy": False}),
         _event("AuthorizeSecurityGroupIngress", groupId="sg-1", ipPermissions={"cidr": "0.0.0.0/0"})],
        [_event("StartLogging", name="arn:aws:cloudtrail:ap-southeast-2:1:trail/trail-a"),
         _event("PutBucketPublicAccessBlock", bucketName="bucket-a", PublicAccessBlockConfiguration=on),
         _event("RevokeSecurityGroupIngress", groupId="sg-1")],
    )
    assert len(raised) == 3
    assert still_open == []


def test_a_change_made_again_after_it_was_undone_is_raised_again(client):
    # Opened, closed, opened again within the hour: the second opening is a new problem, not a repeat of a closed one.
    raised, still_open = _batches(
        [_event("AuthorizeSecurityGroupIngress", groupId="sg-1", ipPermissions={"cidr": "0.0.0.0/0"})],
        [_event("RevokeSecurityGroupIngress", groupId="sg-1")],
        [_event("AuthorizeSecurityGroupIngress", groupId="sg-1", ipPermissions={"cidr": "0.0.0.0/0"})],
    )
    opened = ("A firewall rule now allows traffic from the whole internet", "sg-1")
    assert raised == [opened, opened]
    assert still_open == [opened]


def test_an_open_alert_is_placed_on_the_building_it_concerns(client):
    from app.models import Project
    from app.services import map_alerts
    document = {"nodes": [
        {"id": "n-bucket", "name": "receipts", "properties": {"arn": "arn:aws:s3:::pg-receipts"}},
        {"id": "n-trail", "name": "proving-ground trail",
         "properties": {"arn": "arn:aws:cloudtrail:ap-southeast-2:1:trail/trail-a"}},
        {"id": "n-other", "name": "orders", "properties": {}}]}
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id))
        for title, resource, closed in (("Public access settings on a bucket changed", "pg-receipts", False),
                                        ("Cloud audit logging was switched off", "trail-a", False),
                                        ("A storage bucket policy changed", "pg-receipts", True),
                                        ("A new access key was created", "someone", False)):
            db.add(Signal(org_id=org.id, project_id=project.id, severity="high", title=title, resource=resource,
                          source="whitehat:cloud", closed_reason="undone" if closed else "",
                          closed_at=__import__("app.db", fromlist=["utcnow"]).utcnow() if closed else None))
        db.flush()
        placed = map_alerts.for_clone(db, project.id, document)
        db.rollback()
    assert [a["title"] for a in placed["nodes"]["n-bucket"]] == ["Public access settings on a bucket changed"]
    assert [a["title"] for a in placed["nodes"]["n-trail"]] == ["Cloud audit logging was switched off"]
    assert "n-other" not in placed["nodes"]
    assert "someone" in [a["resource"] for a in placed["unplaced"]]


def _signin(result, mfa="Yes", kind="IAMUser", user="dev"):
    return {"source": "cloudtrail:aws", "message": json.dumps({
        "eventName": "ConsoleLogin", "awsRegion": "ap-southeast-2", "recipientAccountId": "1",
        "sourceIPAddress": "203.0.113.9", "userIdentity": {"type": kind, "userName": user,
                                                            "arn": f"arn:aws:iam::1:user/{user}"},
        "responseElements": {"ConsoleLogin": result}, "additionalEventData": {"MFAUsed": mfa}})}


def test_identity_flags_a_console_sign_in_without_multi_factor_and_a_burst_of_failures(client):
    from app.whitehats.builtin import Identity
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        agent = db.scalar(select(Agent).where(Agent.org_id == org.id, Agent.key == "identity"))
        ctx = Context(db=db, org_id=org.id, agent=agent)
        Identity().on_log_batch(ctx, "aws", [_signin("Success", mfa="No"), _signin("Success", mfa="Yes", user="ok"),
                                             *[_signin("Failure", user="target") for _ in range(5)],
                                             _signin("Failure", user="once")])
        raised = [(s.severity, s.title, s.resource) for s in ctx.raised]
        db.rollback()
    assert raised == [("medium", "Signed in to the AWS console without multi-factor", "dev"),
                      ("high", "5 failed AWS console sign-ins for arn:aws:iam::1:user/target",
                       "arn:aws:iam::1:user/target")]


def _flow(src, dst, sport, dport, sent=900, action="ACCEPT"):
    return {"source": "vpcflow:vpc-1", "message": f"2 1 eni-1 {src} {dst} {sport} {dport} 6 10 {sent} 1 2 {action} OK"}


def test_the_network_whitehat_reads_traffic_logs_for_open_ports_odd_outgoing_and_large_transfers(client):
    from app.whitehats.builtin import Network
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        agent = db.scalar(select(Agent).where(Agent.org_id == org.id, Agent.key == "network"))
        ctx = Context(db=db, org_id=org.id, agent=agent)
        Network().on_log_batch(ctx, "aws", [
            _flow("198.51.100.4", "10.0.1.5", 51000, 5432),  # the internet reached a database
            _flow("198.51.100.4", "10.0.1.5", 51000, 5432, action="REJECT"),  # blocked: nothing to say
            _flow("10.0.1.5", "203.0.113.7", 40000, 4444),  # odd outgoing port
            _flow("10.0.1.5", "203.0.113.8", 443, 51000),  # a reply to a visitor: not judged
            _flow("10.0.1.6", "203.0.113.9", 40001, 443, sent=1_500_000_000),  # a big upload
            _flow("10.0.1.5", "10.0.2.9", 40000, 4444),  # inside the network: not judged
            {"source": "cloudwatch:/app", "message": "2 1 eni-1 1.2.3.4 10.0.0.1 1 22 6 1 1 1 2 ACCEPT OK"}])
        raised = [(s.severity, s.title, s.resource) for s in ctx.raised]
        db.rollback()
    assert raised == [("high", "Traffic from the internet reached PostgreSQL (port 5432)", "vpc-1"),
                      ("medium", "Unusual outgoing traffic to port 4444", "vpc-1"),
                      ("medium", "Large data transfer out to the internet", "vpc-1")]


def test_every_public_access_block_switched_off_is_high_and_one_of_them_medium(client):
    off = {"BlockPublicAcls": False, "IgnorePublicAcls": False, "BlockPublicPolicy": False, "RestrictPublicBuckets": False}
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        agent = db.scalar(select(Agent).where(Agent.org_id == org.id, Agent.key == "cloud"))
        ctx = Context(db=db, org_id=org.id, agent=agent)
        CloudPosture().on_log_batch(ctx, "aws", [
            _event("PutBucketPublicAccessBlock", bucketName="all-off", PublicAccessBlockConfiguration=off),
            _event("PutBucketPublicAccessBlock", bucketName="one-off",
                   PublicAccessBlockConfiguration={**{k: True for k in off}, "BlockPublicPolicy": False}),
            _event("DeleteBucketPublicAccessBlock", bucketName="deleted")])
        raised = [(s.resource, s.severity) for s in ctx.raised]
        db.rollback()
    assert raised == [("all-off", "high"), ("one-off", "medium"), ("deleted", "high")]

"""CS.1: the live settings of an AWS account, checked by Prowler. Shapes are Prowler 5.45's JSON-OCSF findings."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal, utcnow
from app.models import Connector, Incident, IncidentEvent, Organization, Project, SettingsCheck, Signal
from app.services import cloud_settings


@pytest.fixture(autouse=True)
def _no_outgoing_rules(monkeypatch):
    """The scans here run a fake Prowler on a fake connection; the outgoing-rules check and Amazon's findings are
    tested on their own."""
    monkeypatch.setattr(cloud_settings, "_outgoing", lambda *a, **k: [])
    monkeypatch.setattr(cloud_settings.amazon_findings, "read", lambda *a, **k: ({}, []))


def _finding(check, resource, severity="High", title="A setting is unsafe"):
    return {"status_code": "FAIL", "severity": severity, "message": f"{title}. Seen on {resource}.",
            "finding_info": {"uid": f"prowler-aws-{check}-123-ap-southeast-2-{resource}", "title": f"{check} passes"},
            "remediation": {"desc": "Put it right."},
            "resources": [{"data": {"metadata": {"arn": f"arn:aws:s3:::{resource}", "name": resource}}}]}


def _event(source, read_only=False):
    return {"source": "cloudtrail:ap-southeast-2/X", "message": json.dumps({"eventSource": source, "readOnly": read_only})}


def test_a_change_in_the_activity_history_asks_for_that_service_to_be_checked_again(client):
    assert cloud_settings.service_of("s3.amazonaws.com") == "s3"
    assert cloud_settings.service_of("monitoring.amazonaws.com") == "cloudwatch"
    assert cloud_settings.service_of("signin.example") == ""
    lines = [_event("s3.amazonaws.com"), _event("ec2.amazonaws.com", read_only=True), _event("iam.amazonaws.com")]
    assert cloud_settings.changed_services(lines) == {"s3", "iam"}
    with SessionLocal() as db:
        c = db.scalar(select(Connector).where(Connector.key == "aws"))
        row = cloud_settings.row_for(db, c)
        before = list(row.pending or [])
        row.pending = ["kms"]
        cloud_settings.note_changes(db, c, lines)
        assert row.pending == ["iam", "kms", "s3"]
        db.rollback()
        assert before is not None


def _check(status=None, pending=None):
    return SettingsCheck(connector_id=0, org_id=0, status=status or {}, pending=pending or [])


def test_the_whole_account_is_checked_hourly_and_a_changed_service_between_times():
    now = utcnow()
    assert cloud_settings.due(_check(), now) == []
    fresh = {"status": "done", "full_at": (now - timedelta(minutes=10)).isoformat()}
    assert cloud_settings.due(_check(fresh), now) is None
    assert cloud_settings.due(_check(fresh, ["s3"]), now) == ["s3"]
    stale = {"status": "done", "full_at": (now - timedelta(minutes=61)).isoformat()}
    assert cloud_settings.due(_check(stale), now) == []
    assert cloud_settings.due(_check({"status": "running"}), now) is None


def test_a_failed_check_opens_a_signal_on_its_resource_and_closes_when_it_passes(client, monkeypatch):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id))
        c = Connector(org_id=org.id, key="aws", name="AWS", category="Cloud logs", config={"region": "ap-southeast-2"})
        monkeypatch.setattr(cloud_settings.routing, "projects_for", lambda *a: [project.id])
        public = _finding("s3_bucket_level_public_access_block", "receipts", title="Bucket public access block off")
        trail = _finding("cloudtrail_multi_region_enabled", "trail", title="No trail is logging")
        try:
            assert cloud_settings.apply(db, c, [public, trail], []) == (2, 0)
            rows = list(db.scalars(select(Signal).where(Signal.fingerprint.like("prowler-%"), Signal.closed_at.is_(None))))
            assert {(r.title, r.resource, r.severity, r.source) for r in rows} == {
                ("Bucket public access block off", "arn:aws:s3:::receipts", "high", "whitehat:cloud"),
                ("No trail is logging", "arn:aws:s3:::trail", "high", "whitehat:cloud")}
            # S3 alone checked again, the bucket put right: its signal closes; the trail's, outside the check, stays open
            assert cloud_settings.apply(db, c, [], ["s3"]) == (0, 1)
            still = [r.title for r in db.scalars(select(Signal).where(Signal.fingerprint.like("prowler-%"),
                                                                        Signal.closed_at.is_(None)))]
            assert still == ["No trail is logging"]
            # A GuardDuty finding stays open through a whole-account check that could not read GuardDuty (AMZ.1)...
            threat = _finding("guardduty_unauthorizedaccess", "dev", title="GuardDuty: a key used from a bad address")
            assert cloud_settings.apply(db, c, [threat], []) == (1, 1)  # a whole-account check: the trail is put right
            assert cloud_settings.apply(db, c, [], [], unknown=("guardduty",)) == (0, 0)
            # ...and closes once GuardDuty is read again without it
            assert cloud_settings.apply(db, c, [], []) == (0, 1)
        finally:
            # apply() commits; what it opened must not outlive the test (the background loop would act on it)
            db.rollback()
            mine = db.query(Signal).filter(Signal.org_id == org.id, Signal.fingerprint.like("prowler-%"))
            incidents = {s.incident_id for s in mine if s.incident_id}
            mine.delete()
            db.query(IncidentEvent).filter(IncidentEvent.incident_id.in_(incidents)).delete()
            db.query(Incident).filter(Incident.id.in_(incidents)).delete()
            db.commit()


def test_prowler_runs_the_critical_and_high_checks_with_only_the_roles_short_lived_credentials(monkeypatch):
    seen = {}

    def run(argv, env, **kwargs):
        seen["argv"], seen["env"] = argv, env
        folder = argv[argv.index("-v") + 1].split(":", 1)[0]
        (cloud_settings.Path(folder) / "found.ocsf.json").write_text(json.dumps([_finding("s3_x", "b")]))
        return cloud_settings.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cloud_settings.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cloud_settings.subprocess, "run", run)
    found = cloud_settings.run_prowler({"AccessKeyId": "key-id-1", "SecretAccessKey": "secret-2", "SessionToken": "token-3"},
                                       "ap-southeast-2", ["s3"])
    assert len(found) == 1
    argv = seen["argv"]
    assert argv[argv.index("--severity") + 1:argv.index("--severity") + 3] == ["critical", "high"]
    assert argv[argv.index("--services") + 1] == "s3" and "prowlercloud/prowler:5.45.0" in argv
    line = " ".join(argv)  # credentials never appear on the command line, only in the container's environment
    assert not any(v in line for v in ("key-id-1", "secret-2", "token-3"))
    assert seen["env"]["AWS_SESSION_TOKEN"] == "token-3"


def test_nothing_found_is_a_clean_result_but_refused_checks_never_read_as_all_clear(monkeypatch):
    import pytest
    answers = iter([("Scan completed! There are no findings", ""),
                    ("Scan completed!", "botocore AccessDenied when calling GetBucketPublicAccessBlock")])

    def run(argv, env, **kwargs):
        out, err = next(answers)
        return cloud_settings.subprocess.CompletedProcess(argv, 0, out, err)

    monkeypatch.setattr(cloud_settings.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cloud_settings.subprocess, "run", run)
    creds = {"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c"}
    assert cloud_settings.run_prowler(creds, "ap-southeast-2", []) == []
    with pytest.raises(cloud_settings.MissingPermissions):
        cloud_settings.run_prowler(creds, "ap-southeast-2", [])


def test_a_scan_cut_off_by_a_restart_runs_again_and_a_scan_keeps_to_the_connections_tag(monkeypatch):
    now = utcnow()
    stuck = {"status": "running", "started_at": (now - timedelta(hours=2)).isoformat()}
    assert cloud_settings.due(_check(stuck), now) == []
    seen = {}

    def run(argv, env, **kwargs):
        seen["argv"] = argv
        return cloud_settings.subprocess.CompletedProcess(argv, 0, "no findings", "")

    monkeypatch.setattr(cloud_settings.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cloud_settings.subprocess, "run", run)
    cloud_settings.run_prowler({"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c"}, "ap-southeast-2",
                               [], tag=("project", "whitehats-proving-ground"))
    argv = seen["argv"]
    assert argv[argv.index("--resource-tag") + 1] == "project=whitehats-proving-ground"


def test_a_check_cut_off_by_a_restart_runs_again(client):
    now = utcnow()
    running = lambda services: {"status": "running", "started_at": (now - timedelta(minutes=5)).isoformat(),
                                "services": services, "full_at": (now - timedelta(minutes=20)).isoformat()}
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        c = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        row = cloud_settings.row_for(db, c)
        before = (dict(row.status or {}), list(row.pending or []))
        try:
            row.status, row.pending = running(["s3"]), []
            db.commit()
            assert cloud_settings.due(row, now) is None  # it reads as still running
            assert cloud_settings.resume_after_restart(db) >= 1
            db.refresh(row)
            assert row.status["status"] == "interrupted"
            assert cloud_settings.due(row, now) == ["s3"]  # its service is due again at once
            row.status = running([])
            db.commit()
            cloud_settings.resume_after_restart(db)
            db.refresh(row)
            assert cloud_settings.due(row, now) == []  # a cut-off full check makes the whole account due
        finally:
            row.status, row.pending = before
            db.commit()


def test_a_service_asked_for_while_a_check_runs_is_not_lost(client, monkeypatch):
    """The activity reader asks for a service to be checked while a check of another service is running: when that
    check writes its result, the request is still there."""
    monkeypatch.setattr(cloud_settings, "run_prowler", lambda *a, **k: [])
    with SessionLocal() as db, SessionLocal() as reader:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        c = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        row = cloud_settings.row_for(db, c)
        before = (dict(row.status or {}), list(row.pending or []))
        row.pending = []
        db.commit()
        source = type("Source", (), {"credentials": lambda self: {}})()
        monkeypatch.setattr(cloud_settings.connectors, "for_row", lambda _db, _c: source)

        def meanwhile(*_a, **_k):
            other = reader.get(Connector, c.id)
            cloud_settings.note_changes(reader, other, [_event("ec2.amazonaws.com")])
            reader.commit()
            return []

        monkeypatch.setattr(cloud_settings, "run_prowler", meanwhile)
        try:
            cloud_settings.scan(db, c, ["ssm"])
            db.refresh(row)
            assert row.pending == ["ec2"] and row.status["status"] == "done"
        finally:
            row.status, row.pending = before
            db.commit()


def test_an_alert_says_what_is_wrong_not_the_name_of_the_check():
    check = {"finding_info": {"title": "S3 bucket has Block Public Access enabled"}}
    assert cloud_settings._title({**check, "message": "Block Public Access is not configured for the S3 Bucket "
                                  "receipts.\n"}) == "Block Public Access is not configured for the S3 Bucket receipts"
    assert cloud_settings._title({**check, "message": "Potential secrets found in the image of repository worker -> "
                                  "Generic Password in file ./a. More."}) == (
        "Potential secrets found in the image of repository worker")
    assert cloud_settings._title({**check, "message": ""}) == "S3 bucket has Block Public Access enabled"


def test_identity_is_checked_across_the_account_and_the_rest_by_the_connections_tag(client, monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_settings, "run_prowler",
                        lambda creds, region, services, tag=None, skip=(): calls.append((services, tag, tuple(skip))) or [])
    source = type("Source", (), {"credentials": lambda self: {}})()
    monkeypatch.setattr(cloud_settings.connectors, "for_row", lambda _db, _c: source)
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        c = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        config, row = dict(c.config), cloud_settings.row_for(db, c)
        before = (dict(row.status or {}), list(row.pending or []))
        c.config = {**config, "tag_key": "project", "tag_value": "pg"}
        db.commit()
        try:
            cloud_settings.scan(db, c, [])
            assert calls == [([], ("project", "pg"), ("iam",)), (["iam"], None, ())]
            calls.clear()
            cloud_settings.scan(db, c, ["ec2"])
            assert calls == [(["ec2"], ("project", "pg"), ("iam",))]
            calls.clear()
            cloud_settings.scan(db, c, ["iam"])
            assert calls == [(["iam"], None, ())]  # an attacker's new user is never tagged
        finally:
            c.config = config
            row.status, row.pending = before
            db.commit()


def test_the_first_look_at_a_scope_is_backlog_and_later_looks_are_news(client, monkeypatch):
    found = {"tagged": [_finding("ec2_sg_open", "sg-1")], "iam": [_finding("iam_admin", "dev")]}
    monkeypatch.setattr(cloud_settings, "run_prowler", lambda creds, region, services, tag=None, skip=():
                        found["iam" if services == ["iam"] else "tagged"])
    monkeypatch.setattr(cloud_settings.connectors, "for_row",
                        lambda _db, _c: type("Source", (), {"credentials": lambda self: {}})())
    seen = []
    monkeypatch.setattr(cloud_settings, "apply", lambda db, c, findings, services, unknown=(): seen.append(
        sorted((f["finding_info"]["uid"].split("-")[2], bool(f.get("_baseline"))) for f in findings)) or (0, 0))
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        c = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        config, row = dict(c.config), cloud_settings.row_for(db, c)
        before = (dict(row.status or {}), list(row.pending or []))
        c.config, row.status = {**config, "tag_key": "project", "tag_value": "pg"}, {}
        db.commit()
        try:
            cloud_settings.scan(db, c, [])
            cloud_settings.scan(db, c, [])
            assert seen == [[("ec2_sg_open", True), ("iam_admin", True)], [("ec2_sg_open", False), ("iam_admin", False)]]
        finally:
            c.config = config
            row.status, row.pending = before
            db.commit()


def test_only_services_prowler_knows_are_asked_for_and_a_failure_waits_before_trying_again():
    assert cloud_settings.service_of("signin.amazonaws.com") == ""  # a console sign-in has no settings to check
    assert cloud_settings.service_of("lambda.amazonaws.com") == "awslambda"
    assert cloud_settings.service_of("events.amazonaws.com") == "eventbridge"
    now = utcnow()
    done = (now - timedelta(minutes=5)).isoformat()
    fresh = {"full_at": (now - timedelta(minutes=10)).isoformat()}
    assert cloud_settings.due(_check({**fresh, "status": "done"}, ["s3", "signin"]), now) == ["s3"]
    assert cloud_settings.due(_check({**fresh, "status": "failed", "finished_at": done}, ["s3"]), now) is None
    later = (now - timedelta(minutes=11)).isoformat()
    assert cloud_settings.due(_check({**fresh, "status": "failed", "finished_at": later}, ["s3"]), now) == ["s3"]


class _Groups:
    """Answers the two reads the outgoing-rules check makes, from fixed security groups."""

    def __init__(self, groups, used):
        self.groups, self.used, self.calls = groups, used, []

    def _session(self):
        return "arn:aws:sts::123:assumed-role/whitehats-readonly/whitehats-reader"

    def _call(self, service, method, *, region="", **kwargs):
        self.calls.append((service, method, kwargs))
        if method == "describe_security_groups":
            return {"SecurityGroups": self.groups}
        return {"NetworkInterfaces": [{"Groups": [{"GroupId": g}]} for g in self.used]}


def _group(gid, *rules):
    return {"GroupId": gid, "GroupName": f"name-{gid}", "IpPermissionsEgress": list(rules)}


def test_a_group_in_use_that_lets_everything_out_to_anywhere_is_raised_and_nothing_else():
    from app.services import cloud_settings, firewall
    everything = {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    v6 = {"IpProtocol": "-1", "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}
    https = {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    inside = {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "10.0.0.0/16"}]}
    source = _Groups([_group("sg-open", everything), _group("sg-v6", v6), _group("sg-https", https),
                      _group("sg-inside", inside), _group("sg-unused", everything)],
                     used=["sg-open", "sg-v6", "sg-https", "sg-inside"])
    found = firewall.findings(source, "ap-southeast-2", tag=("project", "pg"))
    assert [f["resources"][0]["uid"].rsplit("/", 1)[1] for f in found] == ["sg-open", "sg-v6"]
    first = found[0]
    assert first["finding_info"]["uid"] == f"prowler-aws-{firewall.CHECK}-123-ap-southeast-2-sg-open"
    assert cloud_settings.check_of(first) == firewall.CHECK and cloud_settings._title(first).startswith(
        "Security group name-sg-open (sg-open) lets everything it protects reach any address")
    assert source.calls[0][2]["Filters"] == [{"Name": "tag:project", "Values": ["pg"]}]  # the connection's scope


def test_only_the_network_medium_checks_are_kept_beside_critical_and_high():
    from app.services.cloud_settings import kept

    def finding(check, severity):
        return {"severity": severity, "finding_info": {"uid": f"prowler-aws-{check}-123-ap-southeast-2-x"}}
    assert kept(finding("s3_bucket_public_access", "High"))
    assert kept(finding("vpc_flow_logs_enabled", "Medium"))
    assert kept(finding("ec2_networkacl_allow_ingress_tcp_port_22", "Medium"))
    assert not kept(finding("ec2_securitygroup_from_launch_wizard", "Medium"))
    assert not kept(finding("ec2_networkacl_unused", "Low"))

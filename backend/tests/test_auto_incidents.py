"""Serious problems in a connected cloud account open an incident by themselves and resolve it when put right."""

from sqlalchemy import select

from app.db import SessionLocal, utcnow
from app.models import Incident, IncidentEvent, Organization, Project, Signal
from app.services import auto_incidents


def _signal(db, org, project, title, severity="high", resource="arn:aws:s3:::receipts", source="whitehat:cloud"):
    s = Signal(org_id=org.id, project_id=project.id, severity=severity, title=title, detail=f"{title}.",
               entity="ap-southeast-2", resource=resource, source=source, fingerprint=f"t-{title}", origin="live")
    db.add(s)
    db.commit()
    return s


def test_a_serious_cloud_problem_opens_one_incident_per_resource_and_resolves_when_put_right(client):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id))
        made = [
            _signal(db, org, project, "Block Public Access is not configured for the bucket receipts"),
            _signal(db, org, project, "The bucket receipts can be read by anyone", severity="critical"),
            _signal(db, org, project, "Default encryption is off", severity="medium", resource="arn:aws:s3:::logs"),
            _signal(db, org, project, "A secret in a file", source="whitehat:code", resource="app.py"),
        ]
        try:
            ids = [s.id for s in made]
            assert auto_incidents.open_new(db, ids) == 1
            for s in made:
                db.refresh(s)
            inc = db.get(Incident, made[0].incident_id)
            assert made[1].incident_id == inc.id  # the second problem on the bucket joins its incident
            assert made[2].incident_id is None and made[3].incident_id is None  # medium, and not the cloud: by hand
            context = _signal(db, org, project, "Public access settings on a bucket changed", severity="medium",
                              resource="arn:aws:s3:::receipts")
            made.append(context)
            loud = _signal(db, org, project, "The bucket receipts has a public policy", resource="receipts")
            made.append(loud)
            auto_incidents.open_new(db, [loud.id])
            db.refresh(context)
            assert context.incident_id == inc.id  # the same resource's quieter signal joins its incident
            sample = _signal(db, org, project, "A sample bucket problem", resource="arn:aws:s3:::sample")
            sample.origin = "sample"
            db.commit()
            made.append(sample)
            assert auto_incidents.open_new(db, [sample.id]) == 0  # sample data never opens one
            assert inc.severity == "critical" and inc.status == "open" and inc.ref.startswith("INC-")
            assert inc.events[0].actor == "WhiteHats"
            assert auto_incidents.open_new(db, ids) == 0  # nothing twice
            auto_incidents.retitle(db, made[0], "Anyone can read the bucket receipts")
            assert inc.title == "Anyone can read the bucket receipts"  # it follows its first problem's wording

            made[0].closed_at, made[0].closed_reason = utcnow(), "setting put right"
            db.commit()
            assert auto_incidents.resolve_fixed(db, [inc.id]) == 0
            db.refresh(inc)
            assert inc.status == "open"  # one problem on the bucket is still there
            for s in (made[1], context, loud):
                s.closed_at, s.closed_reason = utcnow(), "setting put right"
            db.commit()
            assert auto_incidents.resolve_fixed(db, [inc.id]) == 1
            db.refresh(inc)
            assert inc.status == "resolved" and inc.resolved_at is not None
            assert "setting put right" in inc.events[-1].text
        finally:
            ids = {s.incident_id for s in made if s.incident_id}
            for s in made:
                db.delete(s)
            db.flush()
            for iid in ids:
                db.query(IncidentEvent).filter(IncidentEvent.incident_id == iid).delete()
                db.query(Incident).filter(Incident.id == iid).delete()
            db.commit()


def test_an_incident_a_person_opened_is_resolved_too_when_whitehats_finds_its_findings_gone(client):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id))
        inc = Incident(org_id=org.id, project_id=project.id, ref="INC-9901", title="Opened by a person", severity="high",
                       status="investigating")
        db.add(inc)
        db.flush()
        s = _signal(db, org, project, "Logging was switched off", resource="arn:aws:cloudtrail:trail")
        s.incident_id = inc.id
        db.commit()
        try:
            assert auto_incidents.resolve_fixed(db, [inc.id]) == 0  # its finding is still open
            s.closed_at, s.closed_reason = utcnow(), "undone in AWS: StartLogging"
            db.commit()
            assert auto_incidents.resolve_fixed(db, [inc.id]) == 1
            db.refresh(inc)
            assert inc.status == "resolved" and "undone in AWS" in inc.events[-1].text
        finally:
            db.rollback()
            db.query(Signal).filter(Signal.id == s.id).delete()
            db.query(IncidentEvent).filter(IncidentEvent.incident_id == inc.id).delete()
            db.query(Incident).filter(Incident.id == inc.id).delete()
            db.commit()


def test_what_was_there_before_whitehats_looked_opens_no_incident_and_one_user_is_one_incident(client):
    assert auto_incidents.same_resource("arn:aws:iam::509594260364:user/wh-demo") == "wh-demo"
    assert auto_incidents.same_resource("arn:aws:s3:::receipts") == "receipts"
    assert auto_incidents.same_resource("sg-1") == "sg-1"
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id))
        old = _signal(db, org, project, "User dev has no two-step sign-in", resource="arn:aws:iam::1:user/dev")
        old.baseline = True
        by_trail = _signal(db, org, project, "Full administrator access was granted", severity="critical",
                           resource="wh-demo")
        by_prowler = _signal(db, org, project, "IAM User wh-demo has AdministratorAccess policy attached",
                             severity="critical", resource="arn:aws:iam::1:user/wh-demo")
        db.commit()
        made = [old, by_trail, by_prowler]
        try:
            assert auto_incidents.open_new(db, [s.id for s in made]) == 1
            for s in made:
                db.refresh(s)
            assert old.incident_id is None  # backlog: listed, never an incident by itself
            assert by_trail.incident_id == by_prowler.incident_id is not None
        finally:
            ids = {s.incident_id for s in made if s.incident_id}
            for s in made:
                db.delete(s)
            db.flush()
            for iid in ids:
                db.query(IncidentEvent).filter(IncidentEvent.incident_id == iid).delete()
                db.query(Incident).filter(Incident.id == iid).delete()
            db.commit()

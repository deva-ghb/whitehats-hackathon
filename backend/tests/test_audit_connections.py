"""AUD.6: a full audit remembers the connections it read; the same ones again start none, something new starts one,
and a weekly audit comes 7 days after the last, unless a project admin switched it off."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.connectors import routing
from app.db import Base, SessionLocal, utcnow
from app.models import Audit, AuditLog, Clone, CloneVersion, Connector, Node, Organization, Project, ProjectMember, User
from app.services import audit_jobs

ROLE = "arn:aws:iam::509594260364:role/whitehats-readonly"
SLUG = "audit-connections"


def _map(db, project, repos):
    clone = db.scalar(select(Clone).where(Clone.project_id == project.id))
    if clone is None:
        clone = Clone(org_id=project.org_id, project_id=project.id, slug="audit-map", environment="prod",
                      library_version="1")
        db.add(clone)
        db.flush()
    number = 1 + max([v.number for v in db.scalars(select(CloneVersion).where(CloneVersion.clone_id == clone.id))],
                     default=0)
    version = CloneVersion(org_id=project.org_id, project_id=project.id, clone_id=clone.id, number=number,
                           author="t", reason="t", environment="prod", library_version="1")
    db.add(version)
    db.flush()
    for i, repo in enumerate(repos):
        db.add(Node(org_id=project.org_id, project_id=project.id, clone_version_id=version.id, node_key=f"r{i}",
                    type="repo.github@1", name=repo.split("/")[1], environment="prod", properties={"repo": repo}))
    db.commit()


@pytest.fixture
def project():
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        made = Project(org_id=org.id, name="Audit connections", slug=SLUG)
        db.add(made)
        db.flush()
        ceo = db.scalar(select(User).where(User.email == "ceo@acme.example"))
        db.add(ProjectMember(project_id=made.id, user_id=ceo.id, role="project_admin"))
        aws = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "aws"))
        before = (aws.status, dict(aws.config or {}))
        aws.status, aws.config = "reading", {**(aws.config or {}), "role_arn": ROLE}
        routing.attach(db, aws, made, {}, "test")
        db.commit()
        pid = made.id
    yield pid
    with SessionLocal() as db:
        aws = db.scalar(select(Connector).where(Connector.key == "aws", Connector.org_id == org.id))
        aws.status, aws.config = before
        for table in reversed(Base.metadata.sorted_tables):
            if "project_id" in table.c and table.name != "projects":
                db.execute(table.delete().where(table.c.project_id == pid))
        db.delete(db.get(Project, pid))
        db.commit()


def _finish(db, row, days_ago=0, status="complete"):
    row.status, row.finished_at = status, utcnow() - timedelta(days=days_ago)
    db.commit()


def test_the_same_connections_skip_and_something_new_audits(client, project):
    with SessionLocal() as db:
        p = db.get(Project, project)
        assert audit_jobs.after_connections(db, p, connected=True) is None  # no code connected yet: nothing to audit
        _map(db, p, ["deva-ghb/shop", "deva-ghb/worker"])
        first = audit_jobs.after_connections(db, p, connected=True)
        assert first.trigger == "onboarding" and first.status == "queued"
        assert set(first.connections) == {f"aws:{ROLE}", "github:deva-ghb/shop", "github:deva-ghb/worker"}
        assert first.connections[f"aws:{ROLE}"] == "AWS account 509594260364 (role whitehats-readonly)"
        assert audit_jobs.after_connections(db, p, connected=True) is None  # one is waiting: not another
        _finish(db, first, days_ago=2)

        # disconnected and connected again: the same AWS role and repositories
        skipped = audit_jobs.after_connections(db, p, connected=True)
        assert skipped.status == "skipped" and skipped.trigger == "connections"
        assert skipped.trigger_detail.startswith("An audit with these same connections finished 2 days ago")
        assert "The next weekly audit is on" in skipped.trigger_detail
        assert audit_jobs.after_connections(db, p, connected=False) is None  # the hourly read says nothing

        _map(db, p, ["deva-ghb/shop"])  # fewer repositories: all were audited
        assert audit_jobs.after_connections(db, p, connected=True).status == "skipped"

        _map(db, p, ["deva-ghb/shop", "deva-ghb/worker", "deva-ghb/mobile"])  # a repository it never read
        again = audit_jobs.after_connections(db, p, connected=False)
        assert again.trigger == "connections" and again.status == "queued"
        assert again.trigger_detail == "Not read by the last full audit: GitHub repository deva-ghb/mobile"


def test_a_failed_audit_does_not_count_and_an_audit_from_before_counts_for_what_is_there(client, project):
    with SessionLocal() as db:
        p = db.get(Project, project)
        _map(db, p, ["deva-ghb/shop"])
        old = Audit(org_id=p.org_id, project_id=p.id, trigger="onboarding", status="complete",
                    finished_at=utcnow() - timedelta(days=1))  # from before audits remembered their connections
        db.add(old)
        db.commit()
        assert audit_jobs.after_connections(db, p, connected=True).status == "skipped"
        db.refresh(old)
        assert set(old.connections) == {f"aws:{ROLE}", "github:deva-ghb/shop"}
        _map(db, p, ["deva-ghb/shop", "deva-ghb/new"])
        failed = audit_jobs.after_connections(db, p, connected=True)
        _finish(db, failed, status="failed")
        retried = audit_jobs.after_connections(db, p, connected=True)  # a failed audit read nothing that counts
        assert retried.status == "queued" and retried.id != failed.id


def test_the_weekly_audit_comes_seven_days_after_the_last_and_a_project_admin_can_switch_it_off(client, ceo, project):
    with SessionLocal() as db:
        p = db.get(Project, project)
        _map(db, p, ["deva-ghb/shop"])
        last = audit_jobs.after_connections(db, p, connected=True)
        _finish(db, last, days_ago=6)
        assert audit_jobs.weekly_due(db) == 0
        _finish(db, last, days_ago=8)
    off = client.patch(f"/api/projects/{SLUG}/policy?project={SLUG}", headers=ceo, json={"weekly_audits": False})
    assert off.status_code == 200 and off.json()["weekly_audits"] is False
    with SessionLocal() as db:
        assert audit_jobs.weekly_due(db) == 0
        trail = db.scalar(select(AuditLog).where(AuditLog.project_id == project, AuditLog.action == "set weekly audits"))
        assert (trail.target, trail.detail) == ("Audit connections", "off")
        p = db.get(Project, project)
        skipped = audit_jobs.after_connections(db, p, connected=True)
        assert skipped.trigger_detail.endswith("Weekly audits are switched off for this project.")
    client.patch(f"/api/projects/{SLUG}/policy?project={SLUG}", headers=ceo, json={"weekly_audits": True})
    with SessionLocal() as db:
        assert audit_jobs.weekly_due(db) >= 1
        weekly = db.scalar(select(Audit).where(Audit.project_id == project, Audit.trigger == "schedule"))
        assert weekly.status == "queued" and set(weekly.connections) == {f"aws:{ROLE}", "github:deva-ghb/shop"}

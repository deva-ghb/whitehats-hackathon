"""Posture is unseen until something real reports, and history is one real day at a time.

The demo company is only read. Anything this test changes belongs to a company it creates.
"""

import logging

from sqlalchemy import delete, func, select

from app.db import SessionLocal, utcnow
from app.models import Collector, Connector, Organization, PostureSnapshot, Signal
from app.services.posture import compute, history, history_started, record_daily_snapshots, snapshot


def _remove(org_id):
    with SessionLocal() as db:
        for model in (PostureSnapshot, Signal, Collector, Connector):
            db.execute(delete(model).where(model.org_id == org_id))
        org = db.get(Organization, org_id)
        if org is not None:
            db.delete(org)
        db.commit()


def test_sample_signals_do_not_count_as_seen_and_each_day_is_one_snapshot(client, ceo):
    overview = client.get("/api/overview", headers=ceo).json()
    acme_score = overview["posture"]["score"]
    acme_grade = overview["posture"]["grade"]

    org_id = None
    try:
        with SessionLocal() as db:
            org = Organization(name="Posture Co", slug="posture-co", mfa_required=False)
            db.add(org)
            db.flush()
            org_id = org.id
            assert compute(db, org.id)["grade"] == "Unseen"

            db.add(Signal(org_id=org.id, severity="high", title="Sample only", origin="sample"))
            db.commit()
            assert compute(db, org.id)["grade"] == "Unseen"

            assert snapshot(db, org.id)["grade"] == "Unseen"
            assert snapshot(db, org.id)["grade"] == "Unseen"
            days = db.scalar(select(func.count(PostureSnapshot.id)).where(PostureSnapshot.org_id == org.id))
            assert days == 1
            assert history(db, org.id)[0]["day"] == history_started(db, org.id)

            db.add(Connector(org_id=org.id, key="aws", name="AWS", category="cloud", last_sync_at=utcnow()))
            db.commit()
            assert compute(db, org.id)["grade"] != "Unseen"
            db.scalar(select(Connector).where(Connector.org_id == org.id)).last_sync_at = None
            db.commit()
            assert compute(db, org.id)["grade"] == "Unseen"

            db.add(Collector(org_id=org.id, name="edge", kind="log", mode="local", token_hash="test",
                             last_heartbeat=utcnow()))
            db.commit()
            assert compute(db, org.id)["grade"] != "Unseen"

            assert record_daily_snapshots(db) >= 1
            days = db.scalar(select(func.count(PostureSnapshot.id)).where(PostureSnapshot.org_id == org.id))
            assert days == 1
    finally:
        if org_id is not None:
            _remove(org_id)

    again = client.get("/api/overview", headers=ceo).json()
    assert again["posture"]["grade"] == acme_grade
    assert again["posture"]["score"] == acme_score


def test_opening_overview_twice_writes_nothing(client, ceo):
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        db.execute(delete(PostureSnapshot).where(PostureSnapshot.org_id == org.id))
        db.commit()
    assert client.get("/api/overview", headers=ceo).status_code == 200
    assert client.get("/api/overview", headers=ceo).status_code == 200
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        written = db.scalar(select(func.count(PostureSnapshot.id)).where(PostureSnapshot.org_id == org.id))
    assert written == 0


def test_one_failing_company_does_not_block_the_rest(client, monkeypatch, caplog):
    assert client
    ids = []
    try:
        with SessionLocal() as db:
            for slug in ("snap-bad", "snap-ok"):
                org = Organization(name=slug, slug=slug, status="live", mfa_required=False)
                db.add(org)
                db.flush()
                ids.append(org.id)
            db.commit()
        bad, ok = ids

        def explode(db, org_id, day=None):
            if org_id == bad:
                raise RuntimeError("score failed")
            return snapshot(db, org_id, day)

        monkeypatch.setattr("app.services.posture.snapshot", explode)
        with SessionLocal() as db, caplog.at_level(logging.ERROR):
            record_daily_snapshots(db)
        with SessionLocal() as db:
            bad_days = db.scalar(select(func.count(PostureSnapshot.id)).where(PostureSnapshot.org_id == bad))
            ok_days = db.scalar(select(func.count(PostureSnapshot.id)).where(PostureSnapshot.org_id == ok))
        assert bad_days == 0
        assert ok_days == 1
        assert "score failed" in caplog.text
    finally:
        for org_id in ids:
            _remove(org_id)


def test_history_reads_one_project_so_a_second_project_never_repeats_a_day(client):
    from datetime import date

    from app.models import Project
    from app.projects import ensure_main_project

    assert client
    with SessionLocal() as db:
        org = Organization(name="Two Projects Co", slug="two-projects-co")
        db.add(org)
        db.flush()
        main = ensure_main_project(db, org)
        side = Project(org_id=org.id, name="Side", slug="side")
        db.add(side)
        db.flush()
        today = date.today()
        db.add_all([
            PostureSnapshot(org_id=org.id, project_id=main.id, day=today, score=700, breakdown={"score": 700}),
            PostureSnapshot(org_id=org.id, project_id=side.id, day=today, score=300, breakdown={"score": 300}),
        ])
        db.commit()
        org_id = org.id
    try:
        with SessionLocal() as db:
            assert history(db, org_id) == [{"day": today.isoformat(), "score": 700}]
            assert history_started(db, org_id) == today.isoformat()
    finally:
        with SessionLocal() as db:
            db.execute(delete(PostureSnapshot).where(PostureSnapshot.org_id == org_id))
            db.execute(delete(Project).where(Project.org_id == org_id))
            db.commit()
        _remove(org_id)


def test_each_project_gets_its_own_daily_score_and_history(client):
    from datetime import date

    from app.models import Project, Signal
    from app.projects import ensure_main_project

    assert client
    with SessionLocal() as db:
        org = Organization(name="Split Score Co", slug="split-score-co")
        db.add(org)
        db.flush()
        main = ensure_main_project(db, org)
        side = Project(org_id=org.id, name="Side", slug="side")
        db.add(side)
        db.flush()
        db.add(Signal(org_id=org.id, project_id=side.id, severity="critical", title="Only Side has this"))
        db.commit()
        org_id, main_id, side_id = org.id, main.id, side.id
    try:
        with SessionLocal() as db:
            snapshot(db, org_id)
            today = date.today().isoformat()
            main_day = history(db, org_id, project_id=main_id)
            side_day = history(db, org_id, project_id=side_id)
        assert [row["day"] for row in main_day] == [today] and [row["day"] for row in side_day] == [today]
        assert side_day[0]["score"] < main_day[0]["score"]  # the critical signal lowers Side only
    finally:
        with SessionLocal() as db:
            db.execute(delete(Signal).where(Signal.org_id == org_id))
            db.execute(delete(PostureSnapshot).where(PostureSnapshot.org_id == org_id))
            db.execute(delete(Project).where(Project.org_id == org_id))
            db.commit()
        _remove(org_id)


def test_a_finding_shared_by_two_projects_counts_once_and_a_resolved_one_not_at_all(client):
    from app.models import Incident, Project
    from app.projects import ensure_main_project
    from app.services.posture import findings_once, roll_up

    assert client
    shared = "arn:aws:s3:::shared-receipts"
    with SessionLocal() as db:
        org = Organization(name="Shared Find Co", slug="shared-find-co")
        db.add(org)
        db.flush()
        main = ensure_main_project(db, org)
        side = Project(org_id=org.id, name="Side", slug="side")
        db.add(side)
        db.flush()
        done = Incident(org_id=org.id, project_id=main.id, ref="INC-1", title="Closed out", severity="high",
                        status="resolved", summary="", business_impact="")
        db.add(done)
        db.flush()
        db.add_all([
            Signal(org_id=org.id, project_id=main.id, severity="high", title="Bucket is public", resource=shared),
            Signal(org_id=org.id, project_id=side.id, severity="high", title="Bucket is public", resource=shared),
            Signal(org_id=org.id, project_id=main.id, severity="high", title="Old and resolved", incident_id=done.id),
        ])
        db.commit()
        org_id, ids = org.id, [main.id, side.id]
    try:
        with SessionLocal() as db:
            counted = findings_once(db, ids)
            projects = db.scalars(select(Project).where(Project.id.in_(ids)).order_by(Project.id)).all()
            rolled = roll_up(db, org_id, projects)
        assert counted == {"account": 1, "projects": {"main": 1, "side": 1}}
        scores = [row["score"] for row in rolled["projects"]]
        # Both projects carry the same finding, so the weighted account score takes it once, not twice.
        assert scores[0] == scores[1] == rolled["account"]["score"]
    finally:
        with SessionLocal() as db:
            db.execute(delete(Signal).where(Signal.org_id == org_id))
            db.execute(delete(Incident).where(Incident.org_id == org_id))
            db.execute(delete(Project).where(Project.org_id == org_id))
            db.commit()
        _remove(org_id)


def test_closing_a_finding_raises_the_score_at_once_and_an_old_open_one_still_counts(client):
    """POS.1: the score follows fixes. A merged fix closes its finding and the score rises the same minute;
    a critical finding nobody fixed keeps counting after a week; a "not affected" one does not count."""
    from datetime import timedelta

    from app.projects import ensure_main_project

    assert client
    with SessionLocal() as db:
        org = Organization(name="Fix Follow Co", slug="fix-follow-co")
        db.add(org)
        db.flush()
        project = ensure_main_project(db, org)
        clean = compute(db, org.id, project_id=project.id)["score"]
        fresh = Signal(org_id=org.id, project_id=project.id, severity="high", title="Caller value used as an object key")
        old = Signal(org_id=org.id, project_id=project.id, severity="critical", title="Nobody fixed this",
                     created_at=utcnow() - timedelta(days=8))
        waived = Signal(org_id=org.id, project_id=project.id, severity="high", title="Covered by a VEX statement",
                        vex_status="not_affected")
        db.add_all([fresh, old, waived])
        db.commit()
        org_id, project_id = org.id, project.id
    try:
        with SessionLocal() as db:
            found = compute(db, org_id, project_id=project_id)["score"]
            assert found == clean - 10 - 25  # the high and the week-old critical; the VEX one is not counted
            row = db.scalar(select(Signal).where(Signal.org_id == org_id, Signal.severity == "high"))
            row.closed_at, row.closed_reason = utcnow(), "fixed"
            db.commit()
            assert compute(db, org_id, project_id=project_id)["score"] == found + 10
    finally:
        with SessionLocal() as db:
            db.execute(delete(Signal).where(Signal.org_id == org_id))
            db.commit()
        _remove(org_id)

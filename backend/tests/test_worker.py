"""The daily loop, not a direct function call, records one posture snapshot per company."""

from datetime import date

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import run_worker_cycle
from app.models import Organization, PostureSnapshot


def _rows():
    with SessionLocal() as db:
        return {(row.org_id, row.day) for row in db.scalars(select(PostureSnapshot))}


def test_the_worker_loop_records_one_snapshot_per_company_when_the_day_changes(client):
    assert client
    ids = []
    before = _rows()
    try:
        with SessionLocal() as db:
            for slug in ("cycle-a", "cycle-b"):
                org = Organization(name=slug, slug=slug, status="live", mfa_required=False)
                db.add(org)
                db.flush()
                ids.append(org.id)
            db.commit()
        day_one = date(2026, 10, 22)
        day_two = date(2026, 10, 23)
        with SessionLocal() as db:
            _, last_day = run_worker_cycle(
                db, now=1_000_000, today=day_one, last_retention=1_000_000, last_snapshot_day=None)
        assert last_day == day_one
        with SessionLocal() as db:
            for org_id in ids:
                days = db.scalars(select(PostureSnapshot.day).where(PostureSnapshot.org_id == org_id)).all()
                assert days == [day_one], org_id
        with SessionLocal() as db:
            _, last_day = run_worker_cycle(
                db, now=1_000_000, today=day_two, last_retention=1_000_000, last_snapshot_day=last_day)
        assert last_day == day_two
        with SessionLocal() as db:
            _, again = run_worker_cycle(
                db, now=1_000_100, today=day_two, last_retention=1_000_000, last_snapshot_day=last_day)
        assert again == day_two
        with SessionLocal() as db:
            for org_id in ids:
                days = db.scalars(
                    select(PostureSnapshot.day).where(PostureSnapshot.org_id == org_id).order_by(PostureSnapshot.day)
                ).all()
                assert days == [day_one, day_two], org_id
    finally:
        extra = _rows() - before
        with SessionLocal() as db:
            for org_id, day in extra:
                db.execute(delete(PostureSnapshot).where(
                    PostureSnapshot.org_id == org_id, PostureSnapshot.day == day))
            for org_id in ids:
                org = db.get(Organization, org_id)
                if org is not None:
                    db.delete(org)
            db.commit()

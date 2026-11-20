"""Incident references: INC- and a number, unique within a company."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

PREFIX = "INC-"
FIRST_NUMBER = 2201  # a company's first incident; the demo's sample incidents sit just below


def next_ref(taken: Iterable[str]) -> str:
    """The number after the highest one taken. References that are not INC-<number> are ignored."""
    numbers = [int(ref.removeprefix(PREFIX)) for ref in taken if ref.removeprefix(PREFIX).isdigit()]
    return f"{PREFIX}{max(numbers, default=FIRST_NUMBER - 1) + 1}"


# Two incidents opened at the same moment can pick the same number; the later one takes the next.
REF_ATTEMPTS = 3


class RefTaken(Exception):
    """Every number tried was taken by incidents opened at the same moment."""


def taken_next(db: Session, org_id: int) -> str:
    """The next reference for a company, by the ones it has."""
    from .models import Incident

    return next_ref(db.scalars(select(Incident.ref).where(Incident.org_id == org_id)))


def insert_with_next_ref(db: Session, inc) -> None:
    """Save the incident under the next free reference, retrying when another writer took it first."""
    from .models import Incident

    for _ in range(REF_ATTEMPTS):
        inc.ref = taken_next(db, inc.org_id)
        try:
            with db.begin_nested():
                db.add(inc)
                db.flush()
            return
        except IntegrityError:
            taken = db.scalar(select(Incident.id).where(Incident.org_id == inc.org_id, Incident.ref == inc.ref))
            if taken is None:
                raise  # some other rule failed; a new number would not help
    raise RefTaken

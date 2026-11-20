"""WhiteHat registry and dispatch. To add a WhiteHat, see docs/developers/adding-a-whitehat.md."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Agent, Project
from ..projects import main_project_id
from .base import DATA_SOURCES, Context, WhiteHat
from .builtin import BUILTIN, PLANNED

CATALOG: dict[str, WhiteHat] = {w.key: w for w in [*BUILTIN, *PLANNED]}
BUILTIN_KEYS = tuple(w.key for w in BUILTIN)

__all__ = ["BUILTIN_KEYS", "CATALOG", "DATA_SOURCES", "WhiteHat", "dispatch", "enable"]


def enable(db: Session, org_id: int, key: str, status: str = "active", *, project_id: int | None = None) -> Agent:
    """Switch a WhiteHat on in one project, Main unless another is named."""
    w = CATALOG[key]
    project_id = project_id or main_project_id(db, org_id)
    agent = db.scalar(select(Agent).where(Agent.project_id == project_id, Agent.key == key))
    if agent is None:
        # Coverage stays null until something measures it. Do not stamp a percentage.
        agent = Agent(org_id=org_id, project_id=project_id, key=key, name=w.name, domain=w.domain,
                      status=status, coverage=w.coverage)
        db.add(agent)
        db.flush()
    elif agent.status == "dark" and w.available:
        agent.status = status
    return agent


def dispatch(db: Session, org_id: int, hook: str, *, project_ids: list[int] | None = None, **kwargs) -> int:
    """Run `hook` on every switched-on WhiteHat of this tenant, or of the named projects only.

    Returns the number of signals raised. A connection names the projects its data belongs to.
    """
    raised = 0
    # An archived project is closed: its WhiteHats take no new data until it is restored.
    agents = (select(Agent).join(Project, Project.id == Agent.project_id)
              .where(Agent.org_id == org_id, Agent.status != "dark", Project.archived.is_(False)))
    if project_ids is not None:
        agents = agents.where(Agent.project_id.in_(project_ids))
    for agent in db.scalars(agents):
        w = CATALOG.get(agent.key)
        if w is None or not w.available:
            continue
        ctx = Context(db=db, org_id=org_id, agent=agent)
        getattr(w, hook)(ctx, **kwargs)
        raised += len(ctx.raised)
    return raised

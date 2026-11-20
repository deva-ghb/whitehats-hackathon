"""GL.2: a learning WhiteHat reads its data and keeps what it would have raised, but it alerts no one, its signals are
offered for no incident, and they count in no score, feed or roll-up. Switched back to active, it acts again."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Agent, Organization, Signal
from app.projects import main_project_id
from app.services import posture
from app.whitehats.base import Context

TITLE = "GL.2 trial: burst of failed sign-ins for ops@acme.example"


@pytest.fixture
def identity(monkeypatch):
    alerts = []
    monkeypatch.setattr("app.services.notify.alert", lambda db, org_id, title, text: alerts.append(title))
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        agent = db.scalar(select(Agent).where(Agent.org_id == org.id, Agent.project_id == main_project_id(db, org.id),
                                              Agent.key == "identity"))
        was = agent.status
        yield db, org, agent, alerts
        db.rollback()
        db.execute(delete(Signal).where(Signal.title.startswith("GL.2 trial")))
        db.get(Agent, agent.id).status = was
        db.commit()


def _raise(db, org, agent, suffix=""):
    signal = Context(db=db, org_id=org.id, agent=agent).signal("high", TITLE + suffix, "12 failures in 60 seconds")
    db.commit()
    return signal


def test_a_learning_whitehat_keeps_the_signal_but_alerts_no_one_and_counts_nowhere(identity, client, ceo):
    db, org, agent, alerts = identity
    before = client.get("/api/overview", headers=ceo).json()
    score_before = posture.compute(db, org.id, project_id=agent.project_id)["score"]
    agent.status = "learning"
    signal = _raise(db, org, agent)

    assert signal.learning is True and alerts == []
    overview = client.get("/api/overview", headers=ceo).json()
    assert overview["kpis"]["signals_7d"] == before["kpis"]["signals_7d"]
    assert TITLE not in [s["title"] for s in overview["signals"] + overview["earlier_signals"]]
    assert TITLE not in [s["title"] for s in client.get("/api/signals?unlinked=true", headers=ceo).json()]
    opened = client.post("/api/incidents", headers=ceo, json={"title": "From a trial", "severity": "high", "signal_ids": [signal.id]})
    assert opened.status_code == 404  # not offered, and not accepted when named
    assert posture.compute(db, org.id, project_id=agent.project_id)["score"] == score_before
    card = next(a for a in client.get("/api/agents", headers=ceo).json()["agents"] if a["id"] == agent.id)
    assert card["would_have_7d"] >= 1


def test_switched_back_to_active_it_alerts_and_counts_again(identity, client, ceo):
    db, org, agent, alerts = identity
    agent.status = "active"
    signal = _raise(db, org, agent, " (active)")
    assert signal.learning is False and alerts == [f"HIGH: {TITLE} (active)"]
    assert TITLE + " (active)" in [s["title"] for s in client.get("/api/signals?unlinked=true", headers=ceo).json()]

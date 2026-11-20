"""The demo account's four projects, its sample stories and the people who show access control (spec 0A).

Fintech shop is the account's home project (the proving ground, where Fix Factory and the collectors
work). Health platform and SaaS with AI each hold their own sample story. Whole estate holds every open
finding of the three, raised on the same real resource, so the account counts each one once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .connectors import PROTOTYPE_CONNECTORS, routing
from .models import (
    INCIDENT_CLOSED,
    Agent,
    Connector,
    Incident,
    IncidentEvent,
    Organization,
    Project,
    ProjectMember,
    Remediation,
    Signal,
    User,
)
from .projects import MAIN_NAME, MAIN_SLUG, apply_legacy_access, ensure_main_project
from .security import hash_password
from .whitehats import BUILTIN_KEYS, enable

DEMO_SLUG = "acme"
DEMO_NAME = "Acme (demo)"
HOME_NAME = "Fintech shop"
HEALTH_SLUG, SAAS_SLUG, ESTATE_SLUG = "health-platform", "saas-with-ai", "whole-estate"
# (slug, name, criticality). Health data and payments weigh most in the account score.
OTHER_PROJECTS = (
    (HEALTH_SLUG, "Health platform", "high"),
    (SAAS_SLUG, "SaaS with AI", "medium"),
    (ESTATE_SLUG, "Whole estate", "medium"),
)
HOME_CRITICALITY = "high"

# (email, name, title, account role, {project slug: project role}). MAIN_SLUG is the Fintech shop.
# The job title stays "engineer" because that title grants nothing by itself (projects.apply_legacy_access).
DEMO_PEOPLE = (
    ("health.engineer@acme.example", "Rohan Mehta", "Health platform engineer", None, {HEALTH_SLUG: "engineer"}),
    ("auditor@acme.example", "Sana Qureshi", "Internal auditor", "auditor", {}),
    ("approver@acme.example", "Vikram Shah", "Payments lead", None, {MAIN_SLUG: "approver"}),
)

# The same shape as seed.SAMPLE_INCIDENTS. Signal titles are ones a shipped WhiteHat raises
# (whitehats/signals.py); the last field of a signal is the real resource it is about.
HEALTH_INCIDENTS = (
    {
        "ref": "INC-2216",
        "title": "Public access opened on the patient reports bucket",
        "severity": "high",
        "status": "open",
        "summary": (
            "In the health platform's cloud account (ap-south-1) the public access settings on the patient "
            "reports bucket were changed, and then its bucket policy. Neither change had a ticket."
        ),
        "impact": "Patient reports in the bucket could be read from outside the company until both are put back.",
        "owner": "",
        "hours_ago": 8,
        "events": (
            (8, "signal", "Cloud Posture WhiteHat: Public access settings on a bucket changed", "WhiteHats"),
            (7.8, "signal", "Cloud Posture WhiteHat: A storage bucket policy changed", "WhiteHats"),
        ),
        "signals": (
            (8, "cloud", "medium", "Public access settings on a bucket changed", "ap-south-1", "T1530",
             "arn:aws:s3:::acme-health-patient-reports"),
            (7.8, "cloud", "medium", "A storage bucket policy changed", "ap-south-1", "T1530",
             "arn:aws:s3:::acme-health-patient-reports"),
        ),
    },
)
HEALTH_REMEDIATIONS = (
    {
        "ref": "INC-2216",
        "title": "Block public access to the patient reports bucket again",
        "target": "acme-health-patient-reports",
        "action": "Turn every public access block setting back on and restore the private bucket policy.",
        "why": "Both changes were made without a ticket, and the bucket holds patient reports.",
        "destination": "AWS console",
        "risk": "high",
        "simulated_impact": "1 bucket policy restored · 0 services affected",
        "age": timedelta(minutes=50),
    },
)
SAAS_INCIDENTS = (
    {
        "ref": "INC-2217",
        "title": "Notes query returned another tenant's notes",
        "severity": "high",
        "status": "investigating",
        "summary": (
            "The notes service logged a tenant mismatch: a notes query for one tenant returned notes that "
            "belong to another. The query filters by note id and not by tenant."
        ),
        "impact": "Any tenant who guesses a note id could read another tenant's notes and their AI summaries.",
        "owner": "Priyanka Das",
        "hours_ago": 3,
        "events": (
            (3, "signal", "Endpoint WhiteHat flagged a log shape: error tenant mismatch in notes query", "WhiteHats"),
            (2.5, "note", "Confirmed the notes query filters by note id only, not by tenant.", "Priyanka Das"),
        ),
        "signals": (
            (3, "endpoint", "high", "Log shape needs review: error tenant mismatch in notes query for tenant <*>",
             "notes-api", "", "arn:aws:lambda:ap-south-1:111122223333:function:notes-api"),
        ),
    },
)
SAAS_REMEDIATIONS = (
    {
        "ref": "INC-2217",
        "title": "Filter the notes query by the caller's tenant",
        "target": "notes-api",
        "action": "Add the tenant to the notes query, proven by a test that asks for another tenant's note.",
        "why": "The query filters by note id only, so one tenant can read another tenant's notes.",
        "destination": "GitHub",
        "risk": "high",
        "simulated_impact": "1 code change · tests pass",
        "age": timedelta(minutes=20),
    },
)

_FIX_SKIP = ("ref", "age", "decided_hours")


def add_story(db: Session, org: Organization, project: Project, now: datetime,
              incidents: tuple[dict, ...], fixes: tuple[dict, ...]) -> None:
    """Write one project's sample incidents, their events and signals, and the fixes proposed for them."""
    agents = {agent.key: agent for agent in db.scalars(select(Agent).where(Agent.project_id == project.id))}
    by_ref = {item["ref"]: _sample_incident(db, org, project, agents, now, item) for item in incidents}
    db.add_all(_sample_fixes(org, project, now, fixes, by_ref))
    db.flush()


def _sample_incident(db: Session, org: Organization, project: Project, agents: dict[str, Agent],
                     now: datetime, item: dict) -> Incident:
    resolved = item["status"] == "resolved"
    inc = Incident(org_id=org.id, project_id=project.id, ref=item["ref"], title=item["title"],
                   severity=item["severity"], status=item["status"], summary=item["summary"],
                   business_impact=item["impact"], owner=item["owner"],
                   created_at=now - timedelta(hours=item["hours_ago"]),
                   resolved_at=now - timedelta(hours=item["hours_ago"] - 6) if resolved else None,
                   origin="sample")
    db.add(inc)
    db.flush()
    for hours, kind, text, actor in item["events"]:
        inc.events.append(IncidentEvent(project_id=project.id, at=now - timedelta(hours=hours), kind=kind,
                                        text=text, actor=actor))
    for hours, key, severity, title, entity, mitre, resource in item["signals"]:
        agent = agents.get(key)
        db.add(Signal(org_id=org.id, project_id=project.id, agent_id=agent.id if agent else None,
                      incident_id=inc.id, severity=severity, title=title, entity=entity, resource=resource,
                      mitre=mitre, created_at=now - timedelta(hours=hours),
                      source=f"whitehat:{key}" if agent else key, origin="sample",
                      detail=f"Seen by the {agent.name} WhiteHat." if agent else f"Reported by {key}."))
        if agent:
            agent.last_signal_at = now - timedelta(hours=hours)
    return inc


def _sample_fixes(org: Organization, project: Project, now: datetime, fixes: tuple[dict, ...],
                  by_ref: dict[str, Incident]) -> list[Remediation]:
    rows = []
    for spec in fixes:
        fields = {key: value for key, value in spec.items() if key not in _FIX_SKIP}
        fields.update(org_id=org.id, project_id=project.id, origin="sample", created_at=now - spec["age"])
        if spec.get("ref"):
            fields["incident_id"] = by_ref[spec["ref"]].id
        if "decided_hours" in spec:
            fields["decided_at"] = now - timedelta(hours=spec["decided_hours"])
        rows.append(Remediation(**fields))
    return rows


def is_reshaped(db: Session, org: Organization) -> bool:
    return db.scalar(select(Project.id).where(Project.org_id == org.id, Project.slug == ESTATE_SLUG)) is not None


def shape_demo(db: Session, org: Organization, now: datetime) -> None:
    """Turn the demo account's single project into the four of spec 0A. Run once, on a fresh or older demo.

    The home project already holds the fintech story. Everything else is added here.
    """
    org.name = DEMO_NAME
    home = ensure_main_project(db, org)
    if home.name == MAIN_NAME:
        home.name, home.criticality = HOME_NAME, HOME_CRITICALITY
    projects = {MAIN_SLUG: home}
    for slug, name, criticality in OTHER_PROJECTS:
        projects[slug] = _open_project(db, org, slug, name, criticality)
    db.flush()
    for user in db.scalars(select(User).where(User.org_id == org.id)):
        # CEO and analyst are in every project (spec 0A mapping); the demo engineer stays in the home project.
        apply_legacy_access(db, user)
    _add_people(db, org, projects, now)
    add_story(db, org, projects[HEALTH_SLUG], now, HEALTH_INCIDENTS, HEALTH_REMEDIATIONS)
    add_story(db, org, projects[SAAS_SLUG], now, SAAS_INCIDENTS, SAAS_REMEDIATIONS)
    _attach_everything(db, org, projects[ESTATE_SLUG])
    mirror_open_findings(db, projects[ESTATE_SLUG], [projects[slug] for slug in (MAIN_SLUG, HEALTH_SLUG, SAAS_SLUG)])
    db.flush()


def _open_project(db: Session, org: Organization, slug: str, name: str, criticality: str) -> Project:
    project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == slug))
    if project is None:
        project = Project(org_id=org.id, name=name, slug=slug, criticality=criticality, archived=False,
                          origin="empty", template_key="", environments=["production"], two_approver=False)
        db.add(project)
        db.flush()
    for key in org.contracted_agents or BUILTIN_KEYS:
        enable(db, org.id, key, project_id=project.id)
    return project


def _add_people(db: Session, org: Organization, projects: dict[str, Project], now: datetime) -> None:
    password = hash_password(settings.admin_password)
    for email, name, title, account_role, grants in DEMO_PEOPLE:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            continue
        person = User(org_id=org.id, email=email, name=name, role="engineer", account_role=account_role,
                      title=title, password_hash=password, terms_accepted_at=now - timedelta(days=30))
        db.add(person)
        db.flush()
        for slug, role in grants.items():
            db.add(ProjectMember(project_id=projects[slug].id, user_id=person.id, role=role))


def _attach_everything(db: Session, org: Organization, project: Project) -> None:
    """The connections the prototype reads, with nothing left out of scope, as a whole-estate project would."""
    for connector in db.scalars(select(Connector).where(
            Connector.org_id == org.id, Connector.key.in_(PROTOTYPE_CONNECTORS)).order_by(Connector.id)):
        routing.attach(db, connector, project, {}, "WhiteHats")


def mirror_open_findings(db: Session, estate: Project, products: list[Project]) -> int:
    """Raise each open finding of the product projects in the whole-estate project too.

    That is what a connection attached to both does with a real event (routing.raise_finding): the same
    resource and title in two projects, which the account roll-up counts once. Incidents are not copied:
    each is worked in the product project where its team is. Returns the number of findings raised.
    """
    agents = {agent.key: agent for agent in db.scalars(select(Agent).where(Agent.project_id == estate.id))}
    names = {project.id: project.name for project in products}
    rows = db.scalars(
        select(Signal).outerjoin(Incident, Incident.id == Signal.incident_id)
        .where(Signal.project_id.in_(list(names)), Signal.resource != "", Signal.origin == "sample",
               (Incident.id.is_(None)) | (Incident.status.notin_(INCIDENT_CLOSED)))
        .order_by(Signal.created_at, Signal.id)
    ).all()
    for row in rows:
        key = row.source.removeprefix("whitehat:")
        agent = agents.get(key)
        db.add(Signal(org_id=row.org_id, project_id=estate.id, agent_id=agent.id if agent else None,
                      severity=row.severity, title=row.title, entity=row.entity, resource=row.resource,
                      mitre=row.mitre, created_at=row.created_at, source=row.source, origin=row.origin,
                      detail=f"{row.detail} The same finding is in {names[row.project_id]}, where it is worked."))
        if agent and (agent.last_signal_at is None or agent.last_signal_at < row.created_at):
            agent.last_signal_at = row.created_at
    return len(rows)

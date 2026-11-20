"""One account with a person for each role of spec 0A and two projects, shared by the permission tests.

Main is where the project roles are granted. Side is where nobody below the account roles is a member.
Each project holds one row of everything WhiteHats keeps per project, so a test can tell the two apart.
The account also holds raw collector data, which belongs to no project yet.
"""

from dataclasses import dataclass

import pytest
from conftest import PASSWORD, login
from sqlalchemy import select

from app.db import SessionLocal, utcnow
from app.models import (
    Agent,
    ChatMessage,
    Collector,
    Connector,
    Incident,
    LogDigest,
    LogEvent,
    LogTemplate,
    NetworkFinding,
    NetworkFlow,
    Organization,
    Project,
    ProjectMember,
    Remediation,
    SensePoint,
    Signal,
    User,
)
from app.projects import ensure_main_project
from app.security import hash_password
from app.services.clones import create_clone
from app.services.tenancy import delete_tenant_data

ACCOUNT_ROLES = ("account_owner", "account_admin", "security_lead", "auditor")
PROJECT_ROLES = ("project_admin", "approver", "engineer", "viewer")
ROLES = ACCOUNT_ROLES + PROJECT_ROLES
SLUG = "matrixco"
SIDE_RUN = "matrix-side-run-1"
RAW_LINE = "matrix raw line for account readers only"


@dataclass
class MatrixAccount:
    org_id: int
    headers: dict[str, dict]
    ids: dict[str, int | str]


@pytest.fixture(scope="module", name="matrix")
def matrix_fixture(client):
    """Import matrix_fixture into a test module, then ask for `matrix`."""
    with SessionLocal() as db:
        remove_matrix(db)
        org = Organization(name="Matrix Co", slug=SLUG, mfa_required=False)
        db.add(org)
        db.flush()
        main = ensure_main_project(db, org)
        side = Project(org_id=org.id, name="Side", slug="side")
        db.add(side)
        db.flush()
        _people(db, org, main)
        ids = {
            "clone_main": create_clone(db, org.id, doc("main-estate"), "test", "setup", project_id=main.id).id,
            "clone_side": create_clone(db, org.id, doc("side-estate"), "test", "setup", project_id=side.id).id,
            "finding_main": _finding(db, org.id, main.id),
            "sense_point_main": _sense_point(db, org.id, main.id, "main-estate"),
            "finding_side": _finding(db, org.id, side.id),
            "incident_main": _incident(db, org.id, main.id, "INC-1"),
            "incident_side": _incident(db, org.id, side.id, "INC-2"),
            "signal_main": _signal(db, org.id, main.id),
            "signal_side": _signal(db, org.id, side.id),
            "fix_decided": _fix(db, org.id, main.id, "rejected"),
            "fix_side": _fix(db, org.id, side.id, "pending"),
            "fix_side_factory": _factory_fix(db, org.id, side.id),
            "agent_side": _agent(db, org.id, side.id),
            "connector": _connector(db, org.id),
            "project_slug": "main",
            **_raw_data(db, org.id),
        }
        ids["chat_side"] = _chat(db, org.id, side.id, "engineer@matrixco.test")
        db.commit()
        org_id = org.id
    headers = {role: login(client, f"{role}@matrixco.test") for role in ROLES}
    yield MatrixAccount(org_id, headers, ids)
    with SessionLocal() as db:
        remove_matrix(db)


def doc(slug: str) -> dict:
    return {"schema_version": 1, "clone": slug, "environment": "dev", "library_version": "2026.10",
            "version": {"number": 1, "author": "test", "reason": "setup"},
            "groups": [], "access_paths": [], "credential_refs": [], "nodes": [], "edges": []}


def _people(db, org: Organization, main: Project) -> None:
    for role in ROLES:
        person = User(org_id=org.id, email=f"{role}@matrixco.test", name=role.replace("_", " ").title(),
                      role="ceo" if role == "account_owner" else "analyst", password_hash=hash_password(PASSWORD),
                      terms_accepted_at=org.created_at, account_role=role if role in ACCOUNT_ROLES else None)
        db.add(person)
        db.flush()
        if role in PROJECT_ROLES:
            db.add(ProjectMember(project_id=main.id, user_id=person.id, role=role))


def _finding(db, org_id: int, project_id: int) -> int:
    row = NetworkFinding(org_id=org_id, project_id=project_id, key=f"matrix-{project_id}", kind="exposure",
                         severity="high", title="Port 22 open to any network", detail="Matrix test.")
    db.add(row)
    db.flush()
    return row.id


def _sense_point(db, org_id: int, project_id: int, slug: str) -> int:
    from app.models import Clone

    version = db.query(Clone).filter_by(org_id=org_id, project_id=project_id, slug=slug).one().versions[0]
    row = SensePoint(org_id=org_id, project_id=project_id, clone_version_id=version.id, node_key="n_matrix", mode="data",
                     reason="Matrix test.")
    db.add(row)
    db.flush()
    return row.id


def _incident(db, org_id: int, project_id: int, ref: str) -> int:
    row = Incident(org_id=org_id, project_id=project_id, ref=ref, title=f"Matrix incident {ref}", severity="high",
                   status="open", summary="Matrix test.", business_impact="None.")
    db.add(row)
    db.flush()
    return row.id


def _signal(db, org_id: int, project_id: int) -> int:
    row = Signal(org_id=org_id, project_id=project_id, severity="high", title=f"Matrix signal {project_id}",
                 detail="Matrix test.", entity="host-1", source="whitehat:network")
    db.add(row)
    db.flush()
    return row.id


def _fix(db, org_id: int, project_id: int, status: str) -> int:
    row = Remediation(org_id=org_id, project_id=project_id, title=f"Matrix fix {status}", target="host-1",
                      action="Close port 22.", destination="Slack", status=status)
    db.add(row)
    db.flush()
    return row.id


def _factory_fix(db, org_id: int, project_id: int) -> int:
    """A Side fix for Fix Factory, pending, and a second one that already started SIDE_RUN."""
    db.add(Remediation(org_id=org_id, project_id=project_id, title="Matrix side code fix sent", target="repo",
                       action="Patch the handler.", destination="Fix Factory", factory_intent="matrix-intent",
                       factory_run_id=SIDE_RUN, status="dispatched"))
    row = Remediation(org_id=org_id, project_id=project_id, title="Matrix side code fix", target="repo",
                      action="Patch the handler.", destination="Fix Factory", factory_intent="matrix-intent",
                      status="pending")
    db.add(row)
    db.flush()
    return row.id


def _agent(db, org_id: int, project_id: int) -> int:
    row = Agent(org_id=org_id, project_id=project_id, key="matrix-side-hat", name="Matrix Side WhiteHat",
                domain="Matrix test.", status="dark")
    db.add(row)
    db.flush()
    return row.id


def _connector(db, org_id: int) -> int:
    row = Connector(org_id=org_id, key="cloudwatch", name="AWS CloudWatch Logs", category="Cloud logs")
    db.add(row)
    db.flush()
    return row.id


def _raw_data(db, org_id: int) -> dict[str, int]:
    """A log collector with one line, a network analyzer with one flow, and a digest: the account's raw data."""
    logs = Collector(org_id=org_id, name="Matrix logs", kind="log", mode="docker", token_hash="matrix-log-token")
    net = Collector(org_id=org_id, name="Matrix network", kind="network", mode="docker",
                    token_hash="matrix-net-token")
    db.add_all([logs, net])
    db.flush()
    template = LogTemplate(org_id=org_id, signature="matrix-signature", pattern="matrix raw line <*>", count=1)
    db.add(template)
    db.flush()
    db.add(LogEvent(org_id=org_id, collector_id=logs.id, template_id=template.id, source="matrix.log",
                    message=RAW_LINE))
    db.add(NetworkFlow(org_id=org_id, collector_id=net.id, proto="tcp", local_addr="0.0.0.0", local_port=22,
                       state="LISTEN", process="sshd"))
    now = utcnow()
    db.add(LogDigest(org_id=org_id, window_start=now, window_end=now, lines=1, raw_tokens=10, templates=1,
                     notable=1, digest_tokens=5, budget_tokens=100, text="digest",
                     ai_summary="Matrix digest summary for account readers only."))
    db.flush()
    return {"collector_logs": logs.id, "template": template.id}


def _chat(db, org_id: int, project_id: int, email: str) -> int:
    person = db.scalar(select(User.id).where(User.email == email))
    row = ChatMessage(org_id=org_id, project_id=project_id, user_id=person, role="user",
                      text="Matrix question asked in Side")
    db.add(row)
    db.flush()
    return row.id


def remove_matrix(db) -> None:
    org = db.scalar(select(Organization).where(Organization.slug == SLUG))
    if org is None:
        return
    delete_tenant_data(db, org)
    db.delete(org)
    db.commit()

import os
import socket
import tempfile
import time
import uuid

import pytest


def _closed_port() -> int:
    """A port nothing listens on, so a collector started by a test reaches no real server."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# Isolated data folder per test session, set before the app is imported.
os.environ["WH_DATA_DIR"] = tempfile.mkdtemp(prefix="wh-test-")
os.environ["WH_LOAD_DOTENV"] = "0"
os.environ["WH_PUBLIC_URL"] = f"http://127.0.0.1:{_closed_port()}"
os.environ.pop("AZURE_OPENAI_API_KEY", None)
os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
# A machine's own GitHub App settings never reach the tests: each test that needs an App sets a fake one.
for _name in ("GITHUB_APP_ID", "GITHUB_APP_SLUG", "GITHUB_APP_CLIENT_ID", "GITHUB_APP_CLIENT_SECRET",
              "GITHUB_APP_PRIVATE_KEY_PATH", "GITHUB_APP_INSTALLATION_ID", "GITHUB_API_URL", "GITHUB_WEBHOOK_SECRET"):
    os.environ.pop(_name, None)

# Most tests draw a clone by hand through the API; the tests of the read-only Studio (STUDIO.7) switch it off themselves.
os.environ["WH_STUDIO_HAND_EDITING"] = "1"

from fastapi.testclient import TestClient

from app.main import app
from app.security import totp_at

PASSWORD = "WhiteHats!2026"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def code_for(secret: str) -> str:
    return totp_at(secret, int(time.time() // 30))


def finish(client, res: dict, secret_holder: dict | None = None) -> dict:
    """Walk the sign-in steps (two-step setup or code) and return the final session response."""
    if res["step"] == "mfa_setup":
        setup = client.post("/api/auth/mfa/setup", json={"step_token": res["step_token"]}).json()
        if secret_holder is not None:
            secret_holder["secret"] = setup["secret"]
        res = client.post("/api/auth/mfa/confirm", json={"step_token": res["step_token"],
                                                         "code": code_for(setup["secret"])}).json()
    elif res["step"] == "mfa":
        res = client.post("/api/auth/mfa", json={"step_token": res["step_token"],
                                                 "code": code_for(secret_holder["secret"])}).json()
    assert res["step"] == "done", res
    return res


def login(client, email="ceo@acme.example", password=PASSWORD, secret_holder=None):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {finish(client, r.json(), secret_holder)['token']}"}


@pytest.fixture(scope="session")
def ceo(client):
    return login(client)


@pytest.fixture(scope="session")
def engineer(client):
    return login(client, "engineer@acme.example")


@pytest.fixture(scope="session")
def operator(client):
    holder = {}
    return login(client, "operator@whitehats.example", secret_holder=holder)


# ---------------------------------------------------------------- Postgres for migration tests


@pytest.fixture(scope="session")
def postgres_server():
    """A throwaway Postgres on a private socket, so parallel runs never share a port.

    pgserver ships its own Postgres binaries, so no system install is needed.
    Set WH_TEST_POSTGRES_URL to use a running server instead.
    """
    given = os.environ.get("WH_TEST_POSTGRES_URL")
    if given:
        yield given
        return
    import pgserver

    home = tempfile.mkdtemp(prefix="whpg-")  # short: the socket path must stay under about 100 characters
    server = pgserver.get_server(home, cleanup_mode="delete")
    try:
        yield server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    finally:
        server.cleanup()


@pytest.fixture
def postgres_engine(postgres_server):
    """An empty database of its own for one test."""
    from sqlalchemy import create_engine, text

    name = f"wh_{uuid.uuid4().hex[:12]}"
    admin = create_engine(postgres_server, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(postgres_server.replace("/postgres?", f"/{name}?"))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def add_person(db, user):
    """Add a person the way an accepted invite does: the legacy role plus its account and project roles."""
    from app.models import Organization
    from app.projects import apply_legacy_access, ensure_main_project

    db.add(user)
    db.flush()
    ensure_main_project(db, db.get(Organization, user.org_id))
    apply_legacy_access(db, user)
    return user


@pytest.fixture
def incident():
    """An incident being investigated, of its own, so no other test's fixes block or unblock it."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Incident, Organization, Remediation
    from app.projects import main_project_id

    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        row = Incident(org_id=org.id, project_id=main_project_id(db, org.id), ref="INC-9701",
                       title="Fix Factory outcome test", severity="high", status="investigating")
        db.add(row)
        db.commit()
        iid = row.id
    yield iid
    with SessionLocal() as db:
        for fix in db.scalars(select(Remediation).where(Remediation.incident_id == iid)):
            db.delete(fix)
        db.flush()  # fixes point at the incident without a relationship, so go first explicitly
        db.delete(db.get(Incident, iid))
        db.commit()


@pytest.fixture
def row_id(client, monkeypatch):
    """Acme's GitHub connection, linked to the recorded installation, with no changes kept; put back afterwards.

    Shared by the change tests (VS.1) and the findings tests (VS.4).
    """
    from connector_contract import GITHUB_CONFIG
    from sqlalchemy import delete, select

    from app.db import SessionLocal
    from app.models import ChangeEvent, Connector, Organization, Remediation
    from app.services import github_webhook

    monkeypatch.setattr(github_webhook, "webhook_secret", lambda: "test-webhook-secret")
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        row = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "github"))
        before = (row.status, dict(row.config or {}), dict(row.state or {}), row.last_sync_at)
        row.status, row.config, row.state, row.last_sync_at = "checked", dict(GITHUB_CONFIG), {}, None
        db.execute(delete(ChangeEvent))
        db.commit()
        row_id = row.id
    yield row_id
    with SessionLocal() as db:
        row = db.get(Connector, row_id)
        row.status, row.config, row.state, row.last_sync_at = before
        db.execute(delete(ChangeEvent))
        # A high finding on a change is offered as a fix at once (FF.SRV); those fixes go with the changes.
        db.execute(delete(Remediation).where(Remediation.factory_intent.like("WH-F-%")))
        db.commit()



def remove_project(project_id: int) -> None:
    """Delete a project a test made, and every row that points at it, children first, so later tests that count
    the demo company's projects (or export every table it owns) never see it."""
    from sqlalchemy import delete

    from app.db import Base, SessionLocal
    from app.models import Project

    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            if "project_id" in table.c and table.name != "projects":
                db.execute(delete(table).where(table.c.project_id == project_id))
        db.execute(delete(Project).where(Project.id == project_id))
        db.commit()

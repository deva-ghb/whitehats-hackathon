"""Runtime settings, read once from the environment."""

from __future__ import annotations

import fcntl
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("WH_DATA_DIR", BACKEND_DIR / ".data")).resolve()
# Every temporary folder the app makes goes here, not to the system's /tmp. The scanners run in containers, and Docker
# mounts a folder by its path on the host: a service with a private /tmp (systemd PrivateTmp, as on the server) makes
# folders Docker cannot see, and Docker then mounts an empty folder in their place, so a scan finds nothing and says so.
SCRATCH_DIR = DATA_DIR / "scratch"
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(SCRATCH_DIR)


def load_local_env() -> None:
    """Read the repo .env for local development and the proving ground.

    Production (WH_ENV=production) and tests (WH_LOAD_DOTENV=0) skip this.
    A variable already set in the process is left alone. Values are never logged.
    """
    if os.environ.get("WH_ENV", "development") == "production":
        return
    if os.environ.get("WH_LOAD_DOTENV", "1") != "1":
        return
    path = BACKEND_DIR.parent.parent / ".env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


load_local_env()


def _persistent_secret(name: str) -> str:
    """A random secret generated on first run and kept in the data folder.

    The bytes are written to a temporary file and renamed into place while a lock
    is held, so a crash leaves either the old file or a finished one, and two
    starts at once keep the same key. An empty file counts as missing.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    lock_fd = os.open(DATA_DIR / f".{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        existing = _read_secret(path)
        if existing:
            return existing
        secret = secrets.token_urlsafe(48)
        temporary = path.with_name(f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(secret)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return secret
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _read_secret(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("WH_DATABASE_URL", f"sqlite:///{DATA_DIR / 'whitehats.db'}")
    )
    jwt_secret: str = field(default_factory=lambda: os.environ.get("WH_JWT_SECRET") or _persistent_secret("jwt.key"))
    secret_box_key: str = field(
        default_factory=lambda: os.environ.get("WH_SECRET_KEY") or _persistent_secret("secretbox.key")
    )
    token_hours: int = int(os.environ.get("WH_TOKEN_HOURS", "12"))
    # Public base URL that deployed collectors call back to.
    public_url: str = os.environ.get("WH_PUBLIC_URL", "http://127.0.0.1:8000")
    # The Fix Factory engine (../../factory). Optional: screens degrade gracefully without it.
    factory_url: str = os.environ.get("FACTORY_URL", "http://127.0.0.1:8797")
    factory_token: str = os.environ.get("WHITEHATS_DISPATCH_TOKEN", "")
    # One engine works on one company's repositories, so it belongs to exactly one company (by short name).
    factory_tenant: str = os.environ.get("WH_FACTORY_TENANT", "acme")
    # Seed credentials for the first owner account.
    admin_email: str = os.environ.get("WH_ADMIN_EMAIL", "ceo@acme.example")
    admin_password: str = os.environ.get("WH_ADMIN_PASSWORD", "WhiteHats!2026")
    # The region this installation stores data in. Tenants must match it.
    region: str = os.environ.get("WH_REGION", "in-south")
    # Seed account for the WhiteHats operator console.
    operator_email: str = os.environ.get("WH_OPERATOR_EMAIL", "operator@whitehats.example")
    operator_password: str = os.environ.get("WH_OPERATOR_PASSWORD", os.environ.get("WH_ADMIN_PASSWORD", "WhiteHats!2026"))
    # Optional SMTP for email alerts and invites.
    smtp_host: str = os.environ.get("WH_SMTP_HOST", "")
    smtp_port: int = int(os.environ.get("WH_SMTP_PORT", "587"))
    smtp_user: str = os.environ.get("WH_SMTP_USER", "")
    smtp_password: str = os.environ.get("WH_SMTP_PASSWORD", "")
    smtp_from: str = os.environ.get("WH_SMTP_FROM", "whitehats@localhost")
    # The WhiteHats GitHub App (CN.1): one App for the installation, installed by each customer on their repositories.
    github_app_id: str = os.environ.get("GITHUB_APP_ID", "")
    github_app_slug: str = os.environ.get("GITHUB_APP_SLUG", "")
    # A path to the App's private key file, never the key itself, so the key stays out of the environment and logs.
    github_app_private_key_path: str = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
    github_webhook_secret: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    # The App's own sign-in, used once per install to prove the person installing can manage that installation.
    # GitHub Enterprise Server has its own API address; empty means github.com. The browser tests point it at a fake.
    github_api_url: str = os.environ.get("GITHUB_API_URL", "")
    github_app_client_id: str = os.environ.get("GITHUB_APP_CLIENT_ID", "")
    github_app_client_secret: str = os.environ.get("GITHUB_APP_CLIENT_SECRET", "")
    # KB.1: the ecosystems whose OSV.dev copy refreshes every hour. Production keeps PyPI and npm; elsewhere it is off
    # unless set, so a test or development server never downloads the bulk exports by itself.
    knowledge_ecosystems: str = os.environ.get(
        "WH_KNOWLEDGE_ECOSYSTEMS", "PyPI,npm" if os.environ.get("WH_ENV") == "production" else "")
    # Where OSV's exports are read from; a browser test points it at a fake.
    knowledge_bucket: str = os.environ.get("WH_OSV_BUCKET", "https://osv-vulnerabilities.storage.googleapis.com")
    # STUDIO.7: the map is drawn from the connections only, so drawing by hand is off unless this is "1". The editing code
    # stays, so manual corrections come back by turning this on.
    studio_hand_editing: bool = os.environ.get("WH_STUDIO_HAND_EDITING", "0") == "1"
    log_dir: Path = DATA_DIR / "logs"
    export_dir: Path = DATA_DIR / "exports"


settings = Settings()
settings.log_dir.mkdir(parents=True, exist_ok=True)
settings.export_dir.mkdir(parents=True, exist_ok=True)

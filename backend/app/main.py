"""WhiteHats platform API."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import changes
from .config import settings
from .db import Base, SessionLocal, engine, utcnow
from .knowledge import osv as knowledge_osv
from .knowledge import service as knowledge_service
from .migrate import migrate
from .models import Organization
from .permissions import allow
from .routers import (
    admin,
    audits,
    clones,
    connection_nodes,
    core,
    github,
    imports,
    knowledge,
    library,
    operate,
    platform,
    projects,
    registry,
    sense_points,
    sphere,
    telemetry,
    vault,
    vex,
)
from .scanning import floor as scan_floor
from .security import tenant_box
from .seed import apply_local_azure, ensure_connectors, seed
from .services import (
    audit_jobs,
    auto_incidents,
    auto_map,
    cloud_rules,
    cloud_settings,
    cloudlogs,
    finding_decisions,
    gcp_checks,
    identity_posture,
    image_scans,
    local_workspace,
    tenancy,
    verify,
)
from .services.loganalyzer import reclassify
from .services.posture import record_daily_snapshots
from .sphere import learn as sphere_learn

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"

# A real access log on disk: the local log collector reads it, so the platform watches itself.
access = logging.getLogger("whitehats.access")
access.setLevel(logging.INFO)
_handler = logging.FileHandler(settings.log_dir / "platform.log")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
access.addHandler(_handler)


worker_log = logging.getLogger("whitehats.worker")
_stop = threading.Event()


def run_worker_cycle(db, *, now: float, today: date, last_retention: float, last_snapshot_day: date | None):
    """One pass of the background loop. A new day records one snapshot per company."""
    for key, result in cloudlogs.sync_all(db, utcnow()).items():
        if isinstance(result, str):
            worker_log.warning("cloud sync %s: %s", key, result)
    auto_incidents.sweep(db)  # a serious cloud problem is an incident at once, and resolves when it is put right
    auto_map.run_due(db)  # STUDIO.6: maps whose connection saw a change, or are an hour old, are read again
    audit_jobs.weekly_due(db)  # AUD.6: a full audit 7 days after the last, where a project keeps them on
    audit_jobs.keep_going(db)  # an audit queued with nobody working on it starts within a minute
    if now - last_retention > 3600:
        tenancy.enforce_retention(db)
        last_retention = now
    if last_snapshot_day != today:
        record_daily_snapshots(db, today)
        last_snapshot_day = today
    return last_retention, last_snapshot_day


def background_worker() -> None:
    """Polled sources when due (GitHub every minute, cloud logs every 5), retention hourly, one snapshot a day."""
    last_retention = 0.0
    last_snapshot_day = None
    while not _stop.wait(cloudlogs.WAKE_EVERY_S if last_retention else 5):
        try:
            with SessionLocal() as db:
                last_retention, last_snapshot_day = run_worker_cycle(
                    db, now=time.time(), today=date.today(),
                    last_retention=last_retention, last_snapshot_day=last_snapshot_day)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("background worker cycle failed")


def settings_worker() -> None:
    """CS.1: the live settings of each connected AWS account, checked by Prowler every hour, and a changed service
    again within a minute of the activity history showing the change. Its own thread: a scan takes minutes."""
    while not _stop.wait(30):
        try:
            with SessionLocal() as db:
                cloud_settings.scan_due(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("cloud settings scan failed")
        try:
            with SessionLocal() as db:
                identity_posture.sync_due(db)  # ID.2: the sign-in provider's people, hourly
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("sign-in provider read failed")


def gcp_worker() -> None:
    """Each connected Google Cloud project: settings checks and admin activity every minute. Runs in the local sample
    workspace too, because the demo reads a real project."""
    while not _stop.wait(20):
        try:
            with SessionLocal() as db:
                gcp_checks.sync_due(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("Google Cloud read failed")


def sphere_worker() -> None:
    """KS.1: learn from approver decisions in order (a fix's rule is worded by a model, so off the request), and check
    hourly that what the sphere cites still exists."""
    last_check = 0.0
    while not _stop.wait(10):
        try:
            with SessionLocal() as db:
                if time.time() - last_check > 600:
                    sphere_learn.check_due(db)
                    cloud_rules.evaluate_all(db)  # KS.2: the map changes under the business rules
                    last_check = time.time()
                sphere_learn.process_pending(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("knowledge sphere pass failed")


def verify_worker() -> None:
    """FD.2: what a person says is fixed, WhiteHats checks again; accepted risks past their date ask again."""
    while not _stop.wait(15):
        try:
            with SessionLocal() as db:
                verify.due(db)
                finding_decisions.expire_due(db)
                auto_incidents.sweep(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("checking again failed")


def knowledge_worker() -> None:
    """KB.1: refresh the advisory copy every hour (the first refresh of an ecosystem loads its bulk export)."""
    while not _stop.wait(5 if not knowledge_service.configured() else 30):
        if not knowledge_service.configured():
            continue
        try:
            with SessionLocal() as db:
                knowledge_service.refresh_and_watch(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("knowledge refresh failed")
        if _stop.wait(knowledge_osv.REFRESH_EVERY_S):
            return


def image_worker() -> None:
    """VS.5c: scan the container images the functions run, a new digest within ten minutes and every image daily."""
    while not _stop.wait(image_scans.CHECK_EVERY_S):
        try:
            with SessionLocal() as db:
                image_scans.check(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("image scan pass failed")


def floor_worker() -> None:
    """SCAN.1: the weekly floor, every attached repository's every file and every image on the twin, once a week."""
    while not _stop.wait(scan_floor.CHECK_EVERY_S):
        try:
            with SessionLocal() as db:
                scan_floor.run(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("weekly floor pass failed")


def change_worker() -> None:
    """Judges the changes the poll kept; the webhook starts its own run, so this one picks up what is left.

    A thread of its own, so a slow engine never makes the poll late.
    """
    while not _stop.wait(changes.process.PROCESS_EVERY_S):
        try:
            with SessionLocal() as db:
                changes.process_pending(db)
        except Exception:  # noqa: BLE001 - the worker must survive any single failure
            worker_log.exception("change worker pass failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    finding_decisions.register()  # FD.1: a finding an approver decided on is saved closed, whoever raises it
    Base.metadata.create_all(engine)
    migrate(engine)
    with SessionLocal() as db:
        seed(db)
        if local_workspace.ENABLED:
            local_workspace.initialize(db)
        else:
            apply_local_azure(db)
        reclassify(db)
        for org in db.query(Organization).filter(Organization.secret_key_enc == ""):
            tenant_box(db, org.id)
        for org in db.query(Organization).filter(Organization.is_platform.is_(False),
                                                 Organization.status.notin_(["offboarded", "deleted"])):
            ensure_connectors(db, org)
        db.commit()
        # The first reading is taken before any page is served. Later days belong to the loop.
        record_daily_snapshots(db)
        if not local_workspace.ENABLED:
            audit_jobs.resume_after_restart(db)
            cloud_settings.resume_after_restart(db)
            sphere_learn.resume_after_restart(db)
    threading.Thread(target=gcp_worker, daemon=True, name="whitehats-gcp").start()
    if local_workspace.ENABLED:
        yield
        return
    worker = threading.Thread(target=background_worker, daemon=True, name="whitehats-worker")
    worker.start()
    threading.Thread(target=change_worker, daemon=True, name="whitehats-changes").start()
    threading.Thread(target=knowledge_worker, daemon=True, name="whitehats-knowledge").start()
    threading.Thread(target=image_worker, daemon=True, name="whitehats-images").start()
    threading.Thread(target=floor_worker, daemon=True, name="whitehats-floor").start()
    threading.Thread(target=settings_worker, daemon=True, name="whitehats-settings").start()
    threading.Thread(target=sphere_worker, daemon=True, name="whitehats-sphere").start()
    threading.Thread(target=verify_worker, daemon=True, name="whitehats-verify").start()
    yield
    _stop.set()


app = FastAPI(title="WhiteHats Platform API", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5190", "http://127.0.0.1:5190"],
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001 - last line of defence: log it, answer plainly
        access.exception("request failed method=%s path=%s", request.method, request.url.path)
        return JSONResponse({"detail": "Something went wrong on our side. It has been logged."}, status_code=500)
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/ingest"):
        ms = int((time.monotonic() - start) * 1000)
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        client = request.client.host if request.client else "-"
        msg = "request method=%s path=%s status=%s ms=%s client=%s"
        if response.status_code == 401:
            msg = "authentication failure " + msg
        access.log(level, msg, request.method, request.url.path, response.status_code, ms, client)
    return response


for r in (core.router, operate.router, platform.router, telemetry.router, admin.router, clones.router,
          connection_nodes.router, imports.router, knowledge.router, library.router, projects.router, vault.router, github.router, sense_points.router, vex.router,
          registry.router, audits.router, sphere.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version}


# The guides describe how the platform is built and run, so they are for people who are signed in.
@app.get("/api/help", dependencies=[Depends(allow("be_signed_in"))])
def help_index():
    """Operations and developer docs, served to the in-app Help screen."""
    pages = []
    for path in sorted(DOCS_DIR.rglob("*.md")):
        rel = path.relative_to(DOCS_DIR).with_suffix("").as_posix()
        title = next((ln[2:].strip() for ln in path.read_text().splitlines() if ln.startswith("# ")), rel)
        pages.append({"slug": rel, "title": title, "section": rel.split("/")[0] if "/" in rel else "start"})
    return pages


@app.get("/api/help/{slug:path}", dependencies=[Depends(allow("be_signed_in"))])
def help_page(slug: str):
    path = (DOCS_DIR / f"{slug}.md").resolve()
    if DOCS_DIR not in path.parents or not path.is_file():
        raise HTTPException(404, "No such page.")
    return {"slug": slug, "markdown": path.read_text()}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = FRONTEND_DIST / path
        if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")

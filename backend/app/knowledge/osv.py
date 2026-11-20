"""A local copy of OSV.dev for the ecosystems in scope (KB.1): loaded once from OSV's bulk export, then kept
current from its list of changed records, fetching only what changed.

OSV publishes, per ecosystem, `all.zip` (every record) and `modified_id.csv` (one line per record, `<modified>,<id>`,
newest first). The first refresh of an ecosystem reads the zip; every later one reads the list down to the newest
change already stored and downloads those records only, so a refresh with nothing new downloads no record.

Every record is kept whole. The data is OSV.dev's, licensed CC-BY 4.0; ATTRIBUTION is shown wherever it is used.
"""

from __future__ import annotations

import io
import logging
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Advisory, AdvisoryAlias, AdvisoryPackage, KnowledgeSource
from .versions import normalise

log = logging.getLogger("whitehats.knowledge")

ECOSYSTEMS = ("PyPI", "npm")
SOURCE = "OSV.dev"
LICENCE = "CC-BY-4.0"
ATTRIBUTION = "Vulnerability data from OSV.dev (https://osv.dev), licensed under CC BY 4.0."
REFRESH_EVERY_S = 3600
BATCH = 500  # records written per commit during a bulk load

# (url, into) -> the body; with `into` (an open binary file) the body is written there and nothing is returned, so a
# bulk export is never held in memory. Raises on any failure.
Fetch = Callable[..., bytes | None]


@dataclass
class Refreshed:
    ecosystem: str
    fetched: int = 0  # records downloaded
    stored: int = 0  # records added or changed
    changed: list[str] = field(default_factory=list)  # ids added or changed, for the new-advisory trigger
    error: str = ""


def http_fetch(url: str, into: IO[bytes] | None = None) -> bytes | None:
    import httpx

    with httpx.Client(timeout=600, follow_redirects=True) as client:
        if into is None:
            answer = client.get(url)
            answer.raise_for_status()
            return answer.content
        with client.stream("GET", url) as answer:
            answer.raise_for_status()
            for chunk in answer.iter_bytes(1 << 20):
                into.write(chunk)
        return None


def bucket() -> str:
    from ..config import settings

    return settings.knowledge_bucket.rstrip("/")


def when(text: str) -> datetime:
    """OSV's time text (`2026-10-29T20:30:02.886610907Z`, nanoseconds and all) as a UTC time to the microsecond."""
    text = text.strip().rstrip("Z")
    whole, _, fraction = text.partition(".")
    fraction = (fraction + "000000")[:6]
    return datetime.fromisoformat(f"{whole}.{fraction}").replace(tzinfo=UTC)


def refresh(db: Session, ecosystems: Iterable[str] = ECOSYSTEMS, *, fetch: Fetch = http_fetch) -> list[Refreshed]:
    """Bring each ecosystem's copy up to date. A failed ecosystem keeps what it had and says why."""
    done = []
    for ecosystem in ecosystems:
        source = db.get(KnowledgeSource, ecosystem) or KnowledgeSource(ecosystem=ecosystem)
        db.add(source)
        result = Refreshed(ecosystem)
        try:
            if source.cursor:
                _changes(db, source, result, fetch)
            else:
                _bulk(db, source, result, fetch)
            source.last_error = ""
        except Exception as exc:  # noqa: BLE001 - a network or format failure leaves the copy as it was
            db.rollback()
            source = db.get(KnowledgeSource, ecosystem) or KnowledgeSource(ecosystem=ecosystem)
            db.add(source)
            result.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            source.last_error = result.error
            log.warning("knowledge refresh failed ecosystem=%s %s", ecosystem, result.error)
        source.refreshed_at, source.last_fetched = utcnow(), result.fetched
        source.records = db.query(Advisory).filter(Advisory.ecosystem == ecosystem).count()
        db.commit()
        log.info("knowledge refresh ecosystem=%s fetched=%d stored=%d", ecosystem, result.fetched, result.stored)
        done.append(result)
    return done


def _bulk(db: Session, source: KnowledgeSource, result: Refreshed, fetch: Fetch) -> None:
    newest, newest_at = "", datetime.min.replace(tzinfo=UTC)
    with tempfile.TemporaryFile() as spool:  # a bulk export is large (npm's is about 220 MB): it goes to disk
        fetch(f"{bucket()}/{source.ecosystem}/all.zip", into=spool)
        spool.seek(0)
        with zipfile.ZipFile(spool) as archive:
            for count, record in enumerate(_records(archive), start=1):
                result.fetched += 1
                if store(db, source.ecosystem, record):
                    result.stored += 1
                    result.changed.append(record["id"])
                if record.get("modified") and when(record["modified"]) > newest_at:
                    newest, newest_at = record["modified"], when(record["modified"])
                if count % BATCH == 0:
                    db.commit()
    source.cursor = newest


def _records(archive: zipfile.ZipFile) -> Iterator[dict]:
    import json

    for name in archive.namelist():
        if name.endswith(".json"):
            yield json.loads(archive.read(name))


def _changes(db: Session, source: KnowledgeSource, result: Refreshed, fetch: Fetch) -> None:
    import json

    stored_up_to = when(source.cursor)
    listing = fetch(f"{bucket()}/{source.ecosystem}/modified_id.csv").decode("utf-8")
    newer = []
    for line in listing.splitlines():  # newest first: stop at the first change already stored
        stamp, _, advisory_id = line.partition(",")
        if not advisory_id or when(stamp) <= stored_up_to:
            break
        newer.append((stamp, advisory_id.strip()))
    for stamp, advisory_id in newer:
        record = json.loads(fetch(f"{bucket()}/{source.ecosystem}/{advisory_id}.json"))
        result.fetched += 1
        if store(db, source.ecosystem, record):
            result.stored += 1
            result.changed.append(advisory_id)
    if newer:
        source.cursor = newer[0][0]


def store(db: Session, ecosystem: str, record: dict) -> bool:
    """Keep one record whole, with its package and alias index. False when the same version was already kept."""
    advisory_id = str(record["id"])
    current = db.get(Advisory, advisory_id)
    modified = str(record.get("modified", ""))
    if current is not None and current.modified == modified:
        return False
    if current is None:
        current = Advisory(id=advisory_id)
        db.add(current)
    current.ecosystem, current.modified = ecosystem, modified
    current.aliases = list(record.get("aliases") or [])
    current.summary = str(record.get("summary") or "")[:2000]
    current.published, current.withdrawn = str(record.get("published") or ""), str(record.get("withdrawn") or "")
    current.record, current.fetched_at = record, utcnow()
    db.flush()
    db.execute(delete(AdvisoryPackage).where(AdvisoryPackage.advisory_id == advisory_id))
    db.execute(delete(AdvisoryAlias).where(AdvisoryAlias.advisory_id == advisory_id))
    names = {(str(item["package"].get("ecosystem", "")), normalise(str(item["package"].get("ecosystem", "")),
                                                                    str(item["package"].get("name", ""))))
             for item in record.get("affected") or [] if isinstance(item.get("package"), dict)}
    db.add_all(AdvisoryPackage(advisory_id=advisory_id, ecosystem=eco, name=name) for eco, name in sorted(names) if name)
    db.add_all(AdvisoryAlias(advisory_id=advisory_id, alias=alias) for alias in sorted(set(current.aliases)))
    return True


def stored_ids(db: Session, ecosystem: str) -> set[str]:
    return set(db.scalars(select(Advisory.id).where(Advisory.ecosystem == ecosystem)))


def zip_of(records: list[dict]) -> bytes:
    """A bulk export holding these records, for tests and for a recorded copy."""
    import json

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for record in records:
            archive.writestr(f"{record['id']}.json", json.dumps(record))
    return out.getvalue()


def export_copy(db: Session, path: Path) -> int:
    """Write the whole copy to a SQLite file the Runner reads with no network. Returns how many records it holds."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from ..db import Base

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    engine = create_engine(f"sqlite:///{path}")
    tables = [Base.metadata.tables[name] for name in ("advisories", "advisory_packages", "advisory_aliases", "knowledge_sources")]
    Base.metadata.create_all(engine, tables=tables)
    count = 0
    with sessionmaker(engine)() as copy:
        for row in db.scalars(select(KnowledgeSource)):
            copy.add(KnowledgeSource(ecosystem=row.ecosystem, cursor=row.cursor, records=row.records,
                                     refreshed_at=row.refreshed_at, last_fetched=row.last_fetched))
        for advisory in db.scalars(select(Advisory)).yield_per(BATCH):
            store(copy, advisory.ecosystem, advisory.record)
            count += 1
            if count % BATCH == 0:
                copy.commit()
        copy.commit()
    engine.dispose()
    return count

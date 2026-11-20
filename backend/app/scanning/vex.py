"""SCAN.5: "not affected" is a VEX statement, never a suppression.

A person's triage that a finding does not apply is an OpenVEX statement: what (the weakness), where (the product),
the status, one of OpenVEX's justifications, the reason in their words, who decided and when, and optionally until
when. A supplier's published VEX is read into the same statements, with the supplier named. Statements are applied,
never used to hide anything: the finding stays listed and is marked; the scanners read them as input (Trivy's
`--vex`) and report what they cover as not affected. A withdrawn or expired statement stops applying, and the finding
is open again.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Signal, VexStatement, utcnow

CONTEXT = "https://openvex.dev/ns/v0.2.0"
STATUSES = ("not_affected", "affected", "fixed", "under_investigation")
JUSTIFICATIONS = {
    "component_not_present": "the part is not in this product",
    "vulnerable_code_not_present": "the weak code is not in this version",
    "vulnerable_code_not_in_execute_path": "the weak code is never run",
    "vulnerable_code_cannot_be_controlled_by_adversary": "an attacker cannot reach or control the weak code",
    "inline_mitigations_already_exist": "something else already stops it",
}


class VexError(ValueError):
    """A statement that cannot be made. The message says why, in one line."""


def active(statement: VexStatement, now: datetime) -> bool:
    return statement.withdrawn_at is None and (statement.expires_at is None or statement.expires_at > now)


def subject(signal: Signal, vulnerability: str = "", package: str = "") -> tuple[str, str, str, str]:
    """(vulnerability, product, subcomponent, image) for a finding: what a statement about it names.

    A code finding is a rule in a file of a repository. An image finding is one advisory in one package of one image;
    a finding that groups an image's weaknesses needs the advisory and the package named."""
    parts = signal.fingerprint.split("|")
    if parts[0] == "image":
        image = signal.resource
        if len(parts) >= 3 and not vulnerability:
            vulnerability = parts[2]
        if not vulnerability or not package:
            raise VexError("This finding groups an image's weaknesses: name the advisory and the package it is about.")
        return vulnerability, package, "", image
    if len(parts) >= 2 and signal.resource.startswith("https://"):
        return parts[0], signal.resource, f"{signal.resource}/blob/{signal.branch or 'HEAD'}/{parts[1]}", ""
    raise VexError("WhiteHats cannot say what this finding is about precisely enough for a VEX statement.")


def mark(db: Session, signal: Signal, *, author: str, status: str, justification: str, reason: str,
         expires_at: datetime | None = None, vulnerability: str = "", package: str = "", now=utcnow) -> VexStatement:
    """A person's triage of one finding, kept as a statement and applied at once."""
    if status not in STATUSES:
        raise VexError(f"The status must be one of {', '.join(STATUSES)}.")
    if status == "not_affected" and justification not in JUSTIFICATIONS:
        raise VexError("Say why it does not apply, with one of the five OpenVEX justifications.")
    if not reason.strip():
        raise VexError("Say in your own words why, in one line: the statement is a decision somebody owns.")
    if expires_at is not None and expires_at <= now():
        raise VexError("An end date must be in the future.")
    weakness, product, subcomponent, image = subject(signal, vulnerability, package)
    statement = VexStatement(
        org_id=signal.org_id, project_id=signal.project_id, fingerprint=signal.fingerprint, image=image,
        vulnerability=weakness, product=product, subcomponent=subcomponent, status=status,
        justification=justification if status == "not_affected" else "", impact_statement=reason.strip()[:2000],
        author=author[:120], created_at=now(), expires_at=expires_at, signal_id=signal.id)
    db.add(statement)
    db.flush()
    apply(db, signal.project_id, now)
    return statement


def withdraw(db: Session, statement: VexStatement, *, by: str, now=utcnow) -> None:
    statement.withdrawn_at, statement.withdrawn_by = now(), by[:120]
    db.flush()  # the session does not flush by itself, and `apply` must see the statement withdrawn
    apply(db, statement.project_id, now)


def read_supplier(db: Session, *, org_id: int, project_id: int, supplier: str, document: dict, image: str = "",
                  source_url: str = "", now=utcnow) -> list[VexStatement]:
    """A supplier's OpenVEX document, kept statement by statement with the supplier named. Only what the document says:
    each product it names, the status, the justification and the impact statement it gives."""
    if not supplier.strip():
        raise VexError("Name the supplier the document comes from.")
    if not isinstance(document, dict) or document.get("@context", "").rstrip("/") not in (CONTEXT, "https://openvex.dev/ns"):
        raise VexError("This is not an OpenVEX document.")
    kept = []
    for item in document.get("statements") or []:
        status = str(item.get("status") or "")
        name = str((item.get("vulnerability") or {}).get("name") or item.get("vulnerability") or "")
        if status not in STATUSES or not name:
            raise VexError("Each statement needs a vulnerability and an OpenVEX status.")
        for product in item.get("products") or []:
            kept.append(VexStatement(
                org_id=org_id, project_id=project_id, image=image, vulnerability=name[:120],
                product=str(product.get("@id") or "")[:400], status=status,
                justification=str(item.get("justification") or ""),
                impact_statement=str(item.get("impact_statement") or item.get("status_notes") or "")[:2000],
                author=str(document.get("author") or supplier)[:120], supplier=supplier.strip()[:120],
                source_url=source_url[:500], created_at=now()))
    if not kept:
        raise VexError("The document has no statement with a product.")
    db.add_all(kept)
    db.flush()
    return kept


def in_force(db: Session, project_id: int, now: datetime) -> list[VexStatement]:
    rows = db.scalars(select(VexStatement).where(
        VexStatement.project_id == project_id, VexStatement.withdrawn_at.is_(None),
        or_(VexStatement.expires_at.is_(None), VexStatement.expires_at > now)).order_by(VexStatement.id))
    return list(rows)


def apply(db: Session, project_id: int, now=utcnow) -> int:
    """Mark each open finding with the statement in force for it, and clear the mark where none is. Returns changes."""
    current = {row.fingerprint: row for row in in_force(db, project_id, now()) if row.fingerprint}
    changed = 0
    for signal in db.scalars(select(Signal).where(
            Signal.project_id == project_id, Signal.fingerprint != "", Signal.closed_at.is_(None))):
        statement = current.get(signal.fingerprint)
        if statement is None and signal.vex_statement_id is None:
            continue  # no person's statement covers it, now or before: a supplier's mark from the scan stays
        status = statement.status if statement is not None and statement.status in ("not_affected", "fixed") else ""
        wanted = (status, statement.id if status and statement is not None else None)
        if (signal.vex_status, signal.vex_statement_id) != wanted:
            signal.vex_status, signal.vex_statement_id = wanted
            changed += 1
    db.commit()
    return changed


def for_image(db: Session, project_ids: list[int], image: str, now: datetime) -> dict:
    """The statements in force about this image, ours and its suppliers', as the OpenVEX document Trivy reads."""
    rows = [row for project_id in project_ids for row in in_force(db, project_id, now)
            if row.image in (image, "") and row.product.startswith("pkg:")]
    return _document(rows, f"image {image}", now)


def document(db: Session, project_id: int, name: str, now=utcnow) -> dict:
    """The project's VEX as an OpenVEX document: every statement in force."""
    return _document(in_force(db, project_id, now()), name, now())


def _document(rows: list[VexStatement], name: str, now: datetime) -> dict:
    statements = []
    for row in rows:
        product = {"@id": row.product}
        if row.subcomponent:
            product["subcomponents"] = [{"@id": row.subcomponent}]
        statement = {"@id": f"https://whitehats.dev/vex/statement/{row.id}", "vulnerability": {"name": row.vulnerability},
                     "timestamp": _stamp(row.created_at), "products": [product], "status": row.status,
                     "status_notes": _notes(row)}
        if row.justification:
            statement["justification"] = row.justification
        if row.impact_statement and row.status == "not_affected":
            statement["impact_statement"] = row.impact_statement
        if row.supplier:
            statement["supplier"] = row.supplier
        statements.append(statement)
    return {"@context": CONTEXT, "@id": f"https://whitehats.dev/vex/{name.replace(' ', '-')}/{_stamp(now)}",
            "author": "WhiteHats", "role": "Document creator", "timestamp": _stamp(now), "version": 1,
            "statements": statements}


def _notes(row: VexStatement) -> str:
    who = f"Published by {row.supplier}" if row.supplier else f"Decided by {row.author}"
    until = f", until {_stamp(row.expires_at)}" if row.expires_at else ""
    return f"{who} on {_stamp(row.created_at)}{until}."


def _stamp(when: datetime | None) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else ""


def words(row: VexStatement) -> str:
    """The statement in a sentence for the screen: why, who, and until when."""
    why = JUSTIFICATIONS.get(row.justification, row.status.replace("_", " "))
    who = f"published by {row.supplier}" if row.supplier else f"decided by {row.author}"
    until = f", until {row.expires_at.day} {row.expires_at:%b %Y}" if row.expires_at else ""
    return f"Not affected: {why}. {row.impact_statement} ({who}{until})".strip()

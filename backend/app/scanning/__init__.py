"""SCAN.1: scan plans checked against the scanner catalogue and the project's scope, and the weekly floor."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Clone, ImageScan, ScanPlanRecord
from ..services.clones import read_clone
from .plan import Plan, PlanRefused, Scope, checked, every_file

__all__ = ["Plan", "PlanRefused", "Scope", "checked", "every_file", "request_images", "store", "twin_nodes"]


def twin_nodes(db: Session, project_ids: list[int]) -> list[dict]:
    """Every node of the latest version of every clone in these projects."""
    nodes: list[dict] = []
    for clone in db.scalars(select(Clone).where(Clone.project_id.in_(project_ids)).order_by(Clone.id)):
        nodes += (read_clone(db, clone.org_id, clone.id) or {}).get("nodes", [])
    return nodes


def twin_images(nodes: list[dict]) -> frozenset[str]:
    return frozenset(str(node["properties"]["image"]) for node in nodes
                     if "@sha256:" in str((node.get("properties") or {}).get("image") or ""))


def store(db: Session, *, org_id: int, project_ids: list[int], kind: str, repo: str, data: dict, scope: Scope,
          fallback: str, change_id: int | None = None) -> tuple[ScanPlanRecord, Plan]:
    """Check the plan and keep it. A refused plan is kept with its refusal, and what runs instead is every file in
    scope with its default scanners, so a bad plan never means less scanning."""
    record = ScanPlanRecord(org_id=org_id, project_ids=list(project_ids), kind=kind, repo=repo, change_id=change_id)
    try:
        plan = checked(data, scope)
    except PlanRefused as exc:
        record.refused = str(exc)
        files = sorted(scope.files.get(repo, ()))
        plan = checked({"trigger": data.get("trigger", "") if isinstance(data, dict) else "", "made_by": fallback,
                        "targets": every_file(repo, files, "the plan was refused, so every file is scanned")}, scope)
    record.plan = plan.to_dict()
    db.add(record)
    db.commit()
    return record, plan


def request_images(db: Session, images: list[str], now) -> None:
    """Ask the image worker to scan these images on its next pass, whatever their last scan."""
    for image in images:
        row = db.get(ImageScan, image) or ImageScan(image=image)
        row.requested_at = now()
        db.add(row)
    db.commit()

"""The account's projects: the home list, and settings for the one the URL names."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project
from ..permissions import allow
from ..security import Principal
from ..services import project_desk

router = APIRouter(prefix="/api")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["empty", "template", "connection"] = "empty"
    criticality: Literal["high", "medium", "low"] = "medium"
    template: str | None = None
    connector_id: int | None = None
    scope: dict | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    criticality: Literal["high", "medium", "low"] | None = None
    environments: list[str] | None = None
    archived: bool | None = None


class PolicyIn(BaseModel):
    two_approver: bool | None = None
    weekly_audits: bool | None = None  # AUD.6


class ConfirmIn(BaseModel):
    confirm: Literal[True]


class MemberIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    role: Literal["project_admin", "approver", "engineer", "viewer"]
    reason: str = Field(max_length=300)  # every grant says why (spec 0A); project_desk checks it says something


class RemoveMemberIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    reason: str = Field(max_length=300)


def _call(action):
    try:
        return action()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


def _named(user: Principal, slug: str) -> Project:
    """The project this path names. A project permission has already checked the same slug via ?project=."""
    if user.project is None or user.project.slug != slug:
        raise HTTPException(404, "Project not found.")
    return user.project


def _in_account(db: Session, user: Principal, slug: str) -> Project:
    row = db.scalar(select(Project).where(Project.org_id == user.org_id, Project.slug == slug))
    if row is None:
        raise HTTPException(404, "Project not found.")
    return row


@router.get("/projects")
def projects(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    return project_desk.list_visible(db, user)


@router.get("/projects/archived")
def archived_projects(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    return project_desk.list_archived(db, user)


@router.get("/account/overview")
def account_overview(user: Principal = Depends(allow("use_account")), db: Session = Depends(get_db)):
    return project_desk.account_overview(db, user)


@router.post("/projects")
def create_project(body: ProjectIn, user: Principal = Depends(allow("manage_projects")), db: Session = Depends(get_db)):
    return _call(lambda: project_desk.create_project(
        db, user, name=body.name, kind=body.kind, criticality=body.criticality,
        template=body.template, connector_id=body.connector_id, scope=body.scope,
    ))


@router.get("/projects/{slug}/settings")
def project_settings(slug: str, user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    return project_desk.describe(db, user, _named(user, slug))


@router.patch("/projects/{slug}")
def update_project(slug: str, body: ProjectPatch, user: Principal = Depends(allow("manage_projects")),
                   db: Session = Depends(get_db)):
    project = _in_account(db, user, slug)
    return _call(lambda: project_desk.update_project(db, user, project, body.model_dump(exclude_unset=True)))


@router.patch("/projects/{slug}/policy")
def project_policy(slug: str, body: PolicyIn, user: Principal = Depends(allow("set_policies")),
                   db: Session = Depends(get_db)):
    project = _named(user, slug)

    def change() -> dict:
        out = None
        if body.two_approver is not None:
            out = project_desk.set_two_approver(db, user, project, body.two_approver)
        if body.weekly_audits is not None:
            out = project_desk.set_weekly_audits(db, user, project, body.weekly_audits)
        if out is None:
            raise ValueError("Say which policy to change.")
        return out
    return _call(change)


@router.post("/projects/{slug}/archive")
def archive_project(slug: str, body: ConfirmIn, user: Principal = Depends(allow("archive_project")),
                    db: Session = Depends(get_db)):
    if not body.confirm:
        return {}
    try:
        return project_desk.archive_project(db, user, _named(user, slug))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/projects/{slug}/members")
def project_member(slug: str, body: MemberIn, user: Principal = Depends(allow("manage_members")),
                   db: Session = Depends(get_db)):
    return _call(lambda: project_desk.add_member(db, user, _named(user, slug), body.email, body.role, body.reason))


@router.delete("/projects/{slug}/members")
def remove_project_member(slug: str, body: RemoveMemberIn, user: Principal = Depends(allow("manage_members")),
                          db: Session = Depends(get_db)):
    return _call(lambda: project_desk.remove_member(db, user, _named(user, slug), body.email, body.reason))

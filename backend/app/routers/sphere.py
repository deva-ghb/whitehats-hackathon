"""The knowledge sphere's screen (KS.1): read-only, built by WhiteHats. The only actions are an approver confirming or
rejecting what WhiteHats proposed, and asking it to propose from the latest audit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Audit, SphereDecision
from ..permissions import allow
from ..security import Principal, audit
from ..services import cloud_rules, local_workspace
from ..services.clones import library
from ..services.coverage import map_nodes
from ..sphere import graph, learn, store

router = APIRouter(prefix="/api")
PAGE = 250  # the most items one page of the sphere carries, so a large business still draws smoothly
WHOLE = ("onboarding", "schedule", "request")  # audits of the whole repository


def _current(db: Session, project_id: int) -> tuple[dict, str]:
    """The newest version, from the graph database when it is there (else replayed from the versions)."""
    if store.sync_graph(db, project_id):
        try:
            with graph.Graph(project_id) as g:
                return {"nodes": sorted(g.nodes(), key=lambda n: (n["kind"], n["key"])),
                        "edges": sorted(g.edges(), key=store.edge_id)}, "graph"
        except graph.Unavailable:
            pass
    return store.state(db, project_id).as_dict(), "versions"


def _in_order(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Each concept followed by the rules and exceptions about it, then anything about no concept, so a page boundary
    never splits a concept from what sits around it on the globe (unless one concept alone holds more than a page)."""
    home: dict[str, str] = {}
    for e in edges:
        if e["kind"] == "ABOUT":
            home.setdefault(e["source"], e["target"])
    for e in edges:
        if e["kind"] == "EXEMPTS" and e["source"] not in home and e["target"] in home:
            home[e["source"]] = home[e["target"]]
    members: dict[str, list[dict]] = {n["key"]: [] for n in nodes if n["kind"] == "Concept"}
    loose = []
    for n in nodes:
        if n["kind"] == "Concept":
            continue
        (members[home[n["key"]]] if home.get(n["key"]) in members else loose).append(n)
    out = []
    for n in nodes:
        if n["kind"] == "Concept":
            out += [n, *members[n["key"]]]
    return out + loose


def _page(out: dict, offset: int, limit: int) -> None:
    """Cut what is shown (and, when comparing, what was forgotten) to one page; the links kept are those within it."""
    everything = [(n, False) for n in out["nodes"]] + [(n, True) for n in out.get("forgotten", [])]
    edges = out["edges"] + [e for e in out.get("forgotten_edges", [])]
    ordered = _in_order([n for n, _ in everything], edges)
    gone = {n["key"] for n, f in everything if f}
    page = ordered[offset:offset + limit]
    keys = {n["key"] for n in page}
    # Everything proposed is listed for the approver whatever the page, with the concepts it is about.
    proposed = [n for n in out["nodes"] if n.get("status") == "proposed"]
    waiting = {n["key"] for n in proposed}
    out["proposed"] = proposed
    out["proposed_edges"] = [e for e in out["edges"] if e["kind"] == "ABOUT" and e["source"] in waiting]
    about = {e["target"] for e in out["proposed_edges"]}
    out["proposed_concepts"] = [n for n in out["nodes"] if n["key"] in about]
    out["nodes"] = [n for n in page if n["key"] not in gone]
    if "forgotten" in out:
        out["forgotten"] = [n for n in page if n["key"] in gone]
    out["edges"] = [e for e in out["edges"] if e["source"] in keys and e["target"] in keys]
    out.pop("forgotten_edges", None)
    out["page"] = {"offset": offset, "limit": limit, "total": len(ordered)}


@router.get("/sphere")
def read(version: int | None = None, compare: int | None = None, offset: int = Query(0, ge=0),
         limit: int = Query(PAGE, ge=1, le=PAGE), user: Principal = Depends(allow("see_project")),
         db: Session = Depends(get_db)):
    assert user.project is not None
    pid = user.project.id
    latest = store.latest_number(db, pid)
    number = latest if version is None else max(0, min(version, latest))
    if number == latest:
        shown, source = _current(db, pid)
    else:
        shown, source = store.state(db, pid, number).as_dict(), "versions"
    out = {"latest": latest, "version": number, "source": source, "graph_configured": graph.configured(), **shown}
    if compare is not None:
        before = store.state(db, pid, max(0, min(compare, latest)))
        after = store.state(db, pid, number)
        out["compare"] = compare
        out["diff"] = store.diff(before, after)
        # what was forgotten is drawn too, struck through
        out["forgotten"] = [before.nodes[k] for k in out["diff"]["forgot"]]
        out["forgotten_edges"] = list(before.as_dict()["edges"])  # so a forgotten rule pages with its concept
    waiting = list(db.scalars(select(SphereDecision).where(
        SphereDecision.project_id == pid, SphereDecision.status.in_(("pending", "working")),
        SphereDecision.kind != "check").order_by(SphereDecision.id)))
    out["working"] = [{"kind": d.kind, "actor": d.actor, "at": d.created_at.isoformat()} for d in waiting]
    failed = db.scalars(select(SphereDecision).where(SphereDecision.project_id == pid).order_by(
        SphereDecision.id.desc()).limit(1)).first()
    out["last_error"] = failed.error if failed is not None and failed.status == "failed" else ""
    _page(out, offset, limit)
    return out


@router.get("/sphere/versions")
def versions(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    assert user.project is not None
    out, before = [], store.State()
    for v in store.versions(db, user.project.id):
        after = store.State()
        after.nodes, after.edges = dict(before.nodes), dict(before.edges)
        after.apply(v.ops or [])
        d = store.diff(before, after)
        out.append({"number": v.number, "actor": v.actor, "reason": v.reason, "at": v.created_at.isoformat(),
                    "learned": len(d["learned"]), "forgot": len(d["forgot"]), "changed": len(d["changed"])})
        before = after
    return list(reversed(out))


class Keys(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=200)


def _decide(kind: str, body: Keys, user: Principal, db: Session) -> dict:
    assert user.project is not None
    d = learn.note(db, org_id=user.org_id, project_id=user.project.id, kind=kind, actor=user.name,
                   payload={"keys": body.keys})
    db.flush()
    d.ref = f"{kind}:{d.id}"
    audit(db, user, f"{kind}ed knowledge sphere proposals", str(len(body.keys)), ", ".join(body.keys)[:500])
    db.commit()
    learn.process(db, d)  # no model call: done at once, so the screen shows it
    return {"status": d.status, "error": d.error, "latest": store.latest_number(db, user.project.id)}


@router.post("/sphere/confirm")
def confirm(body: Keys, user: Principal = Depends(allow("approve")), db: Session = Depends(get_db)):
    return _decide("confirm", body, user, db)


@router.post("/sphere/reject")
def reject(body: Keys, user: Principal = Depends(allow("approve")), db: Session = Depends(get_db)):
    return _decide("reject", body, user, db)


class CloudRule(BaseModel):
    text: str = Field(min_length=5, max_length=300)
    detail: str = Field("", max_length=800)
    concept: str = Field("", max_length=80)
    protects: str = Field("other", pattern="^(money|data|access|availability|other)$")
    expect: dict


@router.post("/sphere/cloud-rules")
def add_cloud_rule(body: CloudRule, user: Principal = Depends(allow("approve")), db: Session = Depends(get_db)):
    """KS.2: an approver states a business rule for the cloud, with the check that judges the map against it."""
    assert user.project is not None
    wrong = cloud_rules.problems(body.expect)
    if wrong:
        raise HTTPException(422, " ".join(wrong))
    d = learn.note(db, org_id=user.org_id, project_id=user.project.id, kind="cloud_rule", actor=user.name,
                   payload=body.model_dump())
    db.flush()
    d.ref = f"cloud_rule:{d.id}"
    audit(db, user, "stated a business rule for the cloud", body.text[:200])
    db.commit()
    learn.process(db, d)
    return {"status": d.status, "error": d.error, "latest": store.latest_number(db, user.project.id)}


@router.post("/sphere/cloud-rules/forget")
def forget_cloud_rules(body: Keys, user: Principal = Depends(allow("approve")), db: Session = Depends(get_db)):
    """KS.2: an approver withdraws business rules for the cloud; their open findings close on the next judgement."""
    assert user.project is not None
    d = learn.note(db, org_id=user.org_id, project_id=user.project.id, kind="forget_rule", actor=user.name,
                   payload={"keys": body.keys})
    db.flush()
    d.ref = f"forget_rule:{d.id}"
    audit(db, user, "withdrew business rules for the cloud", ", ".join(body.keys)[:500])
    db.commit()
    learn.process(db, d)
    return {"status": d.status, "error": d.error, "latest": store.latest_number(db, user.project.id)}


@router.get("/sphere/cloud-settings")
def cloud_settings(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    """What a business rule for the cloud can be about: each kind of thing on the map, how many there are, and the
    settings the map holds for them (with an example value), so a rule names a setting that exists."""
    assert user.project is not None
    kinds: dict[str, dict] = {}
    for node in map_nodes(db, user.project.id):
        kind = str(node.type).split("@", 1)[0]
        named = (library().get(kind) or {}).get("display_name") or kind
        entry = kinds.setdefault(kind, {"type": kind, "label": named, "things": [], "settings": {}})
        entry["things"].append({"name": node.name, "key": node.node_key})
        for prop, value in (node.properties or {}).items():
            if isinstance(value, (str, int, float, bool)) and prop not in ("arn",):
                entry["settings"].setdefault(prop, value)
    return sorted(kinds.values(), key=lambda k: k["type"])


@router.post("/sphere/propose")
def propose(user: Principal = Depends(allow("approve")), db: Session = Depends(get_db)):
    """Ask WhiteHats to propose from the newest whole-repository audit (else the newest other finished one)."""
    assert user.project is not None
    pid = user.project.id
    if db.scalar(select(SphereDecision.id).where(SphereDecision.project_id == pid, SphereDecision.kind == "propose",
                                                 SphereDecision.status == "pending").limit(1)) is not None:
        raise HTTPException(409, "WhiteHats is already proposing from an audit.")
    finished = select(Audit).where(Audit.project_id == pid, Audit.status.in_(("complete", "incomplete")))
    # A whole-repository audit maps the whole system; a cloud change's or a pull request's looks at one part of it.
    row = db.scalars(finished.where(Audit.trigger.in_(WHOLE)).order_by(Audit.id.desc()).limit(1)).first() \
        or db.scalars(finished.where(Audit.trigger != "code_change").order_by(Audit.id.desc()).limit(1)).first()
    if row is None:
        raise HTTPException(409, "There is no finished audit of this project to propose from yet.")
    learn.note(db, org_id=user.org_id, project_id=pid, kind="propose", ref=f"audit:{row.id}", actor=user.name,
               payload={"audit_id": row.id})
    audit(db, user, "asked WhiteHats to propose the knowledge sphere", f"audit {row.id}")
    db.commit()
    return {"ok": True, "audit": row.id}


@router.get("/compliance")
def compliance(user: Principal = Depends(allow("see_project")), db: Session = Depends(get_db)):
    """Show project control evidence without drawing conclusions about framework certification."""
    from ..models import Signal
    from ..services import setup_checks

    project_id = user.project.id
    observed = db.scalars(select(Signal).where(
        Signal.org_id == user.org_id, Signal.project_id == project_id, Signal.origin.in_(("live", "local") if local_workspace.ENABLED else ("live",)),
        Signal.closed_at.is_(None), Signal.learning.is_(False),
        Signal.source.in_((cloud_rules.SOURCE, setup_checks.SOURCE)),
    ).order_by(Signal.created_at.desc(), Signal.id.desc()).limit(100))
    nodes = [
        {"key": "policy", "kind": "Concept", "text": "Business policy"},
        {"key": "cloud", "kind": "Concept", "text": "Cloud safeguards"},
    ]
    edges = [{"source": "policy", "target": "cloud", "kind": "RELATES"}]
    if local_workspace.ENABLED:
        nodes, edges = local_workspace.compliance_catalog()
    controls = []
    for finding in observed:
        key = f"finding-{finding.id}"
        parent = "policy" if finding.source == cloud_rules.SOURCE else "cloud"
        node = {"key": key, "kind": "Rule", "text": finding.title, "protects": "data"}
        if local_workspace.ENABLED:
            node.update(label="Finding", severity=finding.severity)
            if finding.fingerprint == local_workspace.BACKUP_POLICY_FINGERPRINT:
                parent = "automated-backups"
            elif (finding.fingerprint or "").startswith("public-api-no-rate-limit|"):
                parent = "api-throttling"
        nodes.append(node)
        edges.append({"source": key, "target": parent, "kind": "ABOUT",
                      **({"detail": finding.detail} if local_workspace.ENABLED else {})})
        controls.append({"key": key, "title": finding.title, "detail": finding.detail,
                         "resource": finding.resource, "severity": finding.severity,
                         "observed_at": finding.created_at.isoformat()})
    return {"nodes": nodes, "edges": edges, "controls": controls}

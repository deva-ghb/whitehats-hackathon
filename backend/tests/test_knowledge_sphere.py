"""KS.1: the knowledge sphere. Only approver decisions change what is known; every change is a version; what is gone
from the code is forgotten; reviews are told the known rules around the files they read."""

from __future__ import annotations

import json
import os
import time

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    Audit,
    Organization,
    Project,
    Remediation,
    Signal,
    SphereDecision,
    SphereGraphState,
    SphereVersion,
    VexStatement,
)
from app.sphere import brief, graph, learn, store

PROPOSAL = {
    "concepts": [{"name": "Order", "detail": "A customer's purchase."}, {"name": "Price", "detail": "What it costs."}],
    "rules": [
        {"text": "An order's price comes from the catalogue, never from the request", "detail": "Else orders are free.",
         "protects": "money", "concepts": ["Order", "Price"],
         "cites": [{"path": "app/orders.py", "first_line": 40, "last_line": 52}]},
        {"text": "A customer sees only their own orders", "protects": "data", "concepts": ["Order"],
         "cites": [{"path": "app/orders.py", "first_line": 60, "last_line": 70}, {"path": "app/auth.py",
                                                                                  "first_line": 1, "last_line": 9}]},
        {"text": "A rule the code does not show", "concepts": ["Order"], "cites": [{"path": "nowhere.py"}]},
    ],
    "relations": [{"from": "Order", "to": "Price"}],
}


@pytest.fixture
def project(client, tmp_path):
    """Acme's main project with an onboarding audit on disk; everything the sphere made is removed afterwards."""
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        p = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
        run = tmp_path / "run"
        (run / "target" / "app").mkdir(parents=True)
        (run / "target" / "app" / "orders.py").write_text("price = catalogue[item]\n")
        (run / "target" / "app" / "auth.py").write_text("owner = user\n")
        (run / "architecture.md").write_text("# Shop\nOrders are priced from the catalogue.\n" * 20)
        (run / "findings.json").write_text(json.dumps([{"verdict": "confirmed", "title": "Orders at zero",
                                                        "trace": [{"file": "app/orders.py", "line": 46}]}]))
        a = Audit(org_id=org.id, project_id=p.id, trigger="onboarding", status="complete", run_dir=str(run))
        db.add(a)
        db.commit()
        made = {"audit": a.id, "signals": [], "fixes": [], "vex": []}
        yield db, org, p, a, made
        db.rollback()
        db.execute(delete(SphereDecision).where(SphereDecision.project_id == p.id))
        db.execute(delete(SphereVersion).where(SphereVersion.project_id == p.id))
        db.execute(delete(SphereGraphState).where(SphereGraphState.project_id == p.id))
        db.execute(delete(VexStatement).where(VexStatement.id.in_(made["vex"])))
        db.execute(delete(Remediation).where(Remediation.id.in_(made["fixes"])))
        db.execute(delete(Signal).where(Signal.id.in_(made["signals"])))
        db.execute(delete(Audit).where(Audit.id == a.id))
        db.commit()


def _learn(db, d: SphereDecision) -> SphereDecision:
    """Process the decision (the background worker may take it first: wait for whichever does)."""
    db.commit()
    learn.process(db, d)
    for _ in range(50):
        db.refresh(d)
        if d.status in ("done", "failed"):
            return d
        time.sleep(0.1)
    raise AssertionError(f"decision {d.id} still {d.status}")


def _answers(monkeypatch, fix_answer=None):
    def ask(role, system, user, agent_id, tokens=6000):
        return PROPOSAL if agent_id.startswith("sphere-propose") else (fix_answer or {})
    monkeypatch.setattr(learn, "_ask", ask)


def _propose(db, org, p, a):
    d = learn.note(db, org_id=org.id, project_id=p.id, kind="propose", ref=f"audit:{a.id}", actor="WhiteHats",
                   payload={"audit_id": a.id})
    return _learn(db, d)


def test_the_first_audit_proposes_and_only_an_approver_makes_it_known(project, monkeypatch):
    db, org, p, a, _ = project
    _answers(monkeypatch)
    assert _propose(db, org, p, a).status == "done"
    now = store.state(db, p.id)
    rules = {k: n for k, n in now.nodes.items() if n["kind"] == "Rule"}
    assert set(rules) == {"rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request",
                          "rule:a-customer-sees-only-their-own-orders"}  # the uncited rule is a guess, left out
    assert all(n["status"] == "proposed" for n in now.nodes.values())
    price = rules["rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request"]
    assert price["protects"] == "money" and price["cites"] == [{"kind": "file", "path": "app/orders.py",
                                                                "lines": [40, 52]}]
    assert "ABOUT|rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request|concept:order" in now.edges
    assert "RELATES|concept:order|concept:price" in now.edges

    keep = ["rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request", "concept:order"]
    _learn(db, learn.note(db, org_id=org.id, project_id=p.id, kind="confirm", actor="Ana Approver",
                          payload={"keys": keep}))
    _learn(db, learn.note(db, org_id=org.id, project_id=p.id, kind="reject", actor="Ana Approver",
                          payload={"keys": ["rule:a-customer-sees-only-their-own-orders"]}))
    now = store.state(db, p.id)
    assert {k for k, n in now.nodes.items() if n["status"] == "known"} == set(keep)
    assert "rule:a-customer-sees-only-their-own-orders" not in now.nodes
    assert any(c.get("label") == "Confirmed by Ana Approver" for c in now.nodes[keep[0]]["cites"])
    assert store.latest_number(db, p.id) == 3  # proposed, confirmed, rejected: three versions
    # any version can be rebuilt, and two compared
    assert store.state(db, p.id, 1).nodes[keep[0]]["status"] == "proposed"
    diff = store.diff(store.state(db, p.id, 2), store.state(db, p.id, 3))
    assert diff["forgot"] == ["rule:a-customer-sees-only-their-own-orders"]


def test_an_approved_fix_teaches_its_rule_and_a_not_affected_mark_an_exception_that_reversing_forgets(
        project, monkeypatch):
    db, org, p, _a, made = project
    _answers(monkeypatch, fix_answer={"existing_rule": "", "text": "An order's price comes from the catalogue",
                                      "detail": "Money.", "concept": "Order", "protects": "money"})
    signal = Signal(org_id=org.id, project_id=p.id, severity="high", title="Orders are recorded at zero",
                    detail="price = 0", entity="app/orders.py:46", source="audit", fingerprint="audit:ks-1")
    db.add(signal)
    db.flush()
    fix = Remediation(org_id=org.id, project_id=p.id, signal_id=signal.id, title="Price from the catalogue",
                      target="app/orders.py", action="Use the catalogue price.", destination="Fix Factory")
    db.add(fix)
    db.flush()
    made["signals"].append(signal.id)
    made["fixes"].append(fix.id)
    learn.note_fix(db, fix, "Ana Approver")
    learn.note_fix(db, fix, "Ana Approver")  # Approvals and Fix Factory both approve: learned once
    db.commit()
    pending = list(db.scalars(select(SphereDecision).where(SphereDecision.project_id == p.id,
                                                           SphereDecision.kind == "fix_approved")))
    assert len(pending) == 1
    _learn(db, pending[0])
    rule = store.state(db, p.id).nodes["rule:an-order-s-price-comes-from-the-catalogue"]
    assert rule["status"] == "known" and rule["taught_by"] == [f"fix:{fix.id}"]
    assert {c["kind"] for c in rule["cites"]} == {"file", "finding", "decision"}

    other = Signal(org_id=org.id, project_id=p.id, severity="medium", title="Free sample orders cost nothing",
                   entity="app/orders.py:80", source="audit", fingerprint="audit:ks-2")
    db.add(other)
    db.flush()
    statement = VexStatement(org_id=org.id, project_id=p.id, fingerprint=other.fingerprint, vulnerability="x",
                             product="pkg:x", status="not_affected", impact_statement="Samples are free by design.",
                             author="Ana Approver", signal_id=other.id)
    db.add(statement)
    db.flush()
    made["signals"].append(other.id)
    made["vex"].append(statement.id)
    _learn(db, learn.note(db, org_id=org.id, project_id=p.id, kind="not_affected", ref=f"vex:{statement.id}",
                          actor="Ana Approver", payload={"statement_id": statement.id}))
    now = store.state(db, p.id)
    exception = next(n for n in now.nodes.values() if n["kind"] == "Exception")
    assert exception["detail"] == "Samples are free by design."
    assert f"EXEMPTS|{exception['key']}|rule:an-order-s-price-comes-from-the-catalogue" in now.edges

    learn.note_reversal(db, org_id=org.id, project_id=p.id, taught=f"vex:{statement.id}", actor="Ana Approver")
    db.commit()
    reversal = db.scalars(select(SphereDecision).where(SphereDecision.project_id == p.id,
                                                       SphereDecision.kind == "reversed")).one()
    _learn(db, reversal)
    now = store.state(db, p.id)
    assert exception["key"] not in now.nodes  # the newer decision wins
    assert "rule:an-order-s-price-comes-from-the-catalogue" in now.nodes  # what another decision taught stays


def test_what_cites_only_things_that_are_gone_is_forgotten(project, monkeypatch):
    db, org, p, a, _ = project
    _answers(monkeypatch)
    _propose(db, org, p, a)
    d = learn.note(db, org_id=org.id, project_id=p.id, kind="check", actor="WhiteHats")
    db.commit()
    assert learn._claim(db, d)
    ops, reason = learn.check(db, d, files={"app/auth.py"}, arns=None)
    by_op = {(o["op"], o.get("key") or o.get("node", {}).get("key")) for o in ops}
    assert ("drop_node", "rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request") in by_op
    kept = next(o["node"] for o in ops if o["op"] == "put_node")
    assert kept["key"] == "rule:a-customer-sees-only-their-own-orders"
    assert [c["path"] for c in kept["lost"]] == ["app/orders.py"] and [c["path"] for c in kept["cites"]] == ["app/auth.py"]
    assert "gone" in reason
    # a repository that cannot be read forgets nothing
    assert learn.check(db, d, files=None, arns=None)[0] == []


def test_a_review_is_told_the_rules_around_the_files_it_reads(project, monkeypatch):
    db, org, p, a, _ = project
    _answers(monkeypatch)
    _propose(db, org, p, a)
    _learn(db, learn.note(db, org_id=org.id, project_id=p.id, kind="confirm", actor="Ana Approver",
                          payload={"keys": ["rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request"]}))
    text = brief.brief(db, p.id, ["app/orders.py"])
    assert "[KNOWN, protects money] rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request" in text
    assert "[PROPOSED, protects data] rule:a-customer-sees-only-their-own-orders" in text
    assert "app/orders.py:40-52" in text
    assert brief.brief(db, p.id, ["README.md"]) == ""  # nothing known around this file


def test_the_screen_reads_any_version_compares_two_and_an_approver_confirms(project, client, ceo, monkeypatch):
    db, org, p, a, _ = project
    _answers(monkeypatch)
    _propose(db, org, p, a)
    key = "rule:an-order-s-price-comes-from-the-catalogue-never-from-the-request"
    done = client.post("/api/sphere/confirm", headers=ceo, json={"keys": [key]})
    assert done.status_code == 200, done.text
    now = client.get("/api/sphere", headers=ceo).json()
    assert now["latest"] == 2 and next(n for n in now["nodes"] if n["key"] == key)["status"] == "known"
    before = client.get("/api/sphere?version=1&compare=0", headers=ceo).json()
    assert before["version"] == 1 and key in before["diff"]["learned"]
    changed = client.get("/api/sphere?version=2&compare=1", headers=ceo).json()["diff"]
    assert changed["changed"] == [key]
    history = client.get("/api/sphere/versions", headers=ceo).json()
    assert [v["number"] for v in history] == [2, 1] and history[1]["learned"] == 4
    assert client.post("/api/sphere/propose", headers=ceo).status_code == 200


def _age_url() -> str:
    return os.environ.get("WH_TEST_SPHERE_DATABASE_URL", "postgresql://whitehats:whitehats-dev@localhost:5455/sphere")


def test_the_graph_database_holds_the_newest_version_and_rebuilds_from_the_versions(project, monkeypatch):
    monkeypatch.setenv("WH_SPHERE_DATABASE_URL", _age_url())
    try:
        with graph.Graph(0):
            pass
    except graph.Unavailable:
        pytest.skip("no PostgreSQL with Apache AGE to test against (WH_TEST_SPHERE_DATABASE_URL)")
    db, org, p, a, _ = project
    _answers(monkeypatch)
    _propose(db, org, p, a)
    assert store.sync_graph(db, p.id)
    with graph.Graph(p.id) as g:
        assert {n["key"] for n in g.nodes()} == set(store.state(db, p.id).nodes)
        assert len(g.edges()) == len(store.state(db, p.id).edges)
        g.clear()  # lost or wiped: it is rebuilt from the versions
    db.get(SphereGraphState, p.id).applied = 0
    db.commit()
    assert store.sync_graph(db, p.id)
    around = {n["key"] for n in store.around(db, p.id, ["app/auth.py"])}
    assert "rule:a-customer-sees-only-their-own-orders" in around and "concept:order" in around
    with graph.Graph(p.id) as g:
        g.clear()


def test_a_large_sphere_comes_a_page_at_a_time_with_each_concept_beside_its_rules():
    from app.routers.sphere import PAGE, _page
    nodes, edges = [], []
    for c in range(60):  # 60 concepts with 5 rules each: 360 items
        nodes.append({"key": f"c{c:02}", "kind": "Concept", "status": "known"})
        for r in range(5):
            nodes.append({"key": f"r{c:02}{r}", "kind": "Rule", "status": "proposed" if c == 59 else "known"})
            edges.append({"kind": "ABOUT", "source": f"r{c:02}{r}", "target": f"c{c:02}"})
    first = {"nodes": list(nodes), "edges": list(edges)}
    _page(first, 0, PAGE)
    assert first["page"] == {"offset": 0, "limit": 250, "total": 360}
    assert len(first["nodes"]) == 250
    shown = {n["key"] for n in first["nodes"]}
    assert all(e["source"] in shown and e["target"] in shown for e in first["edges"])
    assert all(f"c{int(n['key'][1:3]):02}" in shown for n in first["nodes"] if n["kind"] == "Rule")
    # everything proposed is offered to the approver whatever the page, with the concept it is about
    assert len(first["proposed"]) == 5 and [n["key"] for n in first["proposed_concepts"]] == ["c59"]
    rest = {"nodes": list(nodes), "edges": list(edges)}
    _page(rest, 250, PAGE)
    assert len(rest["nodes"]) == 110 and not shown & {n["key"] for n in rest["nodes"]}

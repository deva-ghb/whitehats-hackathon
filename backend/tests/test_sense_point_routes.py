"""Sense points stored per clone version (P6.3): run the analysis, accept, reject, place by hand, and the coverage that follows."""

import json
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Clone, Organization, SensePoint
from app.services import sense_point_store as store
from app.services.clones import create_clone
from app.services.tenancy import delete_tenant_data
from app.services.tenancy import export as export_tenant_data

ROOT = Path(__file__).resolve().parents[3]
BY_HAND = ROOT / "platform" / "e2e" / "screenshots" / "P2.5" / "proving-ground-by-hand.clone.json"
# What a person tells WhiteHats matters in the shop: the two order tables, the receipts bucket and the two secrets.
JEWELS = {"n_dynamodb_1", "n_dynamodb_2", "n_s3_1", "n_ssm_parameter_store_1", "n_ssm_parameter_store_2"}


def _hand_built(slug: str) -> dict:
    document = json.loads(BY_HAND.read_text())
    document["clone"] = slug
    for node in document["nodes"]:
        if node["id"] in JEWELS:
            node["criticality"] = "crown-jewel"
    return document


def _make(client, headers, slug: str) -> int:
    made = client.post("/api/clones", headers=headers, json={"reason": "the hand-built proving ground", "document": _hand_built(slug)})
    assert made.status_code == 200, made.text
    return made.json()["id"]


def _points(client, headers, clone):
    return client.get(f"/api/clones/{clone}/sense-points", headers=headers).json()


def test_a_clone_with_no_analysis_has_no_stored_points_and_says_so(client, engineer):
    clone = _make(client, engineer, "sp-empty")
    found = _points(client, engineer, clone)
    assert found["has_run"] is False and found["points"] == []
    assert found["version"] == 1 and found["is_current"] is True
    assert found["coverage"]["paths_total"] > 0
    assert found["coverage"]["entry_points"] and found["coverage"]["crown_jewels"]


def test_the_analysis_stores_ranked_suggestions_with_rule_based_reasons(client, engineer):
    clone = _make(client, engineer, "sp-ranked")
    done = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer)
    assert done.status_code == 200, done.text
    body = done.json()
    points = body["points"]
    assert len(points) >= 7, [p["node_name"] for p in points]
    assert [p["rank"] for p in points] == list(range(1, len(points) + 1))
    assert {p["status"] for p in points} == {"suggested"} and {p["origin"] for p in points} == {"analysis"}
    assert all("attack paths" in p["reason"] and p["mode"] for p in points)
    assert points[0]["node"] == "n_kms_1" and points[0]["paths"] >= points[-1]["paths"]
    # After the picks that cover every path come the other gaps, the ones on most paths first.
    extras = [p for p in points if "Other suggestions already cover" in p["reason"]]
    assert len(extras) >= 4 and [p["paths"] for p in extras] == sorted((p["paths"] for p in extras), reverse=True)
    assert [p["rank"] for p in extras] == list(range(len(points) - len(extras) + 1, len(points) + 1))
    # No model is connected here and the reasons are still there: they come from the graph and the library.
    assert "Watching it covers them" in points[0]["reason"]
    # The same clone version always gives the same list, and a second run changes nothing.
    again = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer).json()
    assert [(p["node"], p["rank"], p["reason"]) for p in again["points"]] == [(p["node"], p["rank"], p["reason"]) for p in points]
    with SessionLocal() as db:
        assert db.query(SensePoint).filter_by(org_id=db.get(Clone, clone).org_id).filter(
            SensePoint.node_key == "n_kms_1", SensePoint.clone_version_id == db.get(Clone, clone).versions[0].id).count() == 1


def test_accepting_the_top_five_rejecting_one_and_placing_one_by_hand_moves_the_coverage(client, engineer):
    clone = _make(client, engineer, "sp-decisions")
    start = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer).json()
    shares = [start["coverage"]["share"]]
    assert shares[0] == 0.0 and start["coverage"]["paths_total"] > 30
    for point in start["points"][:5]:
        step = client.patch(f"/api/sense-points/{point['id']}", headers=engineer, json={"status": "accepted"})
        assert step.status_code == 200, step.text
        shares.append(step.json()["coverage"]["share"])
    assert shares == sorted(shares) and shares[-1] > shares[0] and shares[1] > 0.5, shares   # the first pick covers most paths
    assert step.json()["counts"] == {"suggested": len(start["points"]) - 5, "accepted": 5, "rejected": 0}
    # A rejection takes nothing away from the coverage and is remembered with who made it.
    sixth = start["points"][5]
    rejected = client.patch(f"/api/sense-points/{sixth['id']}", headers=engineer, json={"status": "rejected"}).json()
    assert rejected["coverage"]["share"] == shares[-1]
    assert next(p for p in rejected["points"] if p["id"] == sixth["id"])["decided_by"] == "Kavya Iyer"
    # A node that is not suggested at all can be placed by hand: accepted at once, and it counts.
    free = "n_function_3"   # the payments worker already logs, so the analysis never suggests it; a person may still want a sense point there
    assert free not in {p["node"] for p in start["points"]}
    placed = client.post(f"/api/clones/{clone}/sense-points", headers=engineer, json={"node": free})
    assert placed.status_code == 200, placed.text
    by_hand = next(p for p in placed.json()["points"] if p["node"] == free)
    assert by_hand["origin"] == "manual" and by_hand["status"] == "accepted" and "Placed by hand by Kavya Iyer" in by_hand["reason"]
    assert placed.json()["coverage"]["share"] >= shares[-1]
    assert placed.json()["counts"]["accepted"] == 6
    # What was decided is what a fresh read says.
    assert _points(client, engineer, clone) == placed.json()
    # A hand placed point is removed, not rejected; a suggestion is put back, not deleted.
    refused = client.delete(f"/api/sense-points/{sixth['id']}", headers=engineer)
    assert refused.status_code == 409 and "placed by hand" in refused.json()["detail"]
    gone = client.delete(f"/api/sense-points/{by_hand['id']}", headers=engineer).json()
    assert free not in {p["node"] for p in gone["points"]}
    assert gone["counts"]["accepted"] == 5
    back = client.patch(f"/api/sense-points/{sixth['id']}", headers=engineer, json={"status": "suggested"}).json()
    assert next(p for p in back["points"] if p["id"] == sixth["id"])["decided_by"] == ""
    assert client.patch(f"/api/sense-points/{sixth['id']}", headers=engineer, json={"status": "maybe"}).status_code == 422
    with SessionLocal() as db:
        actions = {row.action for row in db.scalars(select(AuditLog).where(AuditLog.action.like("sense_points.%")))}
    assert {"sense_points.analyse", "sense_points.accepted", "sense_points.rejected", "sense_points.place", "sense_points.remove"} <= actions


def test_a_point_of_another_company_cannot_be_decided_even_by_a_direct_call(client, engineer):
    clone = _make(client, engineer, "sp-direct")
    point = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer).json()["points"][0]["id"]
    with SessionLocal() as db:
        assert store.decide(db, 0, point, "accepted", "intruder") is None
        assert store.remove(db, 0, point) is None
        assert db.get(SensePoint, point).status == "suggested"


def test_a_node_that_already_logs_is_not_suggested_and_a_node_not_in_the_clone_cannot_be_placed(client, engineer):
    clone = _make(client, engineer, "sp-logging")
    points = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer).json()["points"]
    assert not {p["node"] for p in points} & {"n_cloudwatch_1", "n_function_2"}   # the shop function emits telemetry
    nowhere = client.post(f"/api/clones/{clone}/sense-points", headers=engineer, json={"node": "n_nowhere"})
    assert nowhere.status_code == 404 and "not in this clone" in nowhere.json()["detail"]


def test_points_are_pinned_to_a_version_and_a_new_analysis_keeps_the_decisions(client, engineer):
    clone = _make(client, engineer, "sp-versions")
    first = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer).json()
    top, second = first["points"][0], first["points"][1]
    client.patch(f"/api/sense-points/{top['id']}", headers=engineer, json={"status": "accepted"})
    client.patch(f"/api/sense-points/{second['id']}", headers=engineer, json={"status": "rejected"})
    document = client.get(f"/api/clones/{clone}", headers=engineer).json()["document"]
    document["nodes"][0]["notes"] = "edited after the analysis"
    saved = client.post(f"/api/clones/{clone}/versions", headers=engineer, json={"reason": "a note", "document": document})
    assert saved.json()["version"] == 2
    stale = _points(client, engineer, clone)
    assert stale["version"] == 1 and stale["latest_version"] == 2 and stale["is_current"] is False
    refused = client.post(f"/api/clones/{clone}/sense-points", headers=engineer, json={"node": "n_function_3"})
    assert refused.status_code == 409 and "Run the analysis again" in refused.json()["detail"]
    fresh = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer).json()
    assert fresh["version"] == 2 and fresh["is_current"] is True
    by_node = {p["node"]: p for p in fresh["points"]}
    assert by_node[top["node"]]["status"] == "accepted" and by_node[second["node"]]["status"] == "rejected"
    assert fresh["coverage"]["share"] == stale["coverage"]["share"] or fresh["coverage"]["share"] > 0
    # A hand placed point is carried over too.
    placed = client.post(f"/api/clones/{clone}/sense-points", headers=engineer, json={"node": "n_function_3"})
    assert placed.status_code == 200
    document["nodes"][0]["notes"] = "edited again"
    client.post(f"/api/clones/{clone}/versions", headers=engineer, json={"reason": "again", "document": document})
    carried = client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer).json()
    assert carried["version"] == 3
    assert next(p for p in carried["points"] if p["node"] == "n_function_3")["origin"] == "manual"


def test_the_sense_points_of_a_company_are_exported_and_deleted_with_it(client, engineer):
    clone = _make(client, engineer, "sp-tenancy")
    client.post(f"/api/clones/{clone}/sense-points/analyse", headers=engineer)
    other_doc = _hand_built("sp-tenancy-other")
    with SessionLocal() as db:
        other = Organization(name="Sense Co", slug="sense-co", mfa_required=False)
        db.add(other)
        db.flush()
        theirs = create_clone(db, other.id, other_doc, "test", "setup")
        db.add(SensePoint(org_id=other.id, project_id=theirs.project_id, clone_version_id=theirs.versions[0].id, node_key="n_kms_1",
                          mode="identity", reason="x", status="accepted", origin="manual", decided_by="test"))
        db.commit()
        acme = db.get(Organization, db.get(Clone, clone).org_id)
        path, _digest = export_tenant_data(db, acme)
        rows = json.loads(Path(path).read_text())["tables"]["sense_points"]
        assert rows and all(row["org_id"] == acme.id for row in rows)
        assert any(row["origin"] == "analysis" and row["status"] == "suggested" and row["paths"] > 0 for row in rows)
        # Deleting the other company's data takes its sense points and leaves this company's.
        before = db.query(SensePoint).filter_by(org_id=acme.id).count()
        counts = delete_tenant_data(db, other)
        db.delete(other)
        db.commit()
        assert counts["sense_points"] == 1
        assert db.query(SensePoint).filter_by(org_id=acme.id).count() == before

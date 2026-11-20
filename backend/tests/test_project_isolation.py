"""Reads that summarise a project answer with that project's rows only (AP.2, review 7).

Main and Side each hold an incident, a fix and a finding. Side also holds a dark WhiteHat, a Fix Factory
fix and a question asked there. The account holds raw collector data that belongs to no project yet.
"""

from matrix_account import RAW_LINE, SIDE_RUN, matrix_fixture  # noqa: F401 - the fixture is named matrix
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ChatMessage

SIDE_ONLY = ("INC-2", "Matrix incident INC-2", "Matrix side code fix", "Matrix Side WhiteHat")


def _mentions_side(text: str) -> list[str]:
    return [needle for needle in SIDE_ONLY if needle in text]


def test_the_board_report_counts_only_the_project_it_is_for(client, matrix, monkeypatch):
    from app.routers import admin

    seen = []
    real = admin.pack_context
    monkeypatch.setattr(admin, "pack_context", lambda secs, budget: seen.append(secs) or real(secs, budget))
    engineer = matrix.headers["engineer"]
    facts = client.get("/api/reports/board", headers=engineer).json()["facts"]
    # What the narrative is written from is this project's too.
    written_from = " ".join(section.text for section in seen[0])
    assert "INC-1" in written_from and _mentions_side(written_from) == []
    assert facts["incidents"]["total"] == 1
    assert facts["fixes"] == {"proposed": 1, "approved": 0, "rejected": 1, "pending": 0}
    assert facts["network"]["open"] == 1
    lead_side = client.get("/api/reports/board?project=side", headers=matrix.headers["security_lead"]).json()
    assert lead_side["facts"]["incidents"]["total"] == 1 and lead_side["facts"]["fixes"]["pending"] == 2
    assert lead_side["facts"]["factory"] is None


def test_the_account_board_report_covers_only_projects_the_reader_can_open(client, matrix):
    engineer = client.get("/api/reports/board?account=true", headers=matrix.headers["engineer"]).json()
    assert engineer["facts"]["incidents"]["total"] == 1
    assert [row["slug"] for row in engineer["facts"]["projects"]] == ["main"]
    assert _mentions_side(engineer["narrative"]) == []
    lead = client.get("/api/reports/board?account=true", headers=matrix.headers["security_lead"]).json()
    assert lead["facts"]["incidents"]["total"] == 2
    assert {row["slug"] for row in lead["facts"]["projects"]} == {"main", "side"}


def test_the_briefing_reads_only_the_project_it_is_asked_about(client, matrix):
    engineer = client.get("/api/overview/summary?refresh=true", headers=matrix.headers["engineer"]).json()
    assert "INC-1" in engineer["text"] and _mentions_side(engineer["text"]) == []
    lead = matrix.headers["security_lead"]
    main = client.get("/api/overview/summary?refresh=true", headers=lead).json()
    side = client.get("/api/overview/summary?project=side", headers=lead).json()
    assert "INC-1" in main["text"] and "INC-2" not in main["text"]
    # The cache keeps one briefing per project, so Side never gets Main's.
    assert "INC-2" in side["text"] and "INC-1" not in side["text"]


def test_ask_big_hat_answers_from_this_project_only(client, matrix):
    answer = client.post("/api/assistant/ask", headers=matrix.headers["engineer"],
                         json={"question": "Which incidents are open?"}).json()
    assert "INC-1" in answer["text"] and _mentions_side(answer["text"]) == []
    assert "1 incidents are open" in answer["text"]  # the offline answer counts this project's only
    suggestions = client.get("/api/assistant/suggestions", headers=matrix.headers["engineer"]).json()
    assert any("INC-1" in line for line in suggestions)
    assert all(_mentions_side(line) == [] for line in suggestions)


def test_the_conversation_stays_in_the_project_it_was_held_in(client, matrix):
    engineer = matrix.headers["engineer"]
    texts = [row["text"] for row in client.get("/api/assistant/history", headers=engineer).json()]
    assert "Matrix question asked in Side" not in texts
    assert client.delete("/api/assistant/history", headers=engineer).status_code == 200
    with SessionLocal() as db:
        assert db.get(ChatMessage, matrix.ids["chat_side"]) is not None


def test_the_overview_posture_and_whitehats_count_only_this_projects_whitehats(client, matrix):
    engineer = matrix.headers["engineer"]
    assert client.get("/api/overview", headers=engineer).json()["posture"]["blind_spots"] == 0
    agents = client.get("/api/agents", headers=engineer).json()
    assert agents["summary"] == {"watching": 0, "blind_spots": 0, "roster": 0}
    lead = matrix.headers["security_lead"]
    assert client.get("/api/overview?project=side", headers=lead).json()["posture"]["blind_spots"] == 1
    assert client.get("/api/agents?project=side", headers=lead).json()["summary"]["roster"] == 1


def test_raw_network_connections_are_for_the_roles_that_see_every_project(client, matrix):
    engineer = client.get("/api/network/overview", headers=matrix.headers["engineer"]).json()
    assert engineer["summary"] is None and engineer["flows"] == []
    assert "not tied to one project" in engineer["raw_hidden"]
    assert [row["id"] for row in engineer["findings"]] == [matrix.ids["finding_main"]]
    auditor = client.get("/api/network/overview", headers=matrix.headers["auditor"]).json()
    assert auditor["summary"]["listening"] == 1 and len(auditor["flows"]) == 1 and auditor["raw_hidden"] is None


def test_raw_logs_and_collector_feeds_are_for_the_roles_that_see_every_project(client, matrix):
    ids = matrix.ids
    reads = ("/api/logs/overview", "/api/logs/digests", "/api/logs/search?q=matrix",
             f"/api/logs/templates/{ids['template']}", f"/api/collectors/{ids['collector_logs']}")
    for role in ("project_admin", "approver", "engineer", "viewer"):
        for path in reads:
            assert client.get(path, headers=matrix.headers[role]).status_code == 403, (role, path)
        assert client.post("/api/logs/digest", headers=matrix.headers[role], json={}).status_code == 403, role
    auditor = matrix.headers["auditor"]
    for path in reads:
        assert client.get(path, headers=auditor).status_code == 200, path
    assert RAW_LINE in str(client.get("/api/logs/search?q=matrix", headers=auditor).json())
    # A digest is triage as well, so the auditor, who changes nothing, cannot build one.
    assert client.post("/api/logs/digest", headers=auditor, json={}).status_code == 403
    assert client.post("/api/logs/digest", headers=matrix.headers["security_lead"], json={}).status_code == 200
    # The list of collectors is shared setup, like the list of connections.
    assert client.get("/api/collectors", headers=matrix.headers["viewer"]).status_code == 200


def test_raw_log_digests_stay_out_of_a_project_members_briefing(client, matrix):
    from app.services import briefing

    with SessionLocal() as db:
        main = _main_id(db, matrix.org_id)
        member = briefing.sections(db, matrix.org_id, briefing.one_project(main, account_data=False))
        lead = briefing.sections(db, matrix.org_id, briefing.one_project(main, account_data=True))
    assert "Latest log digest" not in {section.title for section in member}
    assert "Latest log digest" in {section.title for section in lead}


def test_a_side_project_sees_only_the_runs_its_own_fixes_started(client, matrix, monkeypatch):
    # The engine works on Main's code; another project sees, read only, the runs its own fixes started (FF.SRV),
    # with their own scorecard, and starts none by hand. A run a Side fix started is not Main's to read or decide.
    main_run = {"id": "matrix-main-run", "mode": "live", "status": "verified", "approval": "approved",
                "finished_at": "2026-11-01T10:00:00", "score": 9, "cost_usd": 0.5, "seconds": 80}
    side_run = {**main_run, "id": SIDE_RUN, "status": "failed", "approval": "", "cost_usd": 0.25, "seconds": 40}

    def engine(org, method, path, body=None, timeout=None):
        if path == "/api/runs":
            return {"summary": {"runs": 2}, "runs": [main_run, side_run]}
        if path == "/api/inbox":
            return [{"id": "matrix-intent", "title": "Side"}, {"id": "main-intent", "title": "Main"}]
        return {"row": {"id": path}}

    monkeypatch.setattr("app.services.factory.status", lambda org: {"connected": True, "summary": {}})
    monkeypatch.setattr("app.services.factory.call", engine)
    lead = matrix.headers["security_lead"]
    status = client.get("/api/factory/status?project=side", headers=lead).json()
    assert status["connected"] is True and status["own_runs_only"] is True
    runs = client.get("/api/factory/runs?project=side", headers=lead).json()
    assert [run["id"] for run in runs["runs"]] == [SIDE_RUN]
    assert runs["summary"] == {"runs": 1, "verified": 0, "approved": 0, "avg_score": 9.0, "cost_usd": 0.25,
                               "cost_per_fix": 0.25, "avg_seconds": None}
    assert [item["id"] for item in client.get("/api/factory/inbox?project=side", headers=lead).json()] == ["matrix-intent"]
    assert client.get(f"/api/factory/runs/{SIDE_RUN}?project=side", headers=lead).status_code == 200
    assert client.post("/api/factory/runs?project=side", headers=lead, json={"intent": "matrix-intent"}).status_code == 409
    assert "own_runs_only" not in client.get("/api/factory/status", headers=lead).json()  # Main works with the engine
    main = client.get("/api/factory/runs", headers=lead).json()
    assert [run["id"] for run in main["runs"]] == ["matrix-main-run"]
    approver = matrix.headers["approver"]
    for method, path in (("GET", f"/api/factory/runs/{SIDE_RUN}"), ("POST", f"/api/factory/runs/{SIDE_RUN}/approve")):
        res = client.request(method, path, headers=approver)
        assert res.status_code == 404 and res.json()["detail"] == "No Fix Factory run has that id.", path
    sent = client.post(f"/api/remediations/{matrix.ids['fix_side_factory']}/approve?project=side", headers=lead)
    assert sent.status_code == 409 and "Main project" in sent.json()["detail"]


def _main_id(db, org_id: int) -> int:
    from app.projects import main_project_id

    return main_project_id(db, org_id)


def test_no_chat_message_lands_in_main_when_asked_in_side(client, matrix):
    lead = matrix.headers["security_lead"]
    client.post("/api/assistant/ask?project=side", headers=lead, json={"question": "What is open in Side?"})
    with SessionLocal() as db:
        rows = db.scalars(select(ChatMessage).where(ChatMessage.org_id == matrix.org_id,
                                                    ChatMessage.text == "What is open in Side?")).all()
        projects = {row.project_id for row in rows}
        assert len(projects) == 1 and _main_id(db, matrix.org_id) not in projects

"""KB.1: a local copy of OSV.dev gives the same answers as the public OSV API, refreshes only what changed, and works
with the network switched off."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.knowledge import lookup, osv
from app.knowledge.versions import affects, normalise
from app.models import Advisory

RECORDED = Path(__file__).resolve().parent / "fixtures" / "osv" / "recorded.json"
TABLES = ("advisories", "advisory_packages", "advisory_aliases", "knowledge_sources")


def _session(path: str = "sqlite://") -> Session:
    # One shared connection for an in-memory database, usable from the app's threads too.
    engine = create_engine(path, connect_args={"check_same_thread": False}, poolclass=StaticPool) if path == "sqlite://" \
        else create_engine(path)
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in TABLES])
    return sessionmaker(engine)()


def _recorded() -> dict:
    return json.loads(RECORDED.read_text(encoding="utf-8"))


def _loaded(data: dict) -> Session:
    db = _session()
    for item in data["records"].values():
        osv.store(db, item["ecosystem"], item["record"])
    db.commit()
    return db


def _fields(record: dict, ecosystem: str, name: str) -> dict:
    """What the comparison checks for one record and one package: aliases, ranges, versions and fixed versions."""
    entries = [item for item in record.get("affected") or [] if (item.get("package") or {}).get("ecosystem") == ecosystem
               and normalise(ecosystem, item["package"].get("name", "")) == normalise(ecosystem, name)]
    ranges = [[{"type": span.get("type"), "events": span.get("events") or []} for span in item.get("ranges") or []]
              for item in entries]
    fixed = sorted({str(event["fixed"]) for item in entries for span in item.get("ranges") or []
                    for event in span.get("events") or [] if "fixed" in event})
    return {"aliases": sorted(record.get("aliases") or []), "ranges": ranges, "fixed": fixed,
            "versions": [sorted(item.get("versions") or []) for item in entries], "modified": record.get("modified")}


def _compare(db: Session, queries: list[dict]) -> list[str]:
    """Field by field, every difference between the knowledge base and the API's answers."""
    differences = []
    for query in queries:
        ecosystem, name, version = query["ecosystem"], query["name"], query["version"]
        ours = {item["id"]: item for item in lookup.by_package(db, ecosystem, name, version)}
        theirs = {vuln["id"]: vuln for vuln in query["vulns"] if not vuln.get("withdrawn")}
        if set(ours) != set(theirs):
            differences.append(f"{name} {version}: ids {sorted(set(ours) ^ set(theirs))}")
        for advisory_id in sorted(set(ours) & set(theirs)):
            stored = _fields(db.get(Advisory, advisory_id).record, ecosystem, name)
            api = _fields(theirs[advisory_id], ecosystem, name)
            if stored != api:
                differences.append(f"{name} {version} {advisory_id}: {[key for key in api if api[key] != stored[key]]}")
            if ours[advisory_id]["fixed_versions"] != api["fixed"] or sorted(ours[advisory_id]["aliases"]) != api["aliases"]:
                differences.append(f"{name} {version} {advisory_id}: the lookup's facts differ")
    return differences


# ---------------------------------------------------------------- the same answers as the public API


def test_the_lookups_give_the_same_advisories_ranges_and_fixed_versions_as_the_osv_api_on_a_recording():
    data = _recorded()
    db = _loaded(data)
    assert _compare(db, data["queries"]) == []
    s04 = lookup.by_package(db, "PyPI", "PyYAML", "5.3.1")  # S04's pinned dependency, by its own spelling
    assert "CVE-2020-14343" in {alias for item in s04 for alias in item["aliases"]}
    assert all(item["source"] == "OSV.dev" and item["licence"] == "CC-BY-4.0" and item["modified"] for item in s04)


@pytest.mark.skipif(not os.environ.get("OSV_LIVE"), reason="set OSV_LIVE=1 to compare with the live OSV API")
def test_the_lookups_give_the_same_answers_as_the_live_osv_api():
    import httpx
    import record_osv
    queries, db = [], _session()
    with httpx.Client(timeout=60) as client:
        for ecosystem, name, version in record_osv.QUERIES:
            vulns = record_osv._query(client, ecosystem, name, version)
            queries.append({"ecosystem": ecosystem, "name": name, "version": version, "vulns": vulns})
            for vuln in vulns:
                record = client.get(f"{record_osv.BUCKET}/{ecosystem}/{vuln['id']}.json").json()
                osv.store(db, ecosystem, record)
    db.commit()
    assert _compare(db, queries) == []


def test_an_alias_finds_the_advisory_and_an_unknown_id_is_named():
    db = _loaded(_recorded())
    found = lookup.by_id(db, "CVE-2020-14343")
    assert found is not None and "CVE-2020-14343" in found["aliases"]
    text = f"This is {found['id']} (CVE-2020-14343); see also GHSA-0000-0000-0000 and CVE-1999-99999."
    assert lookup.cited_ids(text) == [found["id"], "CVE-2020-14343", "GHSA-0000-0000-0000", "CVE-1999-99999"]
    assert lookup.unknown_ids(db, text) == ["GHSA-0000-0000-0000", "CVE-1999-99999"]


# ---------------------------------------------------------------- refreshing fetches only what changed


def _record(advisory_id: str, modified: str, fixed: str = "2.0.0", name: str = "shop-lib") -> dict:
    return {"id": advisory_id, "modified": modified, "published": "2026-01-01T00:00:00Z", "aliases": [f"CVE-2026-{advisory_id[-4:]}"],
            "summary": f"{advisory_id} summary",
            "affected": [{"package": {"ecosystem": "PyPI", "name": name},
                          "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": fixed}]}]}]}


class FakeBucket:
    """OSV's bucket for one ecosystem: a bulk zip, the change list, and each record. Counts what was downloaded."""

    def __init__(self, records: list[dict]):
        self.records = {record["id"]: record for record in records}
        self.asked: list[str] = []
        self.offline = False

    def __call__(self, url: str, into=None) -> bytes | None:
        if self.offline:
            raise OSError("the network is switched off")
        self.asked.append(url.rsplit("/", 1)[-1])
        if url.endswith("/all.zip"):
            into.write(osv.zip_of(list(self.records.values())))
            return None
        if url.endswith("/modified_id.csv"):
            rows = sorted(self.records.values(), key=lambda record: osv.when(record["modified"]), reverse=True)
            return "".join(f"{record['modified']},{record['id']}\n" for record in rows).encode()
        return json.dumps(self.records[url.rsplit("/", 1)[-1].removesuffix(".json")]).encode()


def test_the_first_refresh_loads_the_export_and_a_second_with_nothing_new_downloads_no_record():
    bucket = FakeBucket([_record("PYSEC-2026-0001", "2026-10-01T10:00:00.123456789Z"),
                         _record("PYSEC-2026-0002", "2026-10-02T10:00:00Z")])
    db = _session()
    [first] = osv.refresh(db, ["PyPI"], fetch=bucket)
    assert (first.fetched, first.stored, first.error) == (2, 2, "")
    [second] = osv.refresh(db, ["PyPI"], fetch=bucket)
    assert (second.fetched, second.stored, second.changed) == (0, 0, [])
    assert bucket.asked[-1] == "modified_id.csv"  # only the change list was read
    bucket.records["PYSEC-2026-0001"] = _record("PYSEC-2026-0001", "2026-10-30T09:00:00Z", fixed="2.1.0")
    bucket.records["PYSEC-2026-0003"] = _record("PYSEC-2026-0003", "2026-10-30T09:30:00Z")
    [third] = osv.refresh(db, ["PyPI"], fetch=bucket)
    assert (third.fetched, sorted(third.changed)) == (2, ["PYSEC-2026-0001", "PYSEC-2026-0003"])
    assert bucket.asked[-2:] == ["PYSEC-2026-0003.json", "PYSEC-2026-0001.json"]  # the two changed records, nothing else
    assert lookup.by_id(db, "PYSEC-2026-0001")["fixed_versions"] == ["2.1.0"]
    assert [item["id"] for item in lookup.changed_since(db, "2026-10-30T00:00:00Z")] == ["PYSEC-2026-0001", "PYSEC-2026-0003"]


def test_a_failed_refresh_keeps_the_copy_and_says_why():
    bucket = FakeBucket([_record("PYSEC-2026-0001", "2026-10-01T10:00:00Z")])
    db = _session()
    osv.refresh(db, ["PyPI"], fetch=bucket)
    bucket.offline = True
    [failed] = osv.refresh(db, ["PyPI"], fetch=bucket)
    assert "network is switched off" in failed.error
    assert lookup.by_id(db, "PYSEC-2026-0001") is not None


def test_the_runners_copy_answers_with_the_network_switched_off(tmp_path, monkeypatch):
    db = _loaded(_recorded())
    copy_path = tmp_path / "knowledge.db"
    assert osv.export_copy(db, copy_path) == len(_recorded()["records"])

    def no_network(*args, **kwargs):
        raise OSError("the network is switched off")

    import socket

    import httpx
    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    copy = _session(f"sqlite:///{copy_path}")
    assert [item["id"] for item in lookup.by_package(copy, "PyPI", "pyyaml", "5.3.1")] == \
        [item["id"] for item in lookup.by_package(db, "PyPI", "pyyaml", "5.3.1")]
    assert lookup.by_id(copy, "CVE-2020-14343") is not None


# ---------------------------------------------------------------- versions in each ecosystem's own order


@pytest.mark.parametrize(("ecosystem", "events", "version", "expected"), [
    ("PyPI", [{"introduced": "0"}, {"fixed": "5.4"}], "5.3.1", True),
    ("PyPI", [{"introduced": "0"}, {"fixed": "5.4"}], "5.4", False),
    ("PyPI", [{"introduced": "0"}, {"fixed": "5.4"}], "5.4b1", True),  # a prerelease of the fix still has it
    ("PyPI", [{"introduced": "2.0"}, {"last_affected": "2.3"}], "2.3", True),
    ("PyPI", [{"introduced": "2.0"}, {"last_affected": "2.3"}], "2.3.1", False),
    ("PyPI", [{"introduced": "1.0"}, {"fixed": "1.2"}, {"introduced": "2.0"}, {"fixed": "2.1"}], "1.5", False),
    ("PyPI", [{"introduced": "1.0"}, {"fixed": "1.2"}, {"introduced": "2.0"}, {"fixed": "2.1"}], "2.0.5", True),
    ("npm", [{"introduced": "0"}, {"fixed": "4.17.19"}], "4.17.15", True),
    ("npm", [{"introduced": "0"}, {"fixed": "4.17.19"}], "4.17.19", False),
    ("npm", [{"introduced": "0"}, {"fixed": "1.2.6"}], "1.2.6-beta.1", True),
    ("npm", [{"introduced": "1.0.0"}], "0.9.9", False),
    ("npm", [{"introduced": "1.0.0"}], "not-a-version", False),
])
def test_a_version_is_inside_a_range_as_osv_defines_it(ecosystem, events, version, expected):
    assert affects(ecosystem, {"ranges": [{"type": "ECOSYSTEM" if ecosystem == "PyPI" else "SEMVER", "events": events}]},
                   version) is expected


def test_package_names_are_matched_the_way_each_index_matches_them():
    assert normalise("PyPI", "PyYAML") == normalise("PyPI", "pyyaml") == "pyyaml"
    assert normalise("PyPI", "zope.interface") == normalise("PyPI", "Zope_Interface") == "zope-interface"
    assert normalise("npm", "@types/Node") == "@types/Node"


# ---------------------------------------------------------------- a new advisory becomes a finding, with no code change


def test_a_new_advisory_for_a_package_shop_api_uses_is_a_finding_on_shop_api_within_one_refresh(client):  # client: the app's database
    from sqlalchemy import delete, select

    from app.db import SessionLocal
    from app.knowledge import watch
    from app.knowledge.service import refresh_and_watch
    from app.models import AdvisoryAlias, AdvisoryPackage, KnowledgeSource, Organization, Project, Signal
    from app.services.clones import create_clone, save_version

    document = {"schema_version": 1, "clone": "kb-watch", "environment": "prod", "library_version": "2026.10",
                "version": {"number": 1, "author": "test", "reason": "setup"}, "groups": [], "edges": [],
                "access_paths": [], "credential_refs": [],
                "nodes": [{"id": "n_shop_api", "type": "service.function@2", "name": "shop-api", "environment": "prod",
                           "properties": {"packages": ["PyPI:pyyaml==5.3.1", "PyPI:boto3==1.35.0"]}},
                          {"id": "n_other", "type": "service.function@2", "name": "other", "environment": "prod",
                           "properties": {"packages": ["PyPI:pyyaml==6.0.2"]}}]}
    bucket = FakeBucket([_record("PYSEC-2026-0101", "2026-10-01T10:00:00Z", name="left-pad-py")])
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
        clone = create_clone(db, org.id, document, "test", "setup", project_id=project.id)
        try:
            first = refresh_and_watch(db, ["PyPI"], fetch=bucket)
            assert (first["changed"], first["findings"]) == (1, 0)  # an advisory for a package nothing uses
            bucket.records["PYSEC-2026-0102"] = _record("PYSEC-2026-0102", "2026-10-30T09:00:00Z", fixed="5.4", name="PyYAML")
            second = refresh_and_watch(db, ["PyPI"], fetch=bucket)
            assert second["ecosystems"][0]["fetched"] == 1 and (second["changed"], second["findings"]) == (1, 1)
            [found] = db.scalars(select(Signal).where(Signal.source == watch.SOURCE, Signal.clone_id == clone.id)).all()
            assert (found.entity, found.node_key, found.project_id, found.origin) == ("shop-api", "n_shop_api", project.id, "live")
            assert found.title.startswith("PYSEC-2026-0102 (CVE-2026-0102) in pyyaml 5.3.1")
            assert "Fixed in: 5.4." in found.detail and "OSV.dev" in found.detail and "CC BY 4.0" in found.detail
            third = refresh_and_watch(db, ["PyPI"], fetch=bucket)  # nothing new: nothing downloaded, nothing raised again
            assert (third["ecosystems"][0]["fetched"], third["findings"]) == (0, 0)
            assert len(db.scalars(select(Signal).where(Signal.source == watch.SOURCE)).all()) == 1
        finally:
            for node in document["nodes"]:  # other tests read every clone: its latest version uses no package any more
                node["properties"] = {}
            save_version(db, org.id, clone.id, {**document, "version": {"number": 2, "author": "test", "reason": "tidy"}},
                         "test", "tidy")
            db.execute(delete(Signal).where(Signal.source == watch.SOURCE))
            for model in (AdvisoryAlias, AdvisoryPackage, Advisory, KnowledgeSource):
                db.execute(delete(model))
            db.commit()


# ---------------------------------------------------------------- what confirmation does with the knowledge base


def _dependency_decision(tmp_path):
    from app.decisions_api.suspect import Decision, Evidence, Suspect

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "shop-api"\ndependencies = ["pyyaml==5.3.1"]\n', encoding="utf-8")
    suspect = Suspect("osv-scanner", "CVE-2020-14343", "PyYAML: arbitrary code execution", Evidence("pyproject.toml", 3, 3),
                      "critical", ["CWE-20"])
    return Decision("suspect", [suspect], [])


def _asking(explanation: str, sent: list):
    from app.confirmation import Reply

    def ask(system, user):
        sent.append(user)
        return Reply(json.dumps({"verdict": "confirmed", "category": "vulnerable dependency", "severity": "critical",
                                 "confidence": "high", "explanation": explanation}), "test-model", 900, 60)
    return ask


def test_a_dependency_finding_is_confirmed_with_the_knowledge_bases_facts_and_an_invented_id_is_flagged(tmp_path):
    from app.confirmation import confirm
    from app.confirmation.confirm import Knowledge
    from app.confirmation.prompt import INPUT_BUDGET_TOKENS, SYSTEM
    from app.services.llm import estimate_tokens

    db = _loaded(_recorded())
    knowledge = Knowledge.of(db)
    decision = _dependency_decision(tmp_path)
    known = lookup.by_id(db, "CVE-2020-14343")
    sent: list = []
    [good] = confirm(decision, tmp_path, _asking(f"{known['id']} (CVE-2020-14343): yaml.load runs code; fixed in 5.4.", sent),
                     knowledge=knowledge)
    assert good.status == "confirmed" and good.error == ""
    advisory = json.loads(sent[0])["check"]["advisory"]
    assert (advisory["id"], advisory["source"]) == (known["id"], "OSV.dev") and "5.4" in advisory["fixed_versions"]
    assert estimate_tokens(SYSTEM) + estimate_tokens(sent[0]) <= INPUT_BUDGET_TOKENS
    [made_up] = confirm(decision, tmp_path, _asking("Also covered by CVE-2031-00001 and GHSA-zzzz-zzzz-zzzz.", []),
                        knowledge=knowledge)
    assert made_up.status == "unconfirmed"
    assert "CVE-2031-00001, GHSA-zzzz-zzzz-zzzz" in made_up.error and "not shown as confirmed" in made_up.error


def test_the_advisory_facts_stay_inside_the_prompt_budget_however_long_the_record_and_the_file(tmp_path):
    from app.confirmation import confirm
    from app.confirmation.confirm import Knowledge
    from app.confirmation.prompt import INPUT_BUDGET_TOKENS, SYSTEM
    from app.services.llm import estimate_tokens

    long = {"id": "GHSA-long-long-long", "aliases": ["CVE-2020-14343"], "summary": "x " * 5000, "fixed_versions": ["5.4"] * 40,
            "affected": [{"package": {"name": "pyyaml"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "5.4"}]}] * 50}],
            "modified": "2026-10-30T00:00:00Z", "source": "OSV.dev"}
    (tmp_path / "pyproject.toml").write_text("\n".join(f"# line {n} " + "y" * 200 for n in range(400)), encoding="utf-8")
    sent: list = []
    confirm(_dependency_decision(tmp_path), tmp_path, _asking("ok", sent),
            knowledge=Knowledge(lambda advisory_id: long, lambda text: []))
    assert estimate_tokens(SYSTEM) + estimate_tokens(sent[0]) <= INPUT_BUDGET_TOKENS
    assert len(json.loads(sent[0])["check"]["advisory"]["summary"]) <= 300


def test_big_hat_reads_the_advisory_a_question_names_and_marks_an_id_it_cannot_back(client, ceo):
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import AdvisoryAlias, AdvisoryPackage, KnowledgeSource
    from app.routers.core import advisory_section, unknown_note

    with SessionLocal() as db:
        record = _record("PYSEC-2026-0201", "2026-10-30T09:00:00Z", fixed="5.4", name="PyYAML")
        osv.store(db, "PyPI", record)
        db.commit()
        try:
            section = advisory_section(db, "Is CVE-2026-0201 a problem for us, and what about GHSA-zzzz-zzzz-zzzz?")
            assert section is not None and section.priority == 1
            assert "[ADV-PYSEC-2026-0201] CVE-2026-0201." in section.text and "Fixed in: 5.4." in section.text
            assert "GHSA-zzzz-zzzz-zzzz: not in the knowledge base." in section.text
            assert unknown_note(db, "See PYSEC-2026-0201.") == ""
            assert "CVE-2099-12345 is not in WhiteHats' knowledge base" in unknown_note(db, "Also CVE-2099-12345.")
            answer = client.post("/api/assistant/ask", headers=ceo, json={"question": "What is CVE-2026-0201?"}).json()
            assert any(title.startswith("Advisories") for title in answer["meta"]["included"])
        finally:
            for model in (AdvisoryAlias, AdvisoryPackage, Advisory, KnowledgeSource):
                db.execute(delete(model))
            db.commit()


def test_anyone_in_an_account_reads_the_knowledge_base_and_only_the_platform_operator_refreshes_it(client, ceo, engineer, operator):
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import AdvisoryAlias, AdvisoryPackage, KnowledgeSource

    with SessionLocal() as db:
        osv.store(db, "PyPI", _record("PYSEC-2026-0301", "2026-10-30T09:00:00Z", fixed="5.4", name="PyYAML"))
        db.add(KnowledgeSource(ecosystem="PyPI", cursor="2026-10-30T09:00:00Z", records=1))
        db.commit()
    try:
        status = client.get("/api/knowledge", headers=engineer).json()
        assert status["attribution"].startswith("Vulnerability data from OSV.dev") and status["licence"] == "CC-BY-4.0"
        assert [(row["ecosystem"], row["records"]) for row in status["ecosystems"]] == [("PyPI", 1)]
        found = client.get("/api/knowledge/packages", headers=engineer,
                           params={"ecosystem": "PyPI", "name": "pyyaml", "version": "5.3.1"}).json()
        assert [item["id"] for item in found["advisories"]] == ["PYSEC-2026-0301"]
        assert client.get("/api/knowledge/packages", headers=engineer,
                          params={"ecosystem": "PyPI", "name": "pyyaml", "version": "5.4"}).json()["advisories"] == []
        assert client.get("/api/knowledge/advisories/CVE-2026-0301", headers=engineer).json()["id"] == "PYSEC-2026-0301"
        assert client.get("/api/knowledge/advisories/CVE-2099-0001", headers=engineer).status_code == 404
        changes = client.get("/api/knowledge/changes", headers=engineer, params={"since": "2026-10-29T00:00:00Z"}).json()
        assert [item["id"] for item in changes["changes"]] == ["PYSEC-2026-0301"]
        assert client.get("/api/knowledge/packages", headers=engineer,
                          params={"ecosystem": "Go", "name": "x"}).status_code == 400
        assert client.post("/api/knowledge/refresh", headers=ceo, json={}).status_code == 403
        assert client.post("/api/knowledge/refresh", headers=operator, json={"ecosystems": ["Go"]}).status_code == 400
    finally:
        with SessionLocal() as db:
            for model in (AdvisoryAlias, AdvisoryPackage, Advisory, KnowledgeSource):
                db.execute(delete(model))
            db.commit()

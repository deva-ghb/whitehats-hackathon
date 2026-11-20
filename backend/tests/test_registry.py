"""REG.1: the WhiteHats Registry builds a fixed library from its upstream release, keeps it only when the proof sees
the flaw before and not after, and serves it to build systems that hold a registry token."""

import base64
import hashlib
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Organization, RegistryPackage, RegistryToken
from app.registry import build as registry_build
from app.registry.recipes import FLAW_SEEN, PYPDF2_3_0_1

TOY = replace(PYPDF2_3_0_1, key="toy", name="Toy_PDF", upstream_version="1.0.0", package_dirs=("toypdf",),
              version_file="toypdf/_version.py",
              patch="--- a/toypdf/reader.py\n+++ b/toypdf/reader.py\n@@ -1 +1 @@\n-LOOPS = True\n+LOOPS = False\n")


def _toy_source(recipe, root: Path) -> Path:
    """A tiny upstream release laid out as an sdist, in place of the download from PyPI."""
    tree = root / "unpacked" / "Toy_PDF-1.0.0"
    (tree / "toypdf").mkdir(parents=True)
    (tree / "toypdf" / "__init__.py").write_text("")
    (tree / "toypdf" / "_version.py").write_text('__version__ = "1.0.0"\n')
    (tree / "toypdf" / "reader.py").write_text("LOOPS = True\n")
    (tree / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: Toy_PDF\nVersion: 1.0.0\nSummary: toy\n")
    return tree


def _runner(before: int, after: int):
    def run(proof, *, original_tree, wheel):
        if original_tree is not None:
            assert (original_tree / "toypdf" / "reader.py").read_text() == "LOOPS = True\n"  # the untouched release
            return registry_build.Ran(before, "Toy 1.0.0: did not end")
        return registry_build.Ran(after, "Toy 1.0.0+whitehats.1: ended at once")
    return run


@pytest.fixture
def acme(client, monkeypatch, tmp_path):
    monkeypatch.setattr(registry_build, "_upstream_source", _toy_source)
    monkeypatch.setattr(registry_build, "DATA_DIR", tmp_path)
    with SessionLocal() as db:
        org_id = db.scalar(select(Organization.id).where(Organization.slug == "acme"))
    yield org_id
    with SessionLocal() as db:
        db.execute(delete(RegistryPackage).where(RegistryPackage.name == TOY.name))
        db.execute(delete(RegistryToken).where(RegistryToken.name.like("test %")))
        db.commit()


def test_a_fixed_build_is_kept_only_when_the_proof_sees_the_flaw_and_then_not(acme):
    with SessionLocal() as db:
        with pytest.raises(registry_build.BuildRefused, match="did not see the flaw"):
            registry_build.build(db, acme, TOY, by="test", runner=_runner(before=1, after=0))  # failed for another reason
        with pytest.raises(registry_build.BuildRefused, match="did not pass its proof"):
            registry_build.build(db, acme, TOY, by="test", runner=_runner(before=FLAW_SEEN, after=FLAW_SEEN))
        row = registry_build.build(db, acme, TOY, by="test", runner=_runner(before=FLAW_SEEN, after=0))
        assert (row.version, row.status) == ("1.0.0+whitehats.1", "proved")
        assert row.proof["before"]["passed"] is False and row.proof["after"]["passed"] is True
        again = registry_build.build(db, acme, TOY, by="test", runner=_runner(before=FLAW_SEEN, after=0))
        assert again.version == "1.0.0+whitehats.2" and again.sha256 != row.sha256  # the version is in the bytes


def test_the_wheel_holds_the_fix_the_new_version_and_a_true_record(acme, tmp_path):
    with SessionLocal() as db:
        row = registry_build.build(db, acme, TOY, by="test", runner=_runner(before=FLAW_SEEN, after=0))
    wheel = tmp_path / "registry" / str(acme) / row.filename
    assert row.filename == "Toy_PDF-1.0.0+whitehats.1-py3-none-any.whl"
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == row.sha256
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("toypdf/reader.py") == b"LOOPS = False\n"
        assert archive.read("toypdf/_version.py") == b'__version__ = "1.0.0+whitehats.1"\n'
        info = "Toy_PDF-1.0.0+whitehats.1.dist-info"
        assert b"\nVersion: 1.0.0+whitehats.1\n" in archive.read(f"{info}/METADATA")
        for line in archive.read(f"{info}/RECORD").decode().splitlines():
            name, digest, _ = line.split(",")
            if digest:
                data = archive.read(name)
                assert digest == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _basic(token: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"__token__:{token}".encode()).decode()}


def test_build_systems_install_with_a_token_and_nothing_else(client, ceo, acme):
    with SessionLocal() as db:
        row = registry_build.build(db, acme, TOY, by="test", runner=_runner(before=FLAW_SEEN, after=0))
        filename, sha = row.filename, row.sha256
    made = client.post("/api/registry/tokens", headers=ceo, json={"name": "test proving-ground CI"})
    assert made.status_code == 200 and made.json()["token"].startswith("whr_")
    token = made.json()["token"]
    refused = client.get("/api/registry/simple/")
    assert refused.status_code == 401 and refused.headers["www-authenticate"].startswith("Basic")
    assert client.get("/api/registry/simple/", headers=_basic("whr_wrong")).status_code == 401
    index = client.get("/api/registry/simple/", headers=_basic(token))
    assert index.status_code == 200 and 'href="toy-pdf/"' in index.text
    page = client.get("/api/registry/simple/toy-pdf/", headers=_basic(token))
    assert f'href="../../files/{filename}#sha256={sha}"' in page.text
    got = client.get(f"/api/registry/files/{filename}", headers=_basic(token))
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == sha
    listed = client.get("/api/registry", headers=ceo).json()
    assert any(p["version"] == "1.0.0+whitehats.1" for p in listed["packages"])
    assert all("token" not in t for t in listed["tokens"])  # shown once, never again
    client.delete(f"/api/registry/tokens/{made.json()['id']}", headers=ceo)
    assert client.get("/api/registry/simple/", headers=_basic(token)).status_code == 401


def test_a_token_reads_only_its_own_company(client, acme):
    with SessionLocal() as db:
        registry_build.build(db, acme, TOY, by="test", runner=_runner(before=FLAW_SEEN, after=0))
        other = Organization(name="Other Registry Co", slug="other-registry-co")
        db.add(other)
        db.flush()
        db.add(RegistryToken(org_id=other.id, name="test other", token_hash=hashlib.sha256(b"whr_other").hexdigest(),
                             created_by="test"))
        db.commit()
        other_id = other.id
    try:
        assert "toy-pdf" not in client.get("/api/registry/simple/", headers=_basic("whr_other")).text
        assert client.get("/api/registry/simple/toy-pdf/", headers=_basic("whr_other")).status_code == 404
    finally:
        with SessionLocal() as db:
            db.execute(delete(RegistryToken).where(RegistryToken.org_id == other_id))
            db.execute(delete(Organization).where(Organization.id == other_id))
            db.commit()


def test_only_who_manages_connections_makes_tokens_or_builds(client, engineer):
    assert client.post("/api/registry/tokens", headers=engineer, json={"name": "test nope"}).status_code == 403
    assert client.post("/api/registry/builds", headers=engineer, json={"recipe": PYPDF2_3_0_1.key}).status_code == 403


def test_a_proved_build_is_not_affected_by_the_advisories_it_fixed_and_only_those(acme):
    from app.decisions_api.suspect import NOT_AFFECTED, OPEN, Evidence, Suspect
    from app.registry import statements

    with SessionLocal() as db:
        registry_build.build(db, acme, replace(TOY, advisories=("CVE-2023-36464",)), by="test",
                             runner=_runner(before=FLAW_SEEN, after=0))
        covering = statements.for_company(db, acme)

    def finding(package, rule="CVE-2023-36464", engine="osv-scanner"):
        return Suspect(engine=engine, rule_id=rule, title="loop", severity="medium",
                       evidence=Evidence("shop/uv.lock", 3, 3, package=package))

    fixed = [s for s in covering if s.package == "toy-pdf@1.0.0+whitehats.1"]
    assert fixed and NOT_AFFECTED == fixed[0].status and "Toy 1.0.0+whitehats.1: ended at once" in fixed[0].reason
    assert fixed[0].covers(finding("toy-pdf@1.0.0+whitehats.1"))
    assert not fixed[0].covers(finding("toy-pdf@1.0.0"))  # the upstream release is still affected
    assert not fixed[0].covers(finding("toy-pdf@1.0.0+whitehats.1", rule="CVE-2099-0001"))  # another advisory is not
    assert not fixed[0].covers(finding("toy-pdf@1.0.0+whitehats.1", engine="semgrep"))
    assert OPEN != NOT_AFFECTED


def test_the_manifest_is_pinned_to_the_build_from_the_registry_index():
    from app.registry import switch

    before = ('[project]\nname = "shop-api"\nversion = "0.1.0"\ndependencies = ["PyPDF2==3.0.1"]\n\n'
              '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    after = switch.pinned_manifest(before, "pypdf2", "3.0.1", "3.0.1+whitehats.1")
    assert 'dependencies = ["PyPDF2==3.0.1+whitehats.1"]' in after and 'testpaths = ["tests"]' in after
    assert '[[tool.uv.index]]\nname = "whitehats"' in after and "explicit = true" in after
    assert 'pypdf2 = { index = "whitehats" }' in after
    with pytest.raises(switch.NoSwitch, match="does not pin"):
        switch.pinned_manifest(before.replace("==3.0.1", ">=3.0"), "pypdf2", "3.0.1", "3.0.1+whitehats.1")


def test_only_a_dependency_finding_in_a_uv_lockfile_is_a_registry_target():
    from app.registry import switch

    found = {"engine": "osv-scanner", "rule_id": "CVE-2023-36464",
             "evidence": {"path": "shop/uv.lock", "package": "pypdf2@3.0.1"}}
    target = switch.target(found)
    assert target == switch.Target("pypdf2", "3.0.1", "CVE-2023-36464", "shop")
    assert switch.recipe_for(target) is PYPDF2_3_0_1
    assert switch.recipe_for(replace(target, version="3.0.0")) is None
    assert switch.target({**found, "engine": "semgrep"}) is None
    assert switch.target({**found, "evidence": {"path": "shop/requirements.txt", "package": "pypdf2@3.0.1"}}) is None


def test_a_moderate_finding_on_a_library_the_registry_covers_is_offered_the_switch(acme, monkeypatch):
    from app.models import Project, Remediation, Signal
    from app.registry import switch
    from app.services import fix_jobs

    item = {"severity": "medium", "suspect": {"engine": "osv-scanner", "rule_id": "CVE-2023-36464", "title": "loop",
                                               "evidence": {"path": "shop/uv.lock", "start_line": 3, "package": "pypdf2@3.0.1"}}}
    made = []
    monkeypatch.setattr(switch, "ensure_build", lambda db, org, found: made.append(found) or replace_row(found))
    with SessionLocal() as db:
        project = db.scalar(select(Project.id).where(Project.org_id == acme, Project.slug == "main"))
        signal = Signal(org_id=acme, project_id=project, severity="medium", title="loop", fingerprint="x|shop/uv.lock|")
        db.add(signal)
        db.flush()
        try:
            row = fix_jobs.suggest(db, signal, item)
            assert row is not None and row.title == "Fix: use the WhiteHats Registry build of pypdf2 3.0.1"
            assert "PyPDF2 3.0.1+whitehats.1 from the WhiteHats Registry" in row.action and made
            other = Signal(org_id=acme, project_id=project, severity="medium", title="x", fingerprint="y|a.py|")
            db.add(other)
            db.flush()
            plain = {**item, "suspect": {**item["suspect"], "engine": "semgrep", "evidence": {"path": "a.py", "start_line": 1}}}
            assert fix_jobs.suggest(db, other, plain) is None  # a moderate code finding is not offered a fix
        finally:
            db.rollback()
            db.execute(delete(Remediation).where(Remediation.title.like("Fix: use the WhiteHats Registry build%")))
            db.commit()


def replace_row(found):
    return RegistryPackage(name="PyPDF2", version="3.0.1+whitehats.1", upstream_version=found.version)


def test_with_no_fixed_build_possible_it_goes_to_a_person_with_the_reason(acme):
    from app.registry import switch

    with SessionLocal() as db, pytest.raises(switch.NoSwitch, match="A person has to decide"):
        switch.ensure_build(db, acme, switch.Target("leftpad", "1.0.0", "CVE-2099-0001", "shop"))


def test_fix_factory_gets_a_one_hour_token_with_its_dispatch_token_only(client, acme):
    from datetime import timedelta

    from app.config import settings

    object.__setattr__(settings, "factory_token", "dispatch-token")
    try:
        assert client.post("/api/factory/registry-access").status_code == 401
        given = client.post("/api/factory/registry-access", headers={"Authorization": "Bearer dispatch-token"})
        assert given.status_code == 200 and given.json()["seconds"] == 3600
        assert given.json()["index_url"].endswith("/api/registry/simple/")
        token = given.json()["token"]
        assert client.get("/api/registry/simple/", headers=_basic(token)).status_code == 200
        with SessionLocal() as db:
            row = db.scalar(select(RegistryToken).where(RegistryToken.token_hash == hashlib.sha256(token.encode()).hexdigest()))
            assert row.name == "Fix Factory (one run)" and row.org_id == acme
            row.expires_at = row.expires_at - timedelta(hours=2)
            db.commit()
        assert client.get("/api/registry/simple/", headers=_basic(token)).status_code == 401  # spent after its hour
    finally:
        object.__setattr__(settings, "factory_token", "")
        with SessionLocal() as db:
            db.execute(delete(RegistryToken).where(RegistryToken.name == "Fix Factory (one run)"))
            db.commit()


def test_an_open_finding_the_registry_build_covers_is_marked_not_affected_not_closed(acme):
    from app.changes import findings
    from app.decisions_api.suspect import NOT_AFFECTED
    from app.models import ChangeEvent, Connector, Project, Signal
    from app.services import posture

    key = "CVE-2023-36464|shop/uv.lock|"
    with SessionLocal() as db:
        project = db.scalar(select(Project.id).where(Project.org_id == acme, Project.slug == "main"))
        connector = db.scalar(select(Connector.id).where(Connector.org_id == acme, Connector.key == "github"))
        row = Signal(org_id=acme, project_id=project, severity="high", title="PyPDF2 loop", fingerprint=key,
                     resource="https://github.com/acme/shop", branch="feature/statements", source=findings.SOURCE)
        db.add(row)
        db.commit()
        before = posture.compute(db, acme, project_id=project)["score"]
        change = ChangeEvent(org_id=acme, connector_id=connector, project_ids=[project], repo="acme/shop",
                             branch="feature/statements", head_sha="f" * 40, status="decided", files=["shop/uv.lock"],
                             confirmations=[], decision={"suspects": [
                                 {"rule_id": "CVE-2023-36464", "status": NOT_AFFECTED,
                                  "evidence": {"path": "shop/uv.lock", "resource": ""}}]})
        db.add(change)
        db.commit()
        try:
            counts = findings.apply(db, change, still_present={key})
            db.refresh(row)
            assert counts["marked"] == 1 and counts["closed"] == 0
            assert row.vex_status == NOT_AFFECTED and row.closed_at is None  # still reported, marked
            assert posture.compute(db, acme, project_id=project)["score"] == before + 10  # and no longer counted
        finally:
            db.execute(delete(Signal).where(Signal.id == row.id))
            db.execute(delete(ChangeEvent).where(ChangeEvent.id == change.id))
            db.commit()

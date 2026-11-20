"""The per-company vault. Values stay sealed. References are what a clone, a response, a log and an export may hold."""

import hmac
import logging
from pathlib import Path

from conftest import PASSWORD, login
from cryptography.fernet import InvalidToken
from sqlalchemy import create_engine, select, text

from app.config import settings
from app.db import SessionLocal, engine
from app.models import AuditLog, Organization, User, VaultSecret
from app.projects import ensure_main_project
from app.secret_scan import collect, database_cells, find_leaks
from app.security import hash_password, tenant_box
from app.services.tenancy import delete_tenant_data, export
from app.vault_store import VaultError, open_secret

SENTINEL = "whv sample value not a key"
REPLACED = "whv replaced value not a key"
OTHER = "whv other company value not a key"
PLANTED = "whv planted value not a key"
REFERENCE = "vault://tenant/acme/p31-github-app"
SCOPE = "repository:read"
# Values other tests submit as secrets. The scan fails if any of them sit in clear text.
SUITE_SECRETS = (PASSWORD, "sk-test-1234567890", "sk_live_" + "a" * 16)


def test_no_secret_in_the_database_responses_logs_or_exports(client, ceo, engineer, caplog):
    caplog.set_level(logging.INFO, logger="whitehats.vault")
    seen: list[tuple[str, str]] = []
    saved = _call(client, seen, "put", "/api/vault/secrets", headers=ceo,
                  json={"reference": REFERENCE, "value": SENTINEL, "scope": SCOPE})
    assert saved.status_code == 200, saved.text
    assert set(saved.json()) == {"reference", "set", "scope"}
    assert saved.json()["reference"] == REFERENCE and saved.json()["set"] is True and saved.json()["scope"] == SCOPE
    listed = _call(client, seen, "get", "/api/vault/secrets", headers=ceo)
    assert listed.status_code == 200
    item = next(row for row in listed.json()["secrets"] if row["reference"] == REFERENCE)
    assert item["set"] is True and item["scope"] == SCOPE
    assert set(item) == {"reference", "set", "scope", "updated_at"}
    replaced = _call(client, seen, "put", "/api/vault/secrets", headers=ceo,
                     json={"reference": REFERENCE, "value": REPLACED, "scope": SCOPE})
    assert replaced.status_code == 200
    refused = _call(client, seen, "put", "/api/vault/secrets", headers=engineer,
                    json={"reference": REFERENCE, "value": SENTINEL, "scope": SCOPE})
    assert refused.status_code == 403
    assert "Manage connections, collectors and the Runner" in refused.json()["detail"]
    climbed = _call(client, seen, "put", "/api/vault/secrets", headers=ceo, json={
        "reference": "vault://tenant/acme/../vaultco/github-app", "value": SENTINEL, "scope": SCOPE})
    assert climbed.status_code == 422 and "parent segment" in climbed.json()["detail"]
    foreign = _call(client, seen, "put", "/api/vault/secrets", headers=ceo, json={
        "reference": "vault://tenant/vaultco/github-app", "value": SENTINEL, "scope": SCOPE})
    assert foreign.status_code == 422 and "another company" in foreign.json()["detail"]
    echoed = _call(client, seen, "put", "/api/vault/secrets", headers=ceo, json={
        "reference": REFERENCE, "value": SENTINEL, "scope": SENTINEL})
    assert echoed.status_code == 422 and "must not contain the secret" in echoed.json()["detail"]
    broken = _call(client, seen, "put", "/api/vault/secrets", content=b'{"value": "whv sample value not a key"',
                   headers={**ceo, "content-type": "application/json"})
    assert broken.status_code == 422 and broken.json()["detail"] == "Send a JSON object with a reference and a value."
    visible = _call(client, seen, "get", "/api/vault/secrets", headers=engineer)
    assert any(row["reference"] == REFERENCE and row["set"] is True for row in visible.json()["secrets"])
    created = _call(client, seen, "post", "/api/clones", headers=engineer, json=_save("vault-holding"))
    assert created.status_code == 200, created.text
    exported = _call(client, seen, "get", f"/api/clones/{created.json()['id']}/export", headers=engineer)
    assert exported.status_code == 200
    assert exported.json()["credential_refs"][0]["vault_uri"] == REFERENCE
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        opened = open_secret(db, org, REFERENCE)
        if not _same(opened, REPLACED):
            raise AssertionError("The owning company could not open its replaced secret.")
        detail = db.scalar(select(AuditLog.detail).where(
            AuditLog.org_id == org.id, AuditLog.action == "replaced a vault credential", AuditLog.target == REFERENCE))
        path, _digest = export(db, org)
    assert detail == "The value is sealed and was not recorded."
    assert "vault credential saved" in caplog.text
    leaks = find_leaks((SENTINEL, REPLACED, *SUITE_SECRETS), collect(
        engine, [settings.log_dir.parent], [*seen, ("log", caplog.text)]))
    Path(path).unlink(missing_ok=True)
    assert leaks == [], "secret scan: " + ", ".join(leaks)


def test_another_companys_key_cannot_open_the_secret(client, ceo):
    headers = _other_owner(client)
    own = client.put("/api/vault/secrets", headers=headers, json={
        "reference": "vault://tenant/vaultco/github-app", "value": OTHER, "scope": SCOPE})
    assert own.status_code == 200, own.text
    acme = client.put("/api/vault/secrets", headers=ceo, json={
        "reference": "vault://tenant/acme/p31-isolated", "value": SENTINEL, "scope": SCOPE})
    assert acme.status_code == 200, acme.text
    acme_list = client.get("/api/vault/secrets", headers=ceo)
    other_list = client.get("/api/vault/secrets", headers=headers)
    try:
        with SessionLocal() as db:
            acme_org = db.scalar(select(Organization).where(Organization.slug == "acme"))
            other = db.scalar(select(Organization).where(Organization.slug == "vaultco"))
            row = db.scalar(select(VaultSecret).where(VaultSecret.reference == "vault://tenant/acme/p31-isolated"))
            try:
                tenant_box(db, other.id).decrypt(row.value_enc.encode())
            except InvalidToken:
                pass
            else:
                raise AssertionError("The other company's key opened this company's secret.")
            try:
                open_secret(db, other, "vault://tenant/acme/p31-isolated")
            except VaultError as exc:
                assert exc.message == "Vault reference belongs to another company."
            else:
                raise AssertionError("The other company was allowed to open this reference.")
            if not _same(open_secret(db, acme_org, "vault://tenant/acme/p31-isolated"), SENTINEL):
                raise AssertionError("The owning company could not open its own secret.")
            removed = delete_tenant_data(db, other)
            assert removed["vault_secrets"] == 1
            db.delete(other)
            db.commit()
    finally:
        with SessionLocal() as db:
            leftover = db.scalar(select(Organization).where(Organization.slug == "vaultco"))
            if leftover is not None:
                delete_tenant_data(db, leftover)
                db.delete(leftover)
                db.commit()
    leaks = find_leaks((SENTINEL, OTHER), [
        ("response store", own.text), ("response acme list", acme_list.text), ("response other list", other_list.text),
        *database_cells(engine),
    ])
    assert leaks == [], "secret scan: " + ", ".join(leaks)


def test_an_engineer_and_a_security_lead_cannot_store(client, engineer):
    # A new company with MFA off, so this test does not depend on the demo CISO already having enrolled.
    lead = _person(client, "security_lead", "lead@vaultco.test")
    texts = []
    try:
        for headers in (engineer, lead):
            refused = client.put("/api/vault/secrets", headers=headers, json={
                "reference": REFERENCE, "value": SENTINEL, "scope": SCOPE})
            texts.append(refused.text)
            assert refused.status_code == 403
            assert "Account owner, Account admin" in refused.json()["detail"]
    finally:
        _remove_vaultco()
    leaks = find_leaks([SENTINEL], [(f"response {index}", text) for index, text in enumerate(texts)])
    assert leaks == [], "secret scan: " + ", ".join(leaks)


def test_the_scanner_finds_plaintext_in_a_database_and_not_ciphertext(tmp_path):
    path = tmp_path / "scan.db"
    local = create_engine(f"sqlite:///{path}")
    with local.begin() as conn:
        conn.execute(text("CREATE TABLE vault_secrets (reference TEXT, value_enc TEXT)"))
        conn.execute(text("INSERT INTO vault_secrets (reference, value_enc) VALUES (:ref, :enc)"),
                     {"ref": "vault://tenant/acme/app", "enc": "gAAAAAsealed"})
    assert find_leaks([PLANTED], database_cells(local)) == []
    with local.begin() as conn:
        conn.execute(text("CREATE TABLE notes (body TEXT)"))
        conn.execute(text("INSERT INTO notes (body) VALUES (:body)"), {"body": "kept " + PLANTED})
    assert find_leaks([PLANTED], database_cells(local)) == ["notes.body#0 (needle 0)"]
    local.dispose()


def _call(client, seen: list[tuple[str, str]], method: str, path: str, **kwargs):
    response = getattr(client, method)(path, **kwargs)
    seen.append((f"response {method.upper()} {path} {response.status_code}", response.text))
    return response


def _same(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def _save(slug: str) -> dict:
    document = {
        "schema_version": 1, "clone": slug, "environment": "dev", "library_version": "2026.10",
        "version": {"number": 1, "author": "Anita Rao", "reason": "vault reference only"},
        "groups": [{"id": "g_app", "type": "boundary.vpc@1", "name": "app vpc", "kind": "vpc", "environment": "dev"}],
        "access_paths": [{"id": "ap_public", "name": "public APIs", "kind": "public-api", "environment": "dev"}],
        "credential_refs": [{"id": "cr_app", "vault_uri": REFERENCE, "purpose": "test connection"}],
        "nodes": [{
            "id": "n_api", "type": "service.backend@1", "name": "api", "environment": "dev",
            "location": "g_app", "access_path": "ap_public", "credential": "cr_app",
            "properties": {"runtime": "python 3.12"}, "provenance": {"kind": "drawn", "source": "test"},
        }],
        "edges": [{"from": "n_api", "to": "g_app", "type": "contained_in"}],
    }
    return {"author": "Anita Rao", "reason": "vault reference only", "document": document}


def _other_owner(client) -> dict:
    return _person(client, "account_owner", "owner@vaultco.test")


def _person(client, account_role: str, email: str) -> dict:
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "vaultco"))
        if org is None:
            org = Organization(name="Vault Co", slug="vaultco", mfa_required=False, status="live")
            db.add(org)
            db.flush()
            ensure_main_project(db, org)
        if db.scalar(select(User.id).where(User.email == email)) is None:
            db.add(User(
                org_id=org.id, email=email, name=account_role.replace("_", " ").title(), role="analyst",
                account_role=account_role, password_hash=hash_password(PASSWORD), terms_accepted_at=org.created_at,
            ))
            db.commit()
    return login(client, email)


def _remove_vaultco() -> None:
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "vaultco"))
        if org is None:
            return
        delete_tenant_data(db, org)
        db.delete(org)
        db.commit()

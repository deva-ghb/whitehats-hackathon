"""The local Azure key fills each company's seeded primary, and nothing else."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ModelDeployment, Organization
from app.security import seal, unseal
from app.seed import apply_local_azure

ENV_KEY = "unit-test-key-not-real"
TYPED_KEY = "person-typed-key"


def _env(monkeypatch, wh_env):
    if wh_env is None:
        monkeypatch.delenv("WH_ENV", raising=False)
    else:
        monkeypatch.setenv("WH_ENV", wh_env)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", ENV_KEY)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/openai/v1")


def _slot(db, org_id, slot):
    return db.scalar(select(ModelDeployment).where(
        ModelDeployment.org_id == org_id, ModelDeployment.seed_slot == slot))


def test_unset_wh_env_does_not_seal_the_key(client, monkeypatch):
    assert client
    _env(monkeypatch, None)
    with SessionLocal() as db:
        acme = db.scalar(select(Organization).where(Organization.slug == "acme"))
        primary = _slot(db, acme.id, "primary")
        primary.secret_enc = ""
        db.commit()
        apply_local_azure(db)
        db.commit()
        db.refresh(primary)
        assert primary.secret_enc == ""


def test_each_company_gets_its_own_key_and_a_typed_key_is_kept(client, monkeypatch):
    assert client
    _env(monkeypatch, "development")
    second_id = None
    acme_id = None
    try:
        with SessionLocal() as db:
            acme = db.scalar(select(Organization).where(Organization.slug == "acme"))
            acme_id = acme.id
            _slot(db, acme.id, "primary").secret_enc = ""
            second = Organization(name="Second Co", slug="secondco")
            db.add(second)
            db.flush()
            second_id = second.id
            typed = seal(TYPED_KEY, db, second.id)
            db.add_all([
                ModelDeployment(org_id=second.id, name="whitehats · gpt-6-luna", provider="azure_openai",
                                deployment="gpt-6-luna", priority=1, seed_slot="primary", status="needs_key"),
                ModelDeployment(org_id=second.id, name="whitehats · gpt-5.6-terra", provider="azure_openai",
                                deployment="gpt-5.6-terra", priority=2, seed_slot="optional", enabled=False),
                ModelDeployment(org_id=second.id, name="whitehats · mine", provider="azure_openai",
                                deployment="gpt-6-sol", priority=3, seed_slot="",
                                endpoint="https://mine.example"),
                ModelDeployment(org_id=second.id, name="Kept", provider="azure_openai",
                                deployment="kept", priority=1, seed_slot="primary",
                                endpoint="https://kept.example", secret_enc=typed),
            ])
            db.commit()
            apply_local_azure(db)
            db.commit()

            acme_primary = _slot(db, acme.id, "primary")
            second_primary = db.scalar(select(ModelDeployment).where(
                ModelDeployment.org_id == second.id, ModelDeployment.deployment == "gpt-6-luna"))
            optional = _slot(db, second.id, "optional")
            mine = db.scalar(select(ModelDeployment).where(ModelDeployment.name == "whitehats · mine"))
            kept = db.scalar(select(ModelDeployment).where(ModelDeployment.name == "Kept"))
            assert unseal(acme_primary.secret_enc, db, acme.id) == ENV_KEY
            assert acme_primary.endpoint == "https://example.openai.azure.com"
            assert unseal(second_primary.secret_enc, db, second.id) == ENV_KEY
            assert second_primary.deployment == "gpt-6-luna"
            assert optional.deployment == "gpt-5.6-terra" and optional.secret_enc == ""
            assert optional.endpoint == "https://example.openai.azure.com"
            assert mine.endpoint == "https://mine.example" and mine.secret_enc == ""
            assert mine.deployment == "gpt-6-sol"
            assert kept.endpoint == "https://kept.example"
            assert kept.deployment == "kept"
            assert unseal(kept.secret_enc, db, second.id) == TYPED_KEY
    finally:
        with SessionLocal() as db:
            if second_id is not None:
                for row in db.scalars(select(ModelDeployment).where(ModelDeployment.org_id == second_id)):
                    db.delete(row)
                org = db.get(Organization, second_id)
                if org is not None:
                    db.delete(org)
            if acme_id is not None:
                primary = _slot(db, acme_id, "primary")
                if primary is not None:
                    primary.secret_enc = ""
                    primary.status = "needs_key"
                    primary.endpoint = "https://whitehats.openai.azure.com"
            db.commit()

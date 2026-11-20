"""The session signing key: rotating it ends every session, and a generated key file is private from the start."""

import dataclasses
import stat
import threading

from app import config, security


def test_rotating_the_signing_key_signs_everyone_out(client, ceo, monkeypatch):
    assert client.get("/api/auth/me", headers=ceo).status_code == 200
    monkeypatch.setattr(security, "settings", dataclasses.replace(security.settings, jwt_secret="rotated-" + "k" * 48))
    refused = client.get("/api/auth/me", headers=ceo)
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Invalid session. Sign in again."


def test_a_generated_key_file_is_readable_only_by_its_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    previous = config.os.umask(0o022)
    try:
        first = config._persistent_secret("jwt.key")
    finally:
        config.os.umask(previous)
    assert stat.S_IMODE((tmp_path / "jwt.key").stat().st_mode) == 0o600
    assert len(first) >= 48
    assert config._persistent_secret("jwt.key") == first


def test_an_empty_key_file_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "jwt.key").write_text("")
    first = config._persistent_secret("jwt.key")
    assert first.strip()
    assert (tmp_path / "jwt.key").read_text().strip() == first
    assert config._persistent_secret("jwt.key") == first


def test_two_starts_share_one_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    barrier = threading.Barrier(2)
    found = []

    def run():
        barrier.wait()
        found.append(config._persistent_secret("jwt.key"))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found[0] == found[1]
    assert found[0] == (tmp_path / "jwt.key").read_text().strip()

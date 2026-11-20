"""WhiteHats does not keep a long-lived cloud key in its own config, or accept one for CloudWatch."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
KEY = re.compile(r"AKIA[0-9A-Z]{16}|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY")
SKIP = {
    ".git", "node_modules", "dist", "tests", "__pycache__", ".venv",
    "screenshots", "test-results", ".playwright-mcp",
}
SUFFIXES = {".toml", ".yaml", ".yml", ".tf", ".json", ".lock", ".py", ".env", ".example", ".md"}
LOCKS = (
    "platform/backend/uv.lock",
    "factory/uv.lock",
    "library/uv.lock",
    "platform/frontend/package-lock.json",
)


# Built at run time so this file never holds a key-shaped string itself.
PLANTED = "AKIA" + ("A" * 16)


def _config_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP for part in path.relative_to(root).parts):
            continue
        if path.suffix in SUFFIXES or path.name.startswith(".env"):
            yield path


def _files_with_keys(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in _config_files(root)
                  if KEY.search(path.read_text(errors="ignore")))


@pytest.mark.parametrize("name", [f"settings{suffix}" for suffix in sorted(SUFFIXES)] + [".env", ".env.local"])
def test_the_scan_reads_every_kind_of_config_file(tmp_path, name):
    planted = tmp_path / "config" / "deploy" / name
    planted.parent.mkdir(parents=True)
    planted.write_text(f"aws_access_key_id = {PLANTED}\n")
    (tmp_path / "config" / "clean.yaml").write_text("region: ap-southeast-2\n")
    assert _files_with_keys(tmp_path) == [f"config/deploy/{name}"]


@pytest.mark.parametrize("text", [
    PLANTED,
    "key=" + "AKIA" + "Z9" * 8 + "MORE",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "-----BEGIN " + "PRIVATE KEY-----",
])
def test_key_pattern_matches_access_key_ids_and_private_keys(text):
    assert KEY.search(text)


@pytest.mark.parametrize("text", [
    "AKIA" + "A" * 15,
    "akia" + "a" * 16,
    "AKIA" + "a" * 16,
    "-----BEGIN " + "PUBLIC KEY-----",
    "-----BEGIN " + "RSA PUBLIC KEY-----",
    "-----BEGIN " + "CERTIFICATE-----",
])
def test_key_pattern_ignores_short_ids_and_public_material(text):
    assert KEY.search(text) is None


def test_the_product_config_holds_no_long_lived_key():
    assert _files_with_keys(ROOT) == []


def test_long_lived_cloud_keys_are_refused(client, ceo):
    planted = PLANTED

    rows = client.get("/api/connectors", headers=ceo).json()
    cloudwatch = next(row for row in rows if row["key"] == "cloudwatch")
    fields = {field["key"] for field in cloudwatch["fields"]}
    assert "role_arn" in fields
    assert "access_key_id" not in fields
    refused = client.put(f"/api/connectors/{cloudwatch['id']}", headers=ceo, json={"values": {
        "region": "ap-south-1",
        "log_groups": "cloudtrail",
        "role_arn": "arn:aws:iam::111122223333:role/whitehats-reader",
        "access_key_id": planted,
        "token": "secret",
    }})
    assert refused.status_code == 400
    assert "not accepted" in refused.json()["detail"]
    again = client.get("/api/connectors", headers=ceo).json()
    stored = next(row for row in again if row["key"] == "cloudwatch")
    assert stored["status"] == "available"
    assert stored["has_secret"] is False

    missing = [name for name in LOCKS if not (ROOT / name).is_file()]
    assert missing == []

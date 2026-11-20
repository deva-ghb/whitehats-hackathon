"""Secret values never reach the model; it is told the kind and the length instead (R-051)."""

from __future__ import annotations

import json
from pathlib import Path

from app.confirmation import Reply, confirm
from app.confirmation.mask import mask_text
from app.decisions_api import decide

PREFIX = "pgsk_" + "live_"  # split so this file is not itself a hit
KEY = PREFIX + "4h8k2n6q9w3e7r1t5y0u"
CLOUD_KEY = "AKIA" + "Q3EGRXK7MZ2PL5TW"


def _recording(calls: list):
    def ask(system: str, user: str) -> Reply:
        calls.append(user)
        return Reply(json.dumps({"verdict": "confirmed", "category": "secrets", "severity": "high", "confidence": "high",
                                 "explanation": "A live key is written into the file."}), "test-model", 900, 60)
    return ask


def test_a_planted_key_never_appears_in_what_is_sent_to_the_model(tmp_path: Path):
    (tmp_path / "settings.py").write_text(
        "\n".join(["import os", "", f'PROVIDER_KEY = "{KEY}"', f"AWS_KEY_ID = '{CLOUD_KEY}'", 'DEBUG = False', ""]),
        encoding="utf-8")
    decision = decide(tmp_path, scanners=())
    assert decision.escalating  # the provider key rule fired on the planted key
    calls: list = []
    (confirmation,) = confirm(decision, tmp_path, _recording(calls), facts=f"service settings, key {KEY}")
    sent = "\n".join(calls)
    assert KEY not in sent and KEY[len(PREFIX):] not in sent
    assert CLOUD_KEY not in sent
    assert "[hidden provider live key, 30 characters]" in sent
    assert "[hidden cloud access key id, 20 characters]" in sent
    assert confirmation.status == "confirmed"


def test_a_placeholder_and_the_code_around_a_secret_stay_visible():
    # A placeholder is how the model tells a fake key from a real one; reading a secret from the environment is safe code.
    for line in ['api_key: changeme', 'PROVIDER_KEY = "your-key-here"', 'token = os.environ["PROVIDER_TOKEN"]',
                 'secret = settings.SIGNING_SECRET', 'api_key = get_key()', 'def handler(event, context):',
                 'url = "https://shop.example/api/v1/orders"']:
        assert mask_text(line) == line


def test_a_value_given_to_a_secret_name_is_hidden_however_it_looks():
    assert mask_text('password="hunter2hunter2"') == 'password="[hidden secret value, 14 characters]"'
    assert mask_text("DB_PASSWORD=hunter2hunter2") == "DB_PASSWORD=[hidden secret value, 14 characters]"
    assert mask_text('"Authorization": "Bearer abcdefghijklmnop1234"') == (
        '"Authorization": "Bearer [hidden random-looking value, 20 characters]"')


def test_every_known_credential_prefix_is_hidden_with_its_kind():
    for value, kind in [("ghp_" + "a" * 4 + "1B2c3D4e5F6g7H8i9J0k", "GitHub token"), ("xoxb-" + "1234-5678-abcd", "Slack token"),
                        ("sk-" + "proj-7Hk2Lm9Qx", "API key"), (CLOUD_KEY, "cloud access key id")]:
        assert mask_text(f"value = {value}") == f"value = [hidden {kind}, {len(value)} characters]"

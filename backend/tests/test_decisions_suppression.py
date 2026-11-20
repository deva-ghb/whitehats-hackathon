"""A change cannot switch the scanners off with a comment or a file it adds (R-052).

The engine tests run the pinned images, so they need Docker; without it they are skipped. The parsing test always runs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.decisions_api.scanners import checkov, gitleaks, semgrep

STRIPE = "sk_" + "live_"  # split so this file is not itself a hit


def _docker() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=30).returncode == 0


needs_docker = pytest.mark.skipif(not _docker(), reason="the engines run in Docker")


def test_a_check_skipped_by_a_comment_is_still_a_suspect():
    payload = {"results": {
        "failed_checks": [{"check_id": "CKV_AWS_20", "file_path": "/main.tf", "file_line_range": [1, 4], "resource": "aws_s3_bucket.a"}],
        "skipped_checks": [{"check_id": "CKV_AWS_18", "file_path": "/main.tf", "file_line_range": [6, 9], "resource": "aws_s3_bucket.b",
                            "suppress_comment": "Reviewed, safe"}],
    }}
    found = {(suspect.rule_id, suspect.evidence.start_line) for suspect in checkov._suspects(payload)}
    assert found == {("CKV_AWS_20", 1), ("CKV_AWS_18", 6)}


@needs_docker
def test_gitleaks_finds_a_key_whatever_the_change_says(tmp_path: Path):
    (tmp_path / "commented.py").write_text(f'KEY = "{STRIPE}9fK2mQ7xL4pR8tZ1vB6nC3dW"  # gitleaks:allow\n', encoding="utf-8")
    (tmp_path / "ignored.py").write_text(f'KEY = "{STRIPE}4eC39HqLyjWDarjtT1zdp7dc"\n', encoding="utf-8")
    (tmp_path / "allowlisted.py").write_text(f'KEY = "{STRIPE}7hT5kW2xN9qM3vL8pD1sF6gJ"\n', encoding="utf-8")
    (tmp_path / ".gitleaksignore").write_text("/src/ignored.py:stripe-access-token:1\n", encoding="utf-8")
    (tmp_path / ".gitleaks.toml").write_text('[extend]\nuseDefault = true\n[allowlist]\npaths = ["allowlisted[.]py"]\n', encoding="utf-8")
    suspects, run = gitleaks.scan(tmp_path, None, timeout=120)
    assert run.error == ""
    assert {suspect.evidence.path for suspect in suspects} >= {"commented.py", "ignored.py", "allowlisted.py"}


@needs_docker
def test_gitleaks_still_runs_when_the_change_adds_a_broken_config(tmp_path: Path):
    (tmp_path / "settings.py").write_text(f'KEY = "{STRIPE}9fK2mQ7xL4pR8tZ1vB6nC3dW"\n', encoding="utf-8")
    (tmp_path / ".gitleaks.toml").write_text('[allowlist]\npaths = ["a\\.py"]\n', encoding="utf-8")  # not valid TOML
    suspects, run = gitleaks.scan(tmp_path, None, timeout=120)
    assert run.error == "" and [suspect.evidence.path for suspect in suspects] == ["settings.py"]


@needs_docker
def test_semgrep_finds_a_shell_call_marked_nosemgrep_or_listed_in_semgrepignore(tmp_path: Path):
    body = "import subprocess\n\n\ndef run(cmd):\n    return subprocess.call(cmd, shell=True)  # nosemgrep\n"
    (tmp_path / "commented.py").write_text(body, encoding="utf-8")
    (tmp_path / "ignored.py").write_text(body.replace("  # nosemgrep", ""), encoding="utf-8")
    (tmp_path / ".semgrepignore").write_text("ignored.py\n", encoding="utf-8")
    suspects, run = semgrep.scan(tmp_path, None, timeout=300)
    assert run.error == ""
    assert {suspect.evidence.path for suspect in suspects} >= {"commented.py", "ignored.py"}


@needs_docker
def test_checkov_finds_a_check_skipped_by_a_comment_in_the_terraform(tmp_path: Path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "receipts" {\n'
        "  #checkov:skip=CKV_AWS_18:Reviewed by the security team, safe\n"
        '  bucket = "receipts"\n'
        "}\n", encoding="utf-8")
    suspects, run = checkov.scan(tmp_path, None, timeout=300)
    assert run.error == ""
    assert "CKV_AWS_18" in {suspect.rule_id for suspect in suspects}

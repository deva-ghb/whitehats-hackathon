"""Recorded engine JSON becomes a suspect, and the secret text does not come with it."""

import ast
from pathlib import Path

from app.decisions_api.cvss import base_score
from app.decisions_api.pins import CHECKOV_IMAGE, GITLEAKS_IMAGE, OSV_IMAGE, SEMGREP_IMAGE
from app.decisions_api.scanners.checkov import _suspects as checkov_suspects
from app.decisions_api.scanners.gitleaks import _suspects as gitleaks_suspects
from app.decisions_api.scanners.osv import _payload_or_error
from app.decisions_api.scanners.osv import _suspects as osv_suspects
from app.decisions_api.scanners.semgrep import _suspects as semgrep_suspects
from app.decisions_api.suspect import severity_from_cvss

# Trimmed from a real semgrep/semgrep:1.182.1 run of p/python. The line text is not kept.
SEMGREP_FIXTURE = {
    "version": "1.182.1",
    "results": [{
        "check_id": "python.lang.security.audit.subprocess-shell-true.subprocess-shell-true",
        "path": "/src/billing/export.py",
        "start": {"line": 4, "col": 5},
        "end": {"line": 4, "col": 9},
        "extra": {
            "message": "Found subprocess with shell=True.",
            "severity": "ERROR",
            "lines": "SENTINEL_SECRET_VALUE",
            "metadata": {"cwe": ["CWE-78: OS Command Injection"]},
        },
    }],
}

GITLEAKS_FIXTURE = [{
    "RuleID": "private-key",
    "Description": "Private key",
    "File": "/src/deploy/keys.txt",
    "StartLine": 3,
    "EndLine": 3,
    "Secret": "SENTINEL_SECRET_VALUE",
    "Match": "SENTINEL_SECRET_VALUE",
}]

CHECKOV_FIXTURE = {
    "results": {
        "failed_checks": [{
            "check_id": "CKV_AWS_53",
            "check_name": "Ensure S3 bucket has block public ACL enabled",
            "file_abs_path": "/src/modules/storage/main.tf",
            "file_line_range": [10, 16],
            "resource": "aws_s3_bucket_public_access_block.media",
            "code_block": [["10", "SENTINEL_SECRET_VALUE"]],
        }, {
            "check_id": "CKV_AWS_999999",
            "check_name": "Unlisted check",
            "file_path": "/modules/storage/main.tf",
            "file_line_range": [2, 2],
            "resource": "aws_s3_bucket.media",
        }],
    },
}


def test_semgrep_normalises_severity_cwe_and_span_and_drops_the_line():
    suspect = semgrep_suspects(SEMGREP_FIXTURE)[0]
    assert suspect.engine == "semgrep"
    assert suspect.rule_id.endswith("subprocess-shell-true")
    assert suspect.evidence.path == "billing/export.py"
    assert (suspect.evidence.start_line, suspect.evidence.end_line) == (4, 4)
    assert suspect.severity == "high"
    assert suspect.score == 70
    assert suspect.cwe == ["CWE-78"]
    assert "SENTINEL_SECRET_VALUE" not in str(suspect.to_dict())


def test_gitleaks_reports_the_span_and_not_the_secret():
    suspect = gitleaks_suspects(GITLEAKS_FIXTURE)[0]
    assert suspect.rule_id == "private-key"
    assert suspect.evidence.path == "deploy/keys.txt"
    assert suspect.evidence.start_line == 3
    assert suspect.cwe == ["CWE-798"]
    assert suspect.severity == "high"
    assert "SENTINEL_SECRET_VALUE" not in str(suspect.to_dict())


def test_checkov_uses_the_policy_table_and_keeps_the_resource_address():
    suspects = checkov_suspects(CHECKOV_FIXTURE)
    known, unknown = suspects
    assert known.rule_id == "CKV_AWS_53"
    assert known.severity == "medium"
    assert known.score == 40
    assert known.cwe == ["CWE-284"]
    assert known.evidence.resource == "aws_s3_bucket_public_access_block.media"
    assert known.evidence.path == "modules/storage/main.tf"
    assert unknown.severity == "medium"
    assert "SENTINEL_SECRET_VALUE" not in str(known.to_dict())


def test_osv_dedupes_aliases_and_scores_the_cvss_vector(tmp_path: Path):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("[project]\ndependencies = [\"widgets==1.2.3\"]\n", encoding="utf-8")
    vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    payload = {"results": [{"source": {"path": "pyproject.toml", "type": "lockfile"}, "packages": [{
        "package": {"name": "widgets", "version": "1.2.3", "ecosystem": "PyPI"},
        "vulnerabilities": [
            {"id": "PYSEC-2099-1", "aliases": ["CVE-2099-0001"]},
            {
                "id": "GHSA-xxxx-xxxx-xxxx",
                "aliases": ["CVE-2099-0001"],
                "summary": "Example advisory",
                "database_specific": {"cwe_ids": ["CWE-20"], "severity": "CRITICAL"},
                "severity": [{"type": "CVSS_V3", "score": vector}],
            },
        ],
    }]}]}
    suspects = osv_suspects(payload, tmp_path, {})
    assert len(suspects) == 1
    assert suspects[0].rule_id == "CVE-2099-0001"
    assert suspects[0].severity == "critical"
    assert suspects[0].score == 90
    assert suspects[0].cwe == ["CWE-20"]
    assert suspects[0].evidence.path == "pyproject.toml"
    assert suspects[0].evidence.start_line == 2
    assert base_score(vector) == 9.8
    assert severity_from_cvss(9.8) == "critical"


def test_an_empty_lockfile_tree_is_clean_and_a_broken_engine_is_not():
    payload, error = _payload_or_error("", "No package sources found, --help for usage information.\n", 128)
    assert payload == {"results": []}
    assert error == ""
    payload, error = _payload_or_error("", "connection refused", 1)
    assert payload is None
    assert error == "no JSON (exit 1)"


def test_the_doc_names_every_pinned_image():
    text = Path(__file__).resolve().parents[2].joinpath("docs/developers/decisions.md").read_text(encoding="utf-8")
    for image in (SEMGREP_IMAGE, GITLEAKS_IMAGE, OSV_IMAGE, CHECKOV_IMAGE):
        assert image in text


def test_the_package_does_not_call_a_model():
    root = Path(__file__).resolve().parents[1] / "app" / "decisions_api"
    banned = ("app.services.llm", "openai", "anthropic", "api.openai.com", "chat/completions")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, path.name
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.services.llm")

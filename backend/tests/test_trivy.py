"""VS.5c: what Trivy reports for an image becomes findings with the image digest, the package and the fixed version."""

from __future__ import annotations

from pathlib import Path

from app.decisions_api.scanners import trivy

REPORT = {
    "Metadata": {"RepoDigests": ["public.ecr.aws/lambda/python@sha256:ed423f4168b793088d341d8bc1c5ae0ca26a56d04adfd2f3254aca8445aa34c4"]},
    "Results": [
        {"Target": "public.ecr.aws/lambda/python:3.9 (amazon 2)", "Type": "amazon", "Vulnerabilities": [
            {"VulnerabilityID": "CVE-2023-0001", "PkgName": "openssl-libs", "InstalledVersion": "1.0.2k-24", "FixedVersion": "1.0.2k-26",
             "Severity": "HIGH", "Title": "openssl: a flaw", "CweIDs": ["CWE-476"],
             "CVSS": {"nvd": {"V3Vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}}},
            {"VulnerabilityID": "CVE-2023-0002", "PkgName": "glibc", "InstalledVersion": "2.26-62", "FixedVersion": "",
             "Severity": "MEDIUM", "Title": "glibc: no fix yet"},
        ]},
        {"Target": "usr/local/bin/aws-lambda-rie", "Type": "gobinary", "Vulnerabilities": [
            {"VulnerabilityID": "CVE-2024-0003", "PkgName": "stdlib", "InstalledVersion": "v1.21.0", "FixedVersion": "1.21.9, 1.22.2",
             "Severity": "MEDIUM", "Title": "net/http: a flaw"},
        ]},
    ],
}


def test_only_fixable_weaknesses_are_reported_with_digest_package_and_fix_and_the_rest_are_counted():
    found, unfixed = trivy.weaknesses(REPORT, "public.ecr.aws/lambda/python:3.9")
    assert unfixed == 1
    assert [(item.advisory, item.package, item.installed, item.fixed, item.severity) for item in found] == [
        ("CVE-2023-0001", "openssl-libs", "1.0.2k-24", "1.0.2k-26", "critical"),  # the CVSS score, 9.8, decides
        ("CVE-2024-0003", "stdlib", "v1.21.0", "1.21.9, 1.22.2", "medium"),
    ]
    assert found[0].digest == "sha256:ed423f4168b793088d341d8bc1c5ae0ca26a56d04adfd2f3254aca8445aa34c4"
    assert found[0].cwe == ("CWE-476",)


def test_a_change_without_a_dockerfile_does_not_start_trivy(tmp_path: Path):
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    suspects, run = trivy.scan(tmp_path, ["app.py"], timeout=10)
    assert suspects == [] and run.error == "" and run.seconds == 0.0


def test_the_from_line_of_the_last_stage_is_where_the_finding_points():
    text = "FROM python:3.12 AS build\nRUN make\nFROM public.ecr.aws/lambda/python:3.9\nCOPY . .\n"
    assert trivy._from_line(text, "public.ecr.aws/lambda/python:3.9") == 3


def test_a_saved_image_without_its_registry_digest_takes_the_one_docker_pulled():
    report = {**REPORT, "Metadata": {}}
    found, _ = trivy.weaknesses(report, "public.ecr.aws/lambda/python:3.9", "sha256:abc")
    assert {item.digest for item in found} == {"sha256:abc"}


def test_one_base_image_is_one_suspect_with_its_worst_severity_and_the_worst_named_first():
    found, _ = trivy.weaknesses(REPORT, "public.ecr.aws/lambda/python:3.9")
    suspect = trivy._image_suspect("worker/Dockerfile", 4, "public.ecr.aws/lambda/python:3.9", found)
    assert (suspect.rule_id, suspect.severity, suspect.evidence.start_line) == ("trivy.base-image-weaknesses", "critical", 4)
    assert suspect.evidence.resource == "public.ecr.aws/lambda/python:3.9@sha256:ed423f4168b793088d341d8bc1c5ae0ca26a56d04adfd2f3254aca8445aa34c4"
    assert suspect.title.startswith("2 known weaknesses with a fix in the base image public.ecr.aws/lambda/python:3.9 (1 critical, 1 medium)")
    assert suspect.note.startswith("Worst first: CVE-2023-0001 openssl-libs 1.0.2k-24, fixed in 1.0.2k-26; CVE-2024-0003")


def test_a_private_pull_keeps_its_token_in_a_settings_folder_of_its_own_and_never_logs_in(tmp_path, monkeypatch):
    import base64
    import json
    import subprocess

    seen = []

    def run(command, **kwargs):
        env = kwargs.get("env") or {}
        settings = Path(env.get("DOCKER_CONFIG", "")) / "config.json"
        seen.append((command[:2], json.loads(settings.read_text()) if settings.is_file() else None))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(trivy.subprocess, "run", run)
    monkeypatch.setenv("DOCKER_HOST", "unix:///engine.sock")
    problem, _digest = trivy._pulled("registry.example/app@sha256:" + "a" * 64, "linux/arm64", 30, tmp_path,
                                     ("registry.example", "AWS", "pull-token"))
    assert problem == ""
    assert [command for command, _settings in seen] == [["docker", "pull"], ["docker", "save"], ["docker", "image"]]
    token = base64.b64encode(b"AWS:pull-token").decode()
    assert all(settings == {"auths": {"registry.example": {"auth": token}}} for _command, settings in seen)
    assert not list(tmp_path.iterdir())  # nothing of the token in the folder Trivy reads

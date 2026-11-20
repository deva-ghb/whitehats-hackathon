"""Severity and weakness class for Checkov checks.

Open-source Checkov prints no severity. These come from the Palo Alto policy
pages (Prisma Cloud and Cortex AppSec, where APPSEC_AWS_n is CKV_AWS_n), read on
2026-10-24. A check that is not listed counts as medium, so an unknown check is
escalated rather than hidden.
"""

from __future__ import annotations

UNLISTED_SEVERITY = "medium"

# id: (severity, CWE ids)
POLICIES: dict[str, tuple[str, tuple[str, ...]]] = {
    # Storage open to the public
    "CKV_AWS_53": ("medium", ("CWE-284",)),
    "CKV_AWS_54": ("medium", ("CWE-284",)),
    "CKV_AWS_55": ("medium", ("CWE-284",)),
    "CKV_AWS_56": ("medium", ("CWE-284",)),
    "CKV_AWS_70": ("medium", ("CWE-284",)),
    # Identity policies wider than the job
    "CKV_AWS_109": ("low", ("CWE-269",)),
    "CKV_AWS_111": ("low", ("CWE-269",)),
    "CKV_AWS_356": ("high", ("CWE-269",)),
    # Integrity and keys
    "CKV_AWS_272": ("high", ("CWE-494",)),
    "CKV_AWS_337": ("high", ("CWE-311",)),
    # Hardening the estate leaves at low on purpose (each reason is in the PG.1.3 journal entry)
    "CKV_AWS_50": ("low", ("CWE-778",)),
    "CKV_AWS_115": ("low", ("CWE-770",)),
    "CKV_AWS_116": ("low", ("CWE-755",)),
    "CKV_AWS_117": ("low", ("CWE-284",)),
    "CKV_AWS_173": ("low", ("CWE-311",)),
    "CKV_AWS_158": ("low", ("CWE-311",)),
    "CKV_AWS_338": ("low", ("CWE-778",)),
    "CKV_AWS_35": ("low", ("CWE-311",)),
    "CKV_AWS_67": ("low", ("CWE-778",)),
    "CKV_AWS_252": ("low", ("CWE-778",)),
    "CKV2_AWS_10": ("low", ("CWE-778",)),
    "CKV_AWS_76": ("low", ("CWE-778",)),
    "CKV_AWS_136": ("low", ("CWE-311",)),
    "CKV_AWS_8": ("low", ("CWE-311",)),
    "CKV_AWS_135": ("low", ("CWE-400",)),
    "CKV_AWS_23": ("low", ("CWE-1053",)),
    "CKV_AWS_382": ("low", ("CWE-284",)),
    "CKV2_AWS_12": ("low", ("CWE-284",)),
    "CKV2_AWS_41": ("low", ("CWE-284",)),
    # No published severity: a missing container health check is a reliability gap, not an exposure.
    "CKV_DOCKER_2": ("low", ("CWE-754",)),
}


def policy(check_id: str) -> tuple[str, list[str]]:
    severity, cwe = POLICIES.get(check_id, (UNLISTED_SEVERITY, ()))
    return severity, list(cwe)

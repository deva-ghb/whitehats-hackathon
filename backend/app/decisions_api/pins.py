"""Pinned engine images. The tag is the version we run; docs/developers/decisions.md repeats it."""

from __future__ import annotations

SEMGREP_IMAGE = "semgrep/semgrep:1.182.1"
GITLEAKS_IMAGE = "ghcr.io/gitleaks/gitleaks:v8.30.1"
OSV_IMAGE = "ghcr.io/google/osv-scanner:v2.7.0"
CHECKOV_IMAGE = "bridgecrew/checkov:3.3.30"
TRIVY_IMAGE = "aquasec/trivy:0.57.1"

# Public packs shipped for this Semgrep version: Python and secrets, nothing else.
SEMGREP_CONFIGS = ("p/python", "p/secrets")

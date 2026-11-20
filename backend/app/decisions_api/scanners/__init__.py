"""The maintained engines, each in its own pinned container."""

from __future__ import annotations

from .checkov import scan as scan_checkov
from .gitleaks import scan as scan_gitleaks
from .osv import scan as scan_osv
from .semgrep import scan as scan_semgrep
from .trivy import scan as scan_trivy

SCANNERS = (scan_semgrep, scan_gitleaks, scan_osv, scan_checkov, scan_trivy)

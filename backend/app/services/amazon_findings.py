"""Amazon's own security findings, where the customer already has them switched on (AMZ.1).

GuardDuty watches the account for threats (a stolen key used from abroad, a server talking to a known bad address);
Security Hub gathers findings from Amazon's other checkers (Inspector, Macie...). Both cost the customer money after a
30-day trial, so WhiteHats never asks for them: it reads them when they are on, and says when they are off (the Studio's
recommendations). Only high and critical findings that are still open are read.

Security Hub's own setting checks and the GuardDuty findings it copies are skipped: Prowler checks the settings, and
GuardDuty is read directly, so nothing is raised twice.

Each finding has the shape of a Prowler finding, so cloud_settings opens, refreshes and closes it like one. Its check
name is "guardduty_..." or "securityhub_...", never one of Prowler's.
"""

from __future__ import annotations

from typing import Any

from ..connectors.base import SourceError

MOST = 200  # findings read from each service in one pass
SKIPPED_PRODUCTS = ("GuardDuty", "Security Hub")


def _gd_severity(score: float) -> str:
    return "Critical" if score >= 9 else "High" if score >= 7 else ""


def _gd_resource(finding: dict) -> str:
    res = finding.get("Resource") or {}
    if (res.get("InstanceDetails") or {}).get("InstanceId"):
        return res["InstanceDetails"]["InstanceId"]
    if (res.get("AccessKeyDetails") or {}).get("UserName"):
        return res["AccessKeyDetails"]["UserName"]
    for bucket in res.get("S3BucketDetails") or []:
        if bucket.get("Name"):
            return bucket["Name"]
    return res.get("ResourceType") or ""


def _shape(check: str, account: str, region: str, uid: str, severity: str, message: str, remedy: str,
           resource: str) -> dict:
    return {"status_code": "FAIL", "severity": severity,
            "finding_info": {"uid": f"prowler-aws-{check}-{account}-{region}-{uid}", "title": message},
            "message": message, "remediation": {"desc": remedy},
            "resources": [{"uid": resource, "data": {"metadata": {"name": resource}}}]}


def guardduty(source: Any, region: str, account: str) -> tuple[str, list[dict]]:
    """("on" | "off", findings): GuardDuty's open high and critical findings in this region."""
    detectors = source._call("guardduty", "list_detectors", region=region).get("DetectorIds") or []
    if not detectors:
        return "off", []
    out = []
    for detector in detectors:
        ids = source._call("guardduty", "list_findings", region=region, DetectorId=detector, MaxResults=50,
                           FindingCriteria={"Criterion": {"severity": {"Gte": 7},
                                                          "service.archived": {"Eq": ["false"]}}}).get("FindingIds") or []
        for start in range(0, min(len(ids), MOST), 50):
            got = source._call("guardduty", "get_findings", region=region, DetectorId=detector,
                               FindingIds=ids[start:start + 50]).get("Findings") or []
            for f in got:
                severity = _gd_severity(float(f.get("Severity") or 0))
                if not severity:
                    continue
                kind = str(f.get("Type") or "finding").split(":", 1)[0].replace("/", ".").lower()
                out.append(_shape(f"guardduty_{kind.replace('-', '').replace('.', '_')}", account, region,
                                  str(f.get("Id")), severity, f"GuardDuty: {f.get('Title') or f.get('Type')}",
                                  str(f.get("Description") or ""), _gd_resource(f)))
    return "on", out


def securityhub(source: Any, region: str, account: str) -> tuple[str, list[dict]]:
    """("on" | "off", findings): open high and critical findings Security Hub gathered from Amazon's other checkers."""
    try:
        source._call("securityhub", "describe_hub", region=region)
    except SourceError as exc:
        if "not subscribed" in str(exc).lower() or "InvalidAccessException" in str(exc):
            return "off", []
        raise
    filters = {"SeverityLabel": [{"Value": v, "Comparison": "EQUALS"} for v in ("HIGH", "CRITICAL")],
               "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
               "WorkflowStatus": [{"Value": v, "Comparison": "EQUALS"} for v in ("NEW", "NOTIFIED")],
               "ProductName": [{"Value": p, "Comparison": "NOT_EQUALS"} for p in SKIPPED_PRODUCTS]}
    out, token = [], None
    while len(out) < MOST:
        page = source._call("securityhub", "get_findings", region=region, Filters=filters, MaxResults=100,
                            **({"NextToken": token} if token else {}))
        for f in page.get("Findings") or []:
            product = str(f.get("ProductName") or "other").lower().replace(" ", "")
            label = str((f.get("Severity") or {}).get("Label") or "HIGH").title()
            resource = next((r.get("Id") for r in f.get("Resources") or [] if r.get("Id")), "")
            remedy = ((f.get("Remediation") or {}).get("Recommendation") or {}).get("Text") or ""
            uid = str(f.get("Id") or "").replace("-", "")[-120:]
            out.append(_shape(f"securityhub_{product}", account, region, uid, label,
                              f"{f.get('ProductName', 'Security Hub')}: {f.get('Title', 'finding')}", remedy, resource))
        token = page.get("NextToken")
        if not token:
            break
    return "on", out[:MOST]


def read(source: Any, region: str) -> tuple[dict, list[dict]]:
    """Which of the two are on ("on", "off" or "unreadable"), and their findings. Never fails the settings check: a
    role that cannot read them, or a service that answers badly, is noted and the rest goes on."""
    account = source._session().split(":")[4]
    state, found = {}, []
    for name, reader in (("guardduty", guardduty), ("securityhub", securityhub)):
        try:
            state[name], more = reader(source, region, account)
            found += more
        except SourceError as exc:
            state[name] = "unreadable"
            state[f"{name}_error"] = str(exc)[:200]
    return state, found

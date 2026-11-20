"""AMZ.1: GuardDuty's and Security Hub's open high and critical findings, read where the customer has them on."""

from app.connectors.base import SourceError
from app.services import amazon_findings, cloud_settings


class _Account:
    def __init__(self, guardduty=True, hub=True, hub_error=""):
        self.guardduty, self.hub, self.hub_error, self.calls = guardduty, hub, hub_error, []

    def _session(self):
        return "arn:aws:sts::123:assumed-role/whitehats-readonly/whitehats-reader"

    def _call(self, service, method, *, region="", **kwargs):
        self.calls.append((service, method))
        if method == "list_detectors":
            return {"DetectorIds": ["d1"] if self.guardduty else []}
        if method == "list_findings":
            assert kwargs["FindingCriteria"]["Criterion"]["severity"] == {"Gte": 7}
            return {"FindingIds": ["f1", "f2"]}
        if (service, method) == ("guardduty", "get_findings"):
            return {"Findings": [
                {"Id": "f1", "Type": "UnauthorizedAccess:IAMUser/MaliciousIPCaller", "Severity": 8,
                 "Title": "A key was used from a known bad address", "Description": "Rotate the key.",
                 "Resource": {"AccessKeyDetails": {"UserName": "dev"}}},
                {"Id": "f2", "Type": "Recon:EC2/Portscan", "Severity": 5, "Title": "low"}]}
        if method == "describe_hub":
            if self.hub_error:
                raise SourceError(self.hub_error)
            return {}
        assert kwargs["Filters"]["ProductName"] == [{"Value": "GuardDuty", "Comparison": "NOT_EQUALS"},
                                                    {"Value": "Security Hub", "Comparison": "NOT_EQUALS"}]
        return {"Findings": [{"Id": "arn:aws:inspector2:x:123:finding/abc-1", "ProductName": "Inspector",
                              "Title": "CVE in the worker image", "Severity": {"Label": "CRITICAL"},
                              "Resources": [{"Id": "arn:aws:ecr:x:123:repository/worker"}],
                              "Remediation": {"Recommendation": {"Text": "Update the image."}}}]}


def test_guardduty_and_security_hub_findings_are_read_as_settings_findings():
    state, found = amazon_findings.read(_Account(), "ap-southeast-2")
    assert state == {"guardduty": "on", "securityhub": "on"}
    assert [(cloud_settings.check_of(f), f["severity"], cloud_settings._title(f), cloud_settings._resource(f))
            for f in found] == [
        ("guardduty_unauthorizedaccess", "High", "GuardDuty: A key was used from a known bad address", "dev"),
        ("securityhub_inspector", "Critical", "Inspector: CVE in the worker image", "arn:aws:ecr:x:123:repository/worker")]


def test_a_service_switched_off_is_said_and_one_that_cannot_be_read_is_unknown():
    state, found = amazon_findings.read(_Account(guardduty=False, hub_error="InvalidAccessException: not subscribed"),
                                        "ap-southeast-2")
    assert (state, found) == ({"guardduty": "off", "securityhub": "off"}, [])
    state, _found = amazon_findings.read(_Account(hub_error="AccessDenied: securityhub:DescribeHub"), "ap-southeast-2")
    assert state["securityhub"] == "unreadable" and state["guardduty"] == "on"

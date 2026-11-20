"""What the customer could switch on so WhiteHats sees more (REC.1), shown in the Clone Studio.

WhiteHats only reads: it never switches anything on in the customer's account. Where something that would let it see
more is off, it says so here, with why it matters, how to do it and what it costs, so the customer decides.

- A network whose traffic log is off, or goes to a storage bucket (whose files WhiteHats never reads).
- GuardDuty or Security Hub switched off: optional, paid after a 30-day trial.
- A read-only role made from an older template, which cannot read some settings.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors import routing
from ..models import Connector, SettingsCheck

LOG_COST = "AWS charges for delivering and storing the log, about US$0.50 for each GB of traffic recorded."
ROLE_TEMPLATE = "docs/connectors/aws-readonly-role.yaml"


def _network(f: dict) -> dict | None:
    name, vpc, region = f.get("name") or f["vpc"], f["vpc"], f.get("region", "")
    how = (f"In the AWS console, open VPC, choose {vpc} in {region}, then Flow logs, Create flow log: filter All, "
           "destination \"Send to CloudWatch Logs\", a new log group, and a role that may write to it.")
    if f.get("destination") == "none":
        return {"id": f"flow-off-{vpc}", "kind": "network", "resource": vpc, "severity": "high",
                "title": f"Switch on traffic logging for network {name}",
                "why": "WhiteHats cannot see what moves in or out of this network: a server calling an unknown "
                       "address, or data leaving, goes unseen.",
                "how": how, "cost": LOG_COST}
    if f.get("destination") == "s3":
        return {"id": f"flow-bucket-{vpc}", "kind": "network", "resource": vpc, "severity": "medium",
                "title": f"Also send network {name}'s traffic log to CloudWatch Logs",
                "why": "Its traffic log goes to a storage bucket, and WhiteHats never reads files in buckets, so the "
                       "log is kept but not watched.",
                "how": how + " The bucket log can stay as it is.", "cost": LOG_COST}
    return None


def _amazon(state: dict) -> list[dict]:
    out = []
    if state.get("guardduty") == "off":
        out.append({"id": "guardduty-off", "kind": "service", "resource": "", "severity": "low",
                    "title": "Consider switching on Amazon GuardDuty",
                    "why": "GuardDuty spots threats WhiteHats cannot, such as a stolen key used from a known bad "
                           "address. WhiteHats reads its findings automatically once it is on.",
                    "how": "In the AWS console, open GuardDuty and choose Get started, in each region you use.",
                    "cost": "Free for 30 days in each region, then charged by how much activity it analyses."})
    if state.get("securityhub") == "off":
        out.append({"id": "securityhub-off", "kind": "service", "resource": "", "severity": "low",
                    "title": "Consider switching on AWS Security Hub",
                    "why": "It gathers findings from Amazon's other checkers (Inspector, Macie). WhiteHats reads the "
                           "high and critical ones automatically once it is on.",
                    "how": "In the AWS console, open Security Hub and choose Go to Security Hub, then Enable.",
                    "cost": "Free for 30 days, then charged per check; 10,000 findings a month are always free."})
    for name in ("guardduty", "securityhub"):
        if state.get(name) == "unreadable":
            out.append(_role(f"The read-only role cannot read {'GuardDuty' if name == 'guardduty' else 'Security Hub'}."))
    return out


def _role(why: str) -> dict:
    return {"id": "role-update", "kind": "access", "resource": "", "severity": "medium",
            "title": "Update the WhiteHats read-only role",
            "why": f"{why} Some settings go unchecked until the role is updated.",
            "how": f"Update the role's CloudFormation stack with the latest WhiteHats template ({ROLE_TEMPLATE}). It "
                   "still reads settings only, never data.",
            "cost": "None."}


def for_project(db: Session, org_id: int, project_id: int) -> list[dict]:
    """Recommendations for the AWS accounts this project's map is drawn from, most important first."""
    out: list[dict] = []
    for c in db.scalars(select(Connector).where(Connector.org_id == org_id, Connector.key == "aws")):
        if project_id not in routing.projects_for(db, c, routing.connection_resource(c)):
            continue
        state = c.state or {}
        out += [r for r in (_network(f) for f in state.get("flow_logs") or []) if r]
        if state.get("flow_logs_error"):
            out.append(_role("The read-only role cannot see where each network's traffic log goes."))
        check = db.scalar(select(SettingsCheck).where(SettingsCheck.connector_id == c.id))
        status = (check.status if check else None) or {}
        out += _amazon(status.get("amazon") or {})
        if status.get("status") == "limited":
            out.append(_role("The read-only role was refused some settings checks."))
    rank = {"high": 0, "medium": 1, "low": 2}
    unique = {r["id"]: r for r in out}
    return sorted(unique.values(), key=lambda r: rank[r["severity"]])

"""Outgoing firewall rules (NET.1): what Prowler does not check.

Prowler checks every way a security group lets the internet in. It has no check for the other direction: a group that
lets whatever it protects reach any address, on any port. That is AWS's default for a new group, and it is how stolen
data leaves and how a compromised server calls home. So a group in use (attached to at least one network interface)
whose outgoing rules allow every protocol to 0.0.0.0/0 or ::/0 is raised here, as a medium finding.

The finding has the same shape as a Prowler finding (its JSON, OCSF), so it opens, refreshes, closes and is checked
again exactly like one (cloud_settings.apply); its check name is WhiteHats' own, so it is never mistaken for Prowler's.
Reads only: security groups and network interfaces, through the connector's guard.
"""

from __future__ import annotations

from typing import Any

CHECK = "ec2_securitygroup_egress_open_to_internet"  # WhiteHats' own check, named in Prowler's style
ANYWHERE = ("0.0.0.0/0", "::/0")
REMEDY = ("Limit the group's outgoing rules to the addresses and ports its resources need, such as the database's "
          "group on its port and HTTPS (443) to the services they call. Remove the rule that allows all traffic to "
          "0.0.0.0/0 and ::/0.")


def _pages(source: Any, method: str, key: str, region: str, **kwargs: Any) -> list[dict]:
    out, token = [], None
    while True:
        answer = source._call("ec2", method, region=region, **kwargs, **({"NextToken": token} if token else {}))
        out += answer.get(key) or []
        token = answer.get("NextToken")
        if not token:
            return out


def open_to_anywhere(group: dict) -> str:
    """The address range a group's outgoing rules open every protocol to ("" when none)."""
    for rule in group.get("IpPermissionsEgress") or []:
        if str(rule.get("IpProtocol")) != "-1":
            continue
        for cidr in [r.get("CidrIp") for r in rule.get("IpRanges") or []] + [
                r.get("CidrIpv6") for r in rule.get("Ipv6Ranges") or []]:
            if cidr in ANYWHERE:
                return cidr
    return ""


def findings(source: Any, region: str, tag: tuple[str, str] | None = None) -> list[dict]:
    """A Prowler-shaped finding for each security group in use whose outgoing rules allow everything to anywhere."""
    account = source._session().split(":")[4]
    filters = [{"Name": f"tag:{tag[0]}", "Values": [tag[1]]}] if tag else []
    groups = _pages(source, "describe_security_groups", "SecurityGroups", region,
                    **({"Filters": filters} if filters else {}))
    used = {g["GroupId"] for nic in _pages(source, "describe_network_interfaces", "NetworkInterfaces", region)
            for g in nic.get("Groups") or []}
    out = []
    for group in groups:
        gid = group["GroupId"]
        cidr = open_to_anywhere(group)
        if gid not in used or not cidr:
            continue
        arn = f"arn:aws:ec2:{region}:{account}:security-group/{gid}"
        name = group.get("GroupName") or gid
        out.append({
            "status_code": "FAIL", "severity": "Medium",
            "finding_info": {"uid": f"prowler-aws-{CHECK}-{account}-{region}-{gid}",
                             "title": "Security group lets what it protects reach any address"},
            "message": (f"Security group {name} ({gid}) lets everything it protects reach any address on the "
                        f"internet ({cidr}, all ports)"),
            "remediation": {"desc": REMEDY},
            "resources": [{"uid": arn, "data": {"metadata": {"arn": arn, "name": name}}}],
        })
    return out

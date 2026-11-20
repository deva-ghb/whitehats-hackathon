"""Signal titles the shipped WhiteHats raise.

The detectors and the Fix Factory both read this file. It imports nothing
from the rest of the app, so the factory can load it on its own.
"""

from __future__ import annotations

import re

SHIPPED = ("Identity", "Endpoint", "Network", "Cloud Posture", "Insider Threat")
PLANNED = (
    "Application Security",
    "Software Supply Chain",
    "Database",
    "Deployment",
    "Configuration",
    "Communications",
    "Threat Intelligence",
    "Physical / OT",
)

IDENTITY_BURST = 5
LOG_SHAPE_PREFIX = "Log shape needs review: "
ENDPOINT_SUSPICIOUS = {
    "xmrig": "crypto-mining software",
    "minerd": "crypto-mining software",
    "nc": "netcat (raw network tool)",
    "ncat": "netcat (raw network tool)",
    "socat": "socat (network relay tool)",
    "mimikatz": "credential theft tool",
    "chisel": "tunnelling tool",
    "ngrok": "public tunnel to this machine",
}
# Ports the network analyzer treats as high when they listen on every interface.
# The Network WhiteHat forwards that title and no lower one.
NETWORK_RISKY_PORTS = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    445: "SMB",
    1433: "SQL Server",
    2375: "Docker API (unencrypted)",
    3306: "MySQL",
    3389: "Remote Desktop",
    5432: "PostgreSQL",
    5900: "VNC",
    6379: "Redis",
    9200: "Elasticsearch",
    11211: "Memcached",
    27017: "MongoDB",
}
CLOUD_RISKY = {
    "StopLogging": ("critical", "Cloud audit logging was switched off", "T1685.002"),
    "DeleteTrail": ("critical", "A cloud audit trail was deleted", "T1685.002"),
    "PutBucketPolicy": ("medium", "A storage bucket policy changed", "T1530"),
    "PutBucketPublicAccessBlock": ("medium", "Public access settings on a bucket changed", "T1530"),
    "DeleteBucketPublicAccessBlock": ("high", "Public access settings on a bucket changed", "T1530"),
    "CreateAccessKey": ("medium", "A new access key was created", "T1098.001"),
    "AttachUserPolicy": ("medium", "A policy was attached directly to a user", "T1098"),
    "DisableKey": ("high", "An encryption key was disabled", "T1485"),
    "ScheduleKeyDeletion": ("critical", "An encryption key was scheduled for deletion", "T1485"),
    "PutKeyPolicy": ("medium", "Who may use an encryption key changed", "T1098"),
    "CreateUser": ("medium", "A new cloud user was created", "T1136.003"),
    "CreateLoginProfile": ("medium", "A cloud user was given a console password", "T1098"),
    "PutUserPolicy": ("medium", "A policy was written directly on a user", "T1098"),
    "AttachRolePolicy": ("medium", "A policy was attached to a role", "T1098"),
    "UpdateAssumeRolePolicy": ("high", "Who may take on a role was changed", "T1098"),
    "PutBucketAcl": ("medium", "A storage bucket's access list changed", "T1530"),
    "DeleteBucketEncryption": ("high", "Encryption was switched off on a storage bucket", "T1530"),
    "DeleteFlowLogs": ("high", "Network flow logging was switched off", "T1685.002"),
    "StopConfigurationRecorder": ("high", "AWS Config recording was stopped", "T1685.002"),
    "DeleteDetector": ("critical", "GuardDuty threat detection was switched off", "T1685"),
}
# Granting full administrator access is worse than any other policy (CloudPosture raises it as its own signal).
ADMIN_GRANTED = "Full administrator access was granted"
INTERNET_OPEN = "A firewall rule now allows traffic from the whole internet"
# Changes that matter for security: an AWS change of one of these kinds is worth a focused audit (AUD.5). Housekeeping
# (parameters written, snapshots, tags, key grants) is not, however often it happens.
SECURITY_CHANGES = frozenset({*CLOUD_RISKY, "AuthorizeSecurityGroupIngress", "ModifyDBInstance",
                              "ModifySnapshotAttribute", "ModifyImageAttribute", "PutBucketCors", "PutBucketWebsite"})
CLOUD_EXACT = {
    "Root account signed in to the cloud console",
    "A firewall rule now allows traffic from the whole internet",
    ADMIN_GRANTED,
    *(title for _sev, title, _mitre in CLOUD_RISKY.values()),
}

# For human review. Must match a title this WhiteHat raises. Must not match a longer sentence.
_ROOT_USED = re.compile(r"Root account used for [A-Z][A-Za-z0-9]*")
_FAILED_BATCH = re.compile(r"(\d+) failed sign-ins in one batch on .+")
_OFF_HOURS = re.compile(r"(\d+) sign-in\(s\) outside working hours on .+")


def signal_whitehat(title: str) -> str | None:
    """Name of the shipped WhiteHat that raises this exact title, or None."""
    if title in CLOUD_EXACT:
        return "Cloud Posture"
    if _ROOT_USED.fullmatch(title):
        return "Cloud Posture"
    if title.startswith("Failed cloud console sign-in by ") and title != "Failed cloud console sign-in by ":
        return "Cloud Posture"
    burst = _FAILED_BATCH.fullmatch(title)
    if burst and int(burst.group(1)) >= IDENTITY_BURST:
        return "Identity"
    if title.startswith(LOG_SHAPE_PREFIX):
        pattern = title[len(LOG_SHAPE_PREFIX):]
        if pattern and len(pattern) <= 120:
            return "Endpoint"
    for name in ENDPOINT_SUSPICIOUS:
        if re.fullmatch(rf"{re.escape(name)} running on .+", title, re.IGNORECASE):
            return "Endpoint"
    for _port, service in NETWORK_RISKY_PORTS.items():
        if title == f"{service} on port {_port} accepts connections from any network":
            return "Network"
    off = _OFF_HOURS.fullmatch(title)
    if off and int(off.group(1)) >= 1:
        return "Insider Threat"
    return None


def names_in(text: str) -> list[str]:
    """Catalogue names used as '<name> WhiteHat' anywhere in the line, longest first."""
    found = []
    remaining = text
    for name in sorted((*SHIPPED, *PLANNED), key=len, reverse=True):
        token = f"{name} WhiteHat"
        if token in remaining:
            found.append(name)
            remaining = remaining.replace(token, " ")
    return found


def _candidates(text: str) -> list[str]:
    seen: list[str] = []

    def add(value: str) -> None:
        value = value.strip(" :.-")
        if value and value not in seen:
            seen.append(value)

    add(text)
    for name in names_in(text):
        token = f"{name} WhiteHat"
        start = 0
        while True:
            idx = text.find(token, start)
            if idx < 0:
                break
            add(text[idx + len(token):])
            start = idx + len(token)
    return seen


def evidence_problem(line: object) -> str | None:
    """None when the line cites no WhiteHat, or each one it cites really raises the title.

    A line with the word WhiteHat and no catalogue name is refused.
    """
    text = str(line).strip()
    if "WhiteHat" not in text:
        return None
    named = names_in(text)
    if not named or any(name not in SHIPPED for name in named):
        return f"Evidence cites a WhiteHat that is not available: {text}"
    matched = {signal_whitehat(candidate) for candidate in _candidates(text)}
    for name in named:
        if name not in matched:
            return f"Evidence is not a finding that WhiteHat raises: {text}"
    return None

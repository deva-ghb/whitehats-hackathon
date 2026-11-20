"""Checks on the live setup, read from the map the connections keep (HAND.1, ADV.1's first rule).

Some weaknesses are not in any code: they are settings in the cloud account. A public API with no rate limit is
the first one checked. Each map read runs the checks on the project's map from its connections; a weakness found
opens a finding on the node it is about, with a fix for a person, because Fix Factory writes code and cannot change
a live account. The finding and its fix close only when a later read shows the setting changed, never on a click.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Clone, Remediation, Signal
from .clones import read_clone

log = logging.getLogger("whitehats.setup_checks")

SOURCE = "whitehats:setup"
DESTINATION = "A person"
CHANGED = "the setting changed in AWS"
API_TYPE = "edge.api-gateway@1"


@dataclass(frozen=True)
class Weakness:
    rule: str
    severity: str
    title: str
    detail: str
    fix_title: str
    why_a_person: str
    steps: str


def _no_rate_limit(node: dict) -> Weakness | None:
    props = node.get("properties") or {}
    if props.get("api_type") != "http" or not props.get("public") or props.get("rate_limit") != "none":
        return None
    if not str(props.get("arn", "")).rstrip("/").split(":")[-1].startswith("/apis/") or "/stages/" in str(props.get("arn")):
        return None  # a stage tagged on its own is checked as part of its API
    name = node.get("name") or props.get("api_id", "the API")
    region = props.get("region", "")
    rps = props.get("throttling_rps")
    now = (f"Its limit is {rps:g} requests a second, as high as AWS allows the whole account, so it caps nothing."
           if rps else "It sets no limit of its own.")
    return Weakness(
        rule="public-api-no-rate-limit", severity="high",
        title=f"The public API {name} has no rate limit",
        detail=(f"Anyone on the internet can call {name}, and nothing caps how often. {now} One script can send it "
                "thousands of requests a second: every request runs the shop's code and costs money, and a flood "
                "takes the shop down for real customers."),
        fix_title=f"Set a rate limit on {name}",
        why_a_person=("This is a setting in the live AWS account, not code in a repository. Fix Factory only changes "
                      "code through pull requests, and WhiteHats only reads AWS, so a person with access has to make "
                      "the change. WhiteHats closes this fix by itself when it reads the limit in AWS."),
        steps=(f"1. Open API Gateway in the AWS console ({region}) and choose {name}.\n"
               "2. Under Protect, choose Throttling, then edit the default stage.\n"
               "3. Set a rate limit (for example 10 requests a second) and a burst limit (for example 20), and save.\n"
               "4. If your infrastructure code sets this limit, deploy it again instead, so the code and AWS agree.\n"
               "WhiteHats reads AWS again when it sees the change and closes this fix."))


RULES = (_no_rate_limit,)


def _fingerprint(rule: str, resource: str) -> str:
    return f"{rule}|{resource}"[:240]


def run(db: Session, clone: Clone) -> dict:
    """Run every check on the clone's latest version. Returns how many findings were opened and closed."""
    document = read_clone(db, clone.org_id, clone.id)
    if document is None:
        return {"opened": 0, "closed": 0}
    found: dict[str, tuple[dict, Weakness]] = {}
    for node in document["nodes"]:
        if node.get("type") != API_TYPE:
            continue
        for rule in RULES:
            weakness = rule(node)
            if weakness is not None:
                resource = (node.get("properties") or {}).get("arn") or node["id"]
                found[_fingerprint(weakness.rule, resource)] = (node, weakness)
    open_rows = {row.fingerprint: row for row in db.scalars(select(Signal).where(
        Signal.org_id == clone.org_id, Signal.project_id == clone.project_id, Signal.source == SOURCE,
        Signal.closed_at.is_(None))).all()}
    opened = [_open(db, clone, key, *found[key]) for key in found if key not in open_rows]
    read = {node["id"]: node for node in document["nodes"]}
    closed = [_close(db, row, read[row.node_key]) for key, row in open_rows.items()
              if key not in found and row.node_key in read]
    if opened or closed:
        log.info("setup checks clone=%s opened=%s closed=%s", clone.id, len(opened), len(closed))
    return {"opened": len(opened), "closed": len(closed)}


def _open(db: Session, clone: Clone, key: str, node: dict, weakness: Weakness) -> Signal:
    props = node.get("properties") or {}
    row = Signal(org_id=clone.org_id, project_id=clone.project_id, severity=weakness.severity, title=weakness.title,
                 detail=weakness.detail, entity=(node.get("name") or "")[:160], resource=str(props.get("arn") or "")[:300],
                 source=SOURCE, origin="live", clone_id=clone.id, node_key=node["id"], fingerprint=key)
    db.add(row)
    db.flush()
    db.add(Remediation(
        org_id=clone.org_id, project_id=clone.project_id, signal_id=row.id, title=weakness.fix_title[:200],
        target=(node.get("name") or node["id"])[:160], action=weakness.steps, why=weakness.why_a_person,
        destination=DESTINATION, factory_intent=None, risk=weakness.severity, status="pending", origin="live"))
    return row


def _close(db: Session, row: Signal, node: dict) -> Signal:
    """The read no longer finds the weakness: the finding closes, and its fix is verified by that read."""
    row.closed_at, row.closed_reason = utcnow(), CHANGED
    props = node.get("properties") or {}
    seen = (f"a rate limit of {props['throttling_rps']:g} requests a second"
            + (f" (bursts of {props['throttling_burst']:g})" if props.get("throttling_burst") else "")
            if props.get("throttling_rps") else "the setting")
    for fix in db.scalars(select(Remediation).where(
            Remediation.signal_id == row.id, Remediation.org_id == row.org_id,
            Remediation.status.in_(("pending", "dispatched")))).all():
        fix.status, fix.done_by, fix.done_at = "verified", "WhiteHats", utcnow()
        note = f"WhiteHats read AWS again: {row.entity or 'the resource'} now has {seen}, so this fix is done."
        fix.result = ((fix.result + "\n") if fix.result else "") + note
    return row


def awaits_the_setting(db: Session, fix: Remediation) -> bool:
    """A fix that only a later read of the account can finish (no person can mark it done)."""
    if fix.destination != DESTINATION or not fix.signal_id:
        return False
    return db.scalar(select(Signal.source).where(Signal.id == fix.signal_id)) == SOURCE

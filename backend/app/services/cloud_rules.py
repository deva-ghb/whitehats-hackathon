"""Business rules for the cloud (KS.2): what the business needs, judged against the map.

Only the business knows that checkout must take 50 orders a minute, or that the payment worker may only talk to the
payment provider. An approver says so once, as a rule in the knowledge sphere with a check: which things on the map it
is about, which setting, and what that setting must be. WhiteHats then judges the map against it every hour and after
each change it reads: a thing that breaks the rule is a finding on it, closed when the setting is put right.

A check, on a rule node ("expect"):
  {"on": ["edge.api-gateway"] or ["arn:...", "name"],   which map things: a type, an ARN or a name
   "property": "throttling_rps",                        a setting the map holds for them
   "op": ">=" | "<=" | "==" | "!=" | "set" | "unset",
   "value": 50}

A thing the map does not know the setting of is not a breach: it is listed as unknown, never guessed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Signal
from ..sphere import store
from . import auto_incidents
from .coverage import map_nodes

SOURCE = "whitehat:cloud"
PREFIX = "rule-"
OPS = (">=", "<=", "==", "!=", "set", "unset")
# The map's setting names in words a business reader knows (the screen uses the same words).
SETTING_WORDS = {"throttling_rps": "the requests it allows a second", "throttling_burst": "the short burst of requests it allows",
                 "rate_limit": "its rate limit", "public": "whether it is open to the internet",
                 "encrypted": "whether it is encrypted", "versioning": "whether it keeps old versions"}
WORDS = {">=": "at least", "<=": "at most", "==": "exactly", "!=": "anything but", "set": "set", "unset": "not set"}


def problems(expect: dict) -> list[str]:
    """What is wrong with a check, in words ([] when it can be judged)."""
    out = []
    if not expect.get("on"):
        out.append("Say which things on the map the rule is about.")
    if not str(expect.get("property") or "").strip():
        out.append("Say which setting the rule is about.")
    if expect.get("op") not in OPS:
        out.append(f"The comparison must be one of {', '.join(OPS)}.")
    elif expect["op"] in (">=", "<=") and not isinstance(expect.get("value"), (int, float)):
        out.append("At least and at most need a number.")
    return out


def _matches(node, on: list[str]) -> bool:
    kind = str(node.type).split("@", 1)[0]
    arn = str((node.properties or {}).get("arn") or "")
    return any(item in (kind, arn, node.name, node.node_key) for item in on)


def judge(actual, op: str, value) -> bool | None:
    """True kept, False broken, None unknown (the map does not hold the setting)."""
    if op == "set":
        return actual not in (None, "", "none", [], {})
    if op == "unset":
        return actual in (None, "", "none", [], {})
    if actual is None or actual == "":
        return None
    if op in (">=", "<="):
        try:
            number = float(actual)
        except (TypeError, ValueError):
            return None
        return number >= float(value) if op == ">=" else number <= float(value)
    same = str(actual).lower() == str(value).lower()
    return same if op == "==" else not same


def evaluate(db: Session, org_id: int, project_id: int) -> dict:
    """Judge every confirmed rule with a check against the map; open, refresh and close their findings. Returns
    {"kept": n, "broken": n, "unknown": n, "opened": n, "closed": n}."""
    rules = [n for n in store.state(db, project_id).as_dict()["nodes"]
             if n["kind"] == "Rule" and n.get("status") == "known" and n.get("expect")
             and not problems(n["expect"])]
    nodes = map_nodes(db, project_id)
    held = {s.fingerprint: s for s in db.scalars(select(Signal).where(
        Signal.org_id == org_id, Signal.project_id == project_id, Signal.source == SOURCE,
        Signal.fingerprint.like(f"{PREFIX}%"), Signal.closed_at.is_(None)))}
    seen: set[str] = set()
    tally = {"kept": 0, "broken": 0, "unknown": 0, "opened": 0, "closed": 0}
    for rule in rules:
        expect = rule["expect"]
        for node in (n for n in nodes if _matches(n, list(expect["on"]))):
            actual = (node.properties or {}).get(expect["property"])
            verdict = judge(actual, expect["op"], expect.get("value"))
            tally["kept" if verdict else "unknown" if verdict is None else "broken"] += 1
            key = f"{PREFIX}{rule['key']}-{node.node_key}"[:240]
            if verdict is None:
                seen.add(key)  # unknown now: an open finding stays open, it was not put right
            if verdict is not False:
                continue
            seen.add(key)
            needs = WORDS[expect["op"]] + ("" if expect["op"] in ("set", "unset") else f" {expect.get('value')}")
            setting = SETTING_WORDS.get(expect["property"], expect["property"].replace("_", " "))
            detail = (f"On {node.name}, {setting} is {actual if actual not in (None, '') else 'not set'}; the business "
                      f"rule needs {needs}. An approver confirmed this rule: put the setting right, or change the rule "
                      "if the business need changed.")
            title = f"Breaks a business rule: {rule['text']}"[:240]
            resource = str((node.properties or {}).get("arn") or node.name)[:300]
            if key in held:
                held[key].title, held[key].detail = title, detail
                continue
            db.add(Signal(org_id=org_id, project_id=project_id, severity="high" if rule.get("protects") in (
                "money", "data") else "medium", title=title, detail=detail, resource=resource, source=SOURCE,
                fingerprint=key, origin="live", entity=node.name[:160]))
            tally["opened"] += 1
    for key, row in held.items():
        if key not in seen:
            row.closed_at, row.closed_reason = utcnow(), "business rule kept"
            tally["closed"] += 1
    db.commit()
    if tally["opened"]:
        auto_incidents.sweep(db)
    return tally


def evaluate_all(db: Session) -> int:
    """Every project with a business rule for the cloud, judged again (the map changes under the rules). Returns how
    many projects were judged."""
    from ..models import Project, SphereVersion

    ids = set(db.scalars(select(SphereVersion.project_id).distinct()))
    done = 0
    for project in db.scalars(select(Project).where(Project.id.in_(ids), Project.archived.is_(False))):
        if any(n.get("expect") for n in store.state(db, project.id).nodes.values()):
            evaluate(db, project.org_id, project.id)
            done += 1
    return done

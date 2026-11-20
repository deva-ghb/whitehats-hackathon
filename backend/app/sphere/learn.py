"""How the knowledge sphere changes (KS.1). Only approver decisions make something known or forget it; pull requests,
pushes and cloud changes never edit it, they are judged against it.

Every decision is recorded where it is made (`note`) and processed here in order by the sphere's worker, so a slow
model call never holds up the person's click:

- propose       the onboarding audit (or an approver's request) proposes rules and concepts, shown as proposed
- confirm       an approver confirms proposals: they become known
- reject        an approver rejects proposals: they are dropped
- fix_approved  an approver approves a fix: the rule the fix protects becomes known, cited to the finding and the fix
- not_affected  an approver marks a finding not affected: an accepted exception, cited to that decision
- network_accepted  an approver accepts a network finding: an accepted exception
- reversed      an earlier decision is reversed (a statement withdrawn, a finding reopened): what it taught is forgotten
- check         what the sphere cites is checked: a node whose files and map resources are all gone is forgotten
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..db import utcnow
from ..models import Audit, NetworkFinding, Remediation, Signal, SphereDecision, VexStatement
from . import store

log = logging.getLogger("whitehats.sphere")

KINDS = ("propose", "confirm", "reject", "fix_approved", "not_affected", "network_accepted", "decided", "reversed",
         "check", "cloud_rule", "forget_rule")
PROTECTS = ("money", "data", "access", "availability", "other")
MAX_PROPOSALS = 40


class NotLearnable(Exception):
    """The decision cannot teach anything (its finding is gone, the model could not answer)."""


def note(db: Session, *, org_id: int, project_id: int, kind: str, ref: str = "", actor: str = "",
         payload: dict | None = None) -> SphereDecision:
    """Record a decision for the sphere to learn from. The caller commits with its own change."""
    if kind not in KINDS:
        raise ValueError(f"Unknown sphere decision {kind}")
    row = SphereDecision(org_id=org_id, project_id=project_id, kind=kind, ref=ref[:120], actor=actor[:120],
                         payload=payload or {})
    db.add(row)
    db.flush()  # the session does not flush by itself; a second note for the same thing must see this one
    return row


def note_fix(db: Session, fix: Remediation, actor: str) -> None:
    """An approved fix for a finding teaches the rule it protects, once per fix (Approvals and Fix Factory both
    approve)."""
    if not fix.signal_id:
        return
    ref = f"fix:{fix.id}"
    if db.scalar(select(SphereDecision.id).where(SphereDecision.project_id == fix.project_id,
                                                 SphereDecision.ref == ref).limit(1)) is None:
        note(db, org_id=fix.org_id, project_id=fix.project_id, kind="fix_approved", ref=ref, actor=actor,
             payload={"remediation_id": fix.id})


def is_approver(db: Session, user) -> bool:
    """Only an approver's decision teaches the sphere (people who may triage but not approve do not)."""
    from ..permissions import held

    return "approve" in held(db, user, getattr(user, "project", None))


def note_reversal(db: Session, *, org_id: int, project_id: int, taught: str, actor: str) -> None:
    """A decision that taught the sphere is reversed: forget what it taught (nothing, if it taught nothing)."""
    if db.scalar(select(SphereDecision.id).where(SphereDecision.project_id == project_id,
                                                 SphereDecision.ref == taught).limit(1)) is not None:
        note(db, org_id=org_id, project_id=project_id, kind="reversed", ref=f"reversed:{taught}", actor=actor,
             payload={"taught": taught})


def slug(text: str, limit: int = 90) -> str:
    out, dash = [], False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    return "".join(out).strip("-")[:limit].strip("-") or "item"


def _decision_cite(d: SphereDecision, label: str) -> dict:
    return {"kind": "decision", "ref": d.ref or f"decision:{d.id}", "by": d.actor, "at": d.created_at.isoformat(),
            "label": label}


def _where(signal: Signal) -> dict | None:
    """The file and line a finding names, as a citation."""
    entity = str(signal.entity or "")
    path, _, line = entity.rpartition(":")
    if path and line.isdigit():
        return {"kind": "file", "path": path, "lines": [int(line), int(line)]}
    if entity and "/" in entity and not entity.startswith("http"):
        return {"kind": "file", "path": entity, "lines": []}
    return None


def _put(node: dict) -> dict:
    return {"op": "put_node", "node": node}


def _edge(kind: str, source: str, target: str, taught_by: list[str]) -> dict:
    return {"op": "put_edge", "edge": {"kind": kind, "source": source, "target": target, "taught_by": taught_by}}


# ------------------------------------------------------------------ the model


def _ask(role: str, system: str, user: str, agent_id: str, tokens: int = 6000) -> dict:
    """One question to the audit models, answered as a JSON object."""
    from ..audit.azure import Client, ModelError

    folder = DATA_DIR / "sphere"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        reply = Client(folder / "usage.jsonl").respond(role, {"input": [
            {"role": "developer", "content": system}, {"role": "user", "content": user}],
            "reasoning": {"effort": "low"}, "max_output_tokens": tokens}, agent_id)
    except ModelError as exc:
        raise NotLearnable(f"The model could not answer: {exc}") from exc
    text = "".join(part.get("text", "") for item in reply.get("output") or [] if item.get("type") == "message"
                   for part in item.get("content") or [])
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1]) if start >= 0 < end else {}
    except ValueError as exc:
        raise NotLearnable("The model's answer was not readable.") from exc


AUDIT_WORDS = {"onboarding": "The first audit", "cloud_change": "An audit after a cloud change",
               "schedule": "A scheduled audit", "request": "A requested audit", "code_change": "A pull request's review"}

PROPOSE_SYSTEM = """You read a security audit of one software project and say what the business depends on.
Name the business concepts (the things the business deals in: customer, order, price, payment, receipt...) and the
business rules the code must keep (invariants: "an order's price comes from the catalogue, never from the request";
"a customer sees only their own orders"). Only rules the code shows the business relies on, each cited to the files and
lines that implement or enforce it. Not generic security advice. At most 12 concepts and 20 rules.
For each rule say what breaking it costs: money, data, access, availability or other.
Answer with one JSON object only:
{"concepts": [{"name": "...", "detail": "one sentence"}],
 "rules": [{"text": "one sentence, a rule a business person would recognise", "detail": "why it matters",
            "protects": "money", "concepts": ["concept names"],
            "cites": [{"path": "repository-relative file", "first_line": 1, "last_line": 9}]}],
 "relations": [{"from": "concept name", "to": "concept name"}]}"""


def propose(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    """Proposals from an audit's map of the system and its findings. Nothing proposed is known until confirmed."""
    audit = db.get(Audit, int(d.payload.get("audit_id") or 0))
    if audit is None or not audit.run_dir:
        raise NotLearnable("The audit to propose from is gone.")
    run = Path(audit.run_dir)
    target = run / "target"
    architecture = (run / "architecture.md").read_text(errors="replace")[:40_000] if (
        run / "architecture.md").is_file() else ""
    if not architecture:
        raise NotLearnable("The audit has no map of the system to propose from.")
    findings = []
    if (run / "findings.json").is_file():
        for r in json.loads((run / "findings.json").read_text() or "[]"):
            if r.get("verdict") == "confirmed":
                sink = (r.get("trace") or [{}])[-1]
                findings.append(f"- {r.get('title')} ({sink.get('file')}:{sink.get('line')})")
    files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file() and ".git" not in p.parts
                   ) if target.is_dir() else []
    current = store.state(db, d.project_id)
    already = [f"- {n['kind']}: {n['text']}" for n in current.nodes.values() if n["kind"] in ("Concept", "Rule")]
    answer = _ask("strong", PROPOSE_SYSTEM, f"## The audit's map of the system\n{architecture}\n\n## Confirmed findings\n"
                  f"{chr(10).join(findings[:60]) or 'none'}\n\n## Files\n{chr(10).join(files[:800])}\n\n"
                  "## Already in the sphere (name these concepts exactly as written; do not propose these rules again, "
                  f"in any wording)\n{chr(10).join(already[:120]) or 'nothing yet'}",
                  f"sphere-propose-{audit.id}", tokens=12000)
    known_files = set(files)
    ops: list[dict] = []
    concepts: dict[str, str] = {}
    for c in (answer.get("concepts") or [])[:12]:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        key = f"concept:{slug(name)}"
        concepts[name.lower()] = key
        if key not in current.nodes:
            ops.append(_put({"key": key, "kind": "Concept", "text": name[:120], "detail": str(c.get("detail") or "")[:500],
                             "status": "proposed", "cites": [{"kind": "decision", "ref": f"audit:{audit.id}",
                                                              "by": "WhiteHats", "label": "Proposed by the audit"}],
                             "lost": [], "taught_by": []}))
    for r in (answer.get("rules") or [])[:20]:
        text = str(r.get("text") or "").strip()
        cites = []
        for c in r.get("cites") or []:
            path = str(c.get("path") or "").strip().lstrip("./")
            if path in known_files:
                first, last = int(c.get("first_line") or 0), int(c.get("last_line") or 0)
                cites.append({"kind": "file", "path": path, "lines": [first, max(first, last)] if first else []})
        if not text or not cites:
            continue  # a rule nothing in the code shows is a guess, not a proposal
        key = f"rule:{slug(text)}"
        if key in current.nodes:
            continue
        protects = str(r.get("protects") or "other").lower()
        ops.append(_put({"key": key, "kind": "Rule", "text": text[:300], "detail": str(r.get("detail") or "")[:800],
                         "protects": protects if protects in PROTECTS else "other", "status": "proposed",
                         "cites": cites, "lost": [], "taught_by": []}))
        for name in r.get("concepts") or []:
            if (target_key := concepts.get(str(name).lower())) or (
                    target_key := next((k for k, n in current.nodes.items() if n["kind"] == "Concept"
                                        and n["text"].lower() == str(name).lower()), None)):
                ops.append(_edge("ABOUT", key, target_key, []))
    for rel in answer.get("relations") or []:
        a, b = concepts.get(str(rel.get("from") or "").lower()), concepts.get(str(rel.get("to") or "").lower())
        if a and b and a != b:
            ops.append(_edge("RELATES", a, b, []))
    return ops[: MAX_PROPOSALS * 3], f"{AUDIT_WORDS.get(audit.trigger, 'An audit')} proposed what the business depends on"


def confirm(db: Session, d: SphereDecision, *, keep: bool) -> tuple[list[dict], str]:
    """An approver confirms (keep) or rejects proposals, by key."""
    current = store.state(db, d.project_id)
    keys = [k for k in d.payload.get("keys") or [] if k in current.nodes and current.nodes[k]["status"] == "proposed"]
    ops: list[dict] = []
    for key in keys:
        if keep:
            node = dict(current.nodes[key])
            node["status"] = "known"
            node["taught_by"] = sorted(set(node.get("taught_by") or []) | {f"confirm:{d.id}"})
            node["cites"] = [*node.get("cites", []), _decision_cite(d, f"Confirmed by {d.actor}")]
            ops.append(_put(node))
        else:
            ops.append({"op": "drop_node", "key": key})
    verb = "confirmed" if keep else "rejected"
    return ops, f"{d.actor} {verb} {len(keys)} proposal{'s' if len(keys) != 1 else ''}"


FIX_SYSTEM = """A person approved a fix for a security finding in a software project. Say which business rule the fix
protects, as one sentence a business person would recognise ("an order's price comes from the catalogue, never from
the request"), not as a description of the bug. If one of the known rules listed is that rule, give its key instead.
Name the business concept it is about (one or two words), and what breaking it costs: money, data, access,
availability or other.
Answer with one JSON object only: {"existing_rule": "key or empty", "text": "...", "detail": "one sentence",
"concept": "...", "protects": "money"}"""


def fix_approved(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    fix = db.get(Remediation, int(d.payload.get("remediation_id") or 0))
    signal = db.get(Signal, fix.signal_id) if fix and fix.signal_id else None
    if fix is None or signal is None:
        raise NotLearnable("The fix or its finding is gone.")
    current = store.state(db, d.project_id)
    rules = [n for n in current.nodes.values() if n["kind"] == "Rule"]
    answer = _ask("cheap", FIX_SYSTEM, f"Finding: {signal.title}\nWhere: {signal.entity}\n{(signal.detail or '')[:3000]}"
                  f"\n\nThe fix: {fix.title}\n{(fix.action or '')[:1500]}\n\nKnown rules:\n" + "\n".join(
                      f"- {n['key']}: {n['text']}" for n in rules[:80]), f"sphere-fix-{fix.id}", tokens=2000)
    taught = f"fix:{fix.id}"
    cites = [c for c in [_where(signal)] if c] + [
        {"kind": "finding", "id": signal.id, "title": signal.title[:200]},
        _decision_cite(d, f"Fix approved by {d.actor}")]
    existing = current.nodes.get(str(answer.get("existing_rule") or ""))
    ops: list[dict] = []
    if existing and existing["kind"] == "Rule":
        node = dict(existing)
        node["status"] = "known"
        node["taught_by"] = sorted(set(node.get("taught_by") or []) | {taught})
        node["cites"] = [*node.get("cites", []), *[c for c in cites if c not in node.get("cites", [])]]
        ops.append(_put(node))
        return ops, f"{d.actor} approved a fix that protects a rule already known: {node['text']}"
    text = str(answer.get("text") or "").strip() or f"The code must not allow: {signal.title}"
    protects = str(answer.get("protects") or "other").lower()
    key = f"rule:{slug(text)}"
    ops.append(_put({"key": key, "kind": "Rule", "text": text[:300], "detail": str(answer.get("detail") or "")[:800],
                     "protects": protects if protects in PROTECTS else "other", "status": "known", "cites": cites,
                     "lost": [], "taught_by": [taught]}))
    concept = str(answer.get("concept") or "").strip()
    if concept:
        ckey = next((k for k, n in current.nodes.items() if n["kind"] == "Concept" and n["text"].lower() == concept.lower()),
                    f"concept:{slug(concept)}")
        node = dict(current.nodes.get(ckey) or {"key": ckey, "kind": "Concept", "text": concept[:120], "detail": "",
                                                "cites": [], "lost": [], "taught_by": []})
        node["status"] = "known"
        node["taught_by"] = sorted(set(node.get("taught_by") or []) | {taught})
        ops.append(_put(node))
        ops.append(_edge("ABOUT", key, ckey, [taught]))
    return ops, f"{d.actor} approved a fix: WhiteHats learned the rule it protects"


def not_affected(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    statement = db.get(VexStatement, int(d.payload.get("statement_id") or 0))
    signal = db.get(Signal, statement.signal_id) if statement and statement.signal_id else None
    if statement is None or signal is None:
        raise NotLearnable("The statement or its finding is gone.")
    current = store.state(db, d.project_id)
    where = _where(signal)
    taught = f"vex:{statement.id}"
    key = f"exception:{slug(signal.title, 40)}-{statement.id}"
    cites = [c for c in [where] if c] + [{"kind": "finding", "id": signal.id, "title": signal.title[:200]},
                                         _decision_cite(d, f"Marked not affected by {d.actor}")]
    ops = [_put({"key": key, "kind": "Exception", "text": f"Not a problem here: {signal.title}"[:300],
                 "detail": (statement.impact_statement or "")[:800], "status": "known", "cites": cites, "lost": [],
                 "taught_by": [taught]})]
    if where:  # where a known rule is implemented in the same file, this is where it deliberately does not apply
        for rule in current.nodes.values():
            if rule["kind"] == "Rule" and any(c.get("kind") == "file" and c.get("path") == where["path"]
                                              for c in rule.get("cites") or []):
                ops.append(_edge("EXEMPTS", key, rule["key"], [taught]))
    return ops, f"{d.actor} marked a finding not affected: WhiteHats accepted it as an exception"


def decided(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    """An approver called a finding a false positive, or accepted its risk until a date (FD.1)."""
    from ..models import FindingDecision

    decision = db.get(FindingDecision, int(d.payload.get("decision_id") or 0))
    signal = db.get(Signal, decision.signal_id) if decision and decision.signal_id else None
    if decision is None or signal is None:
        raise NotLearnable("The decision or its finding is gone.")
    current = store.state(db, d.project_id)
    where = _where(signal)
    taught = f"decision:{decision.id}"
    false_positive = decision.kind == "false_positive"
    text = (f"Not a problem here: {signal.title}" if false_positive
            else f"Risk accepted until {decision.until:%d %b %Y}: {signal.title}")
    cites = [c for c in [where] if c] + ([{"kind": "node", "arn": signal.resource, "name": signal.resource}]
                                         if signal.resource.startswith("arn:") else []) + [
        {"kind": "finding", "id": signal.id, "title": signal.title[:200]},
        _decision_cite(d, f"{'Called a false positive' if false_positive else 'Risk accepted'} by {d.actor}")]
    key = f"exception:{slug(signal.title, 40)}-d{decision.id}"
    ops = [_put({"key": key, "kind": "Exception", "text": text[:300], "detail": decision.reason[:800],
                 "status": "known", "cites": cites, "lost": [], "taught_by": [taught]})]
    if where:
        for rule in current.nodes.values():
            if rule["kind"] == "Rule" and any(c.get("kind") == "file" and c.get("path") == where["path"]
                                              for c in rule.get("cites") or []):
                ops.append(_edge("EXEMPTS", key, rule["key"], [taught]))
    what = "a false positive" if false_positive else "an accepted risk"
    return ops, f"{d.actor} called a finding {what}: WhiteHats learned it as an exception"


def network_accepted(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    finding = db.get(NetworkFinding, int(d.payload.get("finding_id") or 0))
    if finding is None:
        raise NotLearnable("The network finding is gone.")
    taught = f"network:{finding.id}"
    key = f"exception:network-{slug(finding.key, 40)}"
    return [_put({"key": key, "kind": "Exception", "text": f"Accepted on the network: {finding.title}"[:300],
                  "detail": (finding.detail or "")[:800], "status": "known", "lost": [], "taught_by": [taught],
                  "cites": [_decision_cite(d, f"Accepted by {d.actor}")]})], (
        f"{d.actor} accepted a network finding: WhiteHats accepted it as an exception")


def cloud_rule(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    """An approver states a business rule for the cloud, with the check that judges the map against it (KS.2). It is
    known at once: the approver is the business saying so."""
    from ..services.cloud_rules import problems

    p = d.payload or {}
    text, expect = str(p.get("text") or "").strip(), dict(p.get("expect") or {})
    if not text or problems(expect):
        raise NotLearnable(" ".join(["Say the rule in words."] if not text else []) + " ".join(problems(expect)))
    key = f"rule:cloud-{slug(text, 60)}"
    ops = [_put({"key": key, "kind": "Rule", "text": text[:300], "detail": str(p.get("detail") or "")[:800],
                 "protects": str(p.get("protects") or "other"), "status": "known", "lost": [], "expect": expect,
                 "taught_by": [f"cloud_rule:{d.id}"], "cites": [_decision_cite(d, f"Stated by {d.actor}")]})]
    concept = str(p.get("concept") or "").strip()
    if concept:
        ckey = f"concept:{slug(concept, 40)}"
        current = store.state(db, d.project_id)
        if ckey not in current.nodes:
            ops.append(_put({"key": ckey, "kind": "Concept", "text": concept[:80], "status": "known", "lost": [],
                             "taught_by": [f"cloud_rule:{d.id}"], "cites": []}))
        ops.append(_edge("ABOUT", key, ckey, [f"cloud_rule:{d.id}"]))
    return ops, f"{d.actor} stated a business rule for the cloud"


def forget_rule(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    """An approver withdraws business rules for the cloud they or another approver stated (KS.2)."""
    current = store.state(db, d.project_id)
    keys = [k for k in (d.payload or {}).get("keys") or [] if (current.nodes.get(k) or {}).get("expect")]
    return [{"op": "drop_node", "key": k} for k in keys], f"{d.actor} withdrew {len(keys)} business rule(s) for the cloud"


def reversed_(db: Session, d: SphereDecision) -> tuple[list[dict], str]:
    """Forget what the reversed decision taught. A node another decision also taught keeps those."""
    gone = str(d.payload.get("taught") or d.ref)
    current = store.state(db, d.project_id)
    ops: list[dict] = []
    for node in current.nodes.values():
        taught = node.get("taught_by") or []
        if gone not in taught:
            continue
        rest = [t for t in taught if t != gone]
        if rest:
            ops.append(_put({**node, "taught_by": rest,
                             "cites": [c for c in node.get("cites") or [] if c.get("ref") != gone]}))
        else:
            ops.append({"op": "drop_node", "key": node["key"]})
    return ops, f"{d.actor} reversed an earlier decision: WhiteHats forgot what it taught"


def check(db: Session, d: SphereDecision, files: set[str] | None = None, arns: set[str] | None = None,
          ) -> tuple[list[dict], str]:
    """Forget what cites only things that are gone. `files` and `arns` are what exists now (read when not given)."""
    if files is None:
        files = _default_branch_files(db, d)
    if arns is None:
        arns = _map_arns(db, d.project_id)
    current = store.state(db, d.project_id)
    ops: list[dict] = []
    forgot = 0

    def present(c: dict) -> bool:  # what could not be read is never taken as gone
        if c["kind"] == "file":
            return files is None or c.get("path") in files
        return arns is None or c.get("arn") in arns

    for node in current.nodes.values():
        cites = node.get("cites") or []
        evidence = [c for c in cites if c.get("kind") in store.EVIDENCE]
        if not evidence:
            continue
        gone = [c for c in evidence if not present(c)]
        if not gone:
            continue
        if len(gone) == len(evidence):
            ops.append({"op": "drop_node", "key": node["key"]})
            forgot += 1
        else:
            ops.append(_put({**node, "cites": [c for c in cites if c not in gone],
                             "lost": [*node.get("lost", []), *gone]}))
    return ops, (f"What {forgot} item{'s' if forgot != 1 else ''} cited is gone from the code or the account"
                 if forgot else "Some citations are gone from the code or the account")


def _default_branch_files(db: Session, d: SphereDecision) -> set[str] | None:
    """Every file on the project's repository's default branch; None when it cannot be read (nothing is forgotten)."""
    from ..services import audit_jobs

    try:
        found = audit_jobs._repository(db, SimpleNamespace(org_id=d.org_id, project_id=d.project_id))  # type: ignore[arg-type]
        if found is None:
            return None
        source, repo = found
        return set(source.repository_files(repo).paths)
    except Exception as exc:  # noqa: BLE001 - a read that fails must never read as "everything is gone"
        log.warning("sphere check could not read the repository for project %s: %s", d.project_id, exc)
        return None


def _map_arns(db: Session, project_id: int) -> set[str] | None:
    from .. import scanning

    try:
        nodes = scanning.twin_nodes(db, [project_id])
    except Exception as exc:  # noqa: BLE001
        log.warning("sphere check could not read the map for project %s: %s", project_id, exc)
        return None
    arns = {str((n.get("properties") or {}).get("arn") or "") for n in nodes}
    arns.discard("")
    return arns or None


HANDLERS = {
    "propose": propose,
    "confirm": lambda db, d: confirm(db, d, keep=True),
    "reject": lambda db, d: confirm(db, d, keep=False),
    "fix_approved": fix_approved,
    "not_affected": not_affected,
    "network_accepted": network_accepted,
    "cloud_rule": cloud_rule,
    "forget_rule": forget_rule,
    "decided": decided,
    "reversed": reversed_,
    "check": check,
}


def _claim(db: Session, d: SphereDecision) -> bool:
    """Take the decision for this pass, so two passes never learn the same thing twice."""
    from sqlalchemy import update

    taken = db.execute(update(SphereDecision).where(SphereDecision.id == d.id, SphereDecision.status == "pending")
                       .values(status="working")).rowcount
    db.commit()
    db.refresh(d)
    return bool(taken)


def process(db: Session, d: SphereDecision) -> None:
    if not _claim(db, d):
        return
    try:
        ops, reason = HANDLERS[d.kind](db, d)
    except NotLearnable as exc:
        d.status, d.error = "failed", str(exc)[:2000]
        db.commit()
        return
    version = store.record(db, d.org_id, d.project_id, ops, actor=d.actor or "WhiteHats", reason=reason,
                           decision_id=d.id)
    d.status, d.version_id = "done", version.id if version else None
    db.commit()
    store.sync_graph(db, d.project_id)
    if any(op.get("op") == "drop_node" or (op.get("node") or {}).get("expect") for op in ops):
        from ..services import cloud_rules

        cloud_rules.evaluate(db, d.org_id, d.project_id)  # a new or withdrawn cloud rule is judged at once (KS.2)


def process_pending(db: Session, limit: int = 20) -> int:
    """One pass of the sphere's worker, oldest decision first. Returns how many it processed."""
    rows = list(db.scalars(select(SphereDecision).where(SphereDecision.status == "pending")
                           .order_by(SphereDecision.id).limit(limit)))
    for d in rows:
        try:
            process(db, d)
        except Exception as exc:
            log.exception("sphere decision %s failed", d.id)
            db.rollback()
            d.status, d.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
            db.commit()
    return len(rows)


def check_due(db: Session, every_s: int = 3600) -> int:
    """Ask for a citation check of each project with a sphere, at most hourly (and after each default-branch push,
    which notes its own)."""
    from ..models import SphereVersion

    asked = 0
    projects = {(v.org_id, v.project_id) for v in db.scalars(select(SphereVersion))}
    for org_id, project_id in projects:
        last = db.scalar(select(SphereDecision.created_at).where(
            SphereDecision.project_id == project_id, SphereDecision.kind == "check").order_by(
            SphereDecision.id.desc()).limit(1))
        if last is None or (utcnow() - last).total_seconds() >= every_s:
            # checks that changed nothing are not history worth keeping; the newest says when the last one ran
            db.execute(delete(SphereDecision).where(
                SphereDecision.project_id == project_id, SphereDecision.kind == "check",
                SphereDecision.status == "done", SphereDecision.version_id.is_(None)))
            note(db, org_id=org_id, project_id=project_id, kind="check", actor="WhiteHats")
            asked += 1
    db.commit()
    return asked


def resume_after_restart(db: Session) -> int:
    """At start-up: a decision cut off mid-way is learned again from the start (nothing of it was saved)."""
    from sqlalchemy import update

    n = db.execute(update(SphereDecision).where(SphereDecision.status == "working").values(status="pending")).rowcount
    db.commit()
    return int(n or 0)

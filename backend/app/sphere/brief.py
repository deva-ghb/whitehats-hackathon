"""What a review or audit is told from the knowledge sphere (KS.1): the known business rules and accepted exceptions
around the code it reads, so it judges a change for contextual correctness as well as code correctness. Proposed
rules are offered as hints, never as grounds for a finding on their own."""

from __future__ import annotations

from sqlalchemy.orm import Session

from . import store

FILE = "sphere.md"
MAX_NODES = 60


def _cites(node: dict) -> str:
    parts = []
    for c in node.get("cites") or []:
        if c.get("kind") == "file":
            lines = c.get("lines") or []
            parts.append(f"{c['path']}:{lines[0]}-{lines[1]}" if len(lines) == 2 and lines[0] else c["path"])
        elif c.get("kind") == "node":
            parts.append(c.get("arn") or c.get("name") or "")
        elif c.get("kind") == "decision":
            parts.append(c.get("label") or c.get("ref") or "")
    return "; ".join(p for p in parts if p)


def brief(db: Session, project_id: int, paths: list[str] | None = None) -> str:
    """Markdown for the run folder; "" when the sphere knows nothing relevant. `paths`: only what is around these
    files (a pull request); None: everything (a full audit)."""
    if paths is None:
        nodes = store.state(db, project_id).as_dict()["nodes"]
    else:
        nodes = store.around(db, project_id, paths)
    rules = [n for n in nodes if n["kind"] == "Rule"][:MAX_NODES]
    exceptions = [n for n in nodes if n["kind"] == "Exception"][:MAX_NODES]
    if not rules and not exceptions:
        return ""
    out = ["## What the business depends on (the project's knowledge sphere)",
           ("Rules marked KNOWN were confirmed by the project's approvers. A change that breaks a known rule is a "
           "finding even when the code is otherwise correct: name the rule by its key in the finding, cite it, and "
           "rate the severity by what the rule protects (money the business does not intend to lose or give is high "
           "or above). Rules marked PROPOSED are hints from an earlier audit, not yet confirmed: use them to look, "
           "never as the only grounds for a finding. Accepted exceptions are places approvers decided a pattern is "
           "safe: do not report them again unless the change makes them unsafe in a new way."), ""]
    for n in rules:
        status = "KNOWN" if n.get("status") == "known" else "PROPOSED"
        protects = f", protects {n['protects']}" if n.get("protects") else ""
        out.append(f"- [{status}{protects}] {n['key']}: {n['text']} (cited: {_cites(n) or 'none'})")
        if n.get("detail"):
            out.append(f"  {n['detail']}")
    if exceptions:
        out += ["", "Accepted exceptions:"]
        for n in exceptions:
            out.append(f"- {n['key']}: {n['text']} (cited: {_cites(n) or 'none'})")
            if n.get("detail"):
                out.append(f"  Why: {n['detail']}")
    return "\n".join(out) + "\n"

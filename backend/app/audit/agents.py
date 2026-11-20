"""One audit agent: a fresh model conversation with read-only tools that ends by submitting one checked JSON answer.

Each agent has its own conversation, scratch folder and artifacts, so no agent sees another's reasoning (the
method's independence rule). It ends only through `submit`; an answer that fails its check goes back to the agent
with the reasons, a few times, before the agent is marked failed. Near the turn limit it is told to submit.

The conversation is a saved session (`sessions.py`): kept after every turn, so a crash resumes at the last turn, and
compacted into a handoff note when it grows large. A finished agent writes `result.json`; running the same agent
again returns it without a model call. Every turn is appended to `transcript.jsonl` for review.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import sessions, tools
from .azure import Client, ContextTooLong, ModelError, PolicyBlocked

log = logging.getLogger("whitehats.audit.agents")

CONTEXT_BUDGET = 70_000  # when folding is the fallback, fold until the conversation is under this
MAX_REPAIRS = 3
MAX_BLOCKED = 3
WITHHELD = ("[Withheld: the model provider's safety filter refused a request containing this output. Do not request "
            "it again. Record what you could not review as unresolved: 'withheld by the model provider's filter'.]")
FOLDED = "[Earlier tool output folded to save room. Call the tool again if you need it.]"


class AgentFailed(RuntimeError):
    pass


@dataclass
class Spec:
    agent_id: str
    model: str  # a route in azure.ROUTES (strong | cheap) or one model key
    instructions: str
    task: str
    submit_description: str
    check: Callable[[dict], list[str]]  # problems with an answer; empty when it is acceptable
    can_run_checks: bool = False
    effort: str = "medium"
    max_turns: int = 40
    max_output_tokens: int = 16_000


READ_TOOLS = [
    {"type": "function", "name": "list_files", "description": "List repository files under a folder (recursive).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "folder, '' for root"}},
                    "required": ["path"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "read_file",
     "description": f"Read a file with line numbers, at most {tools.MAX_READ_LINES} lines per call.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": ["integer", "null"]}},
         "required": ["path", "start_line", "end_line"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "search",
     "description": "Search file contents line by line. Python regular expression unless literal is true.",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string", "description": "folder or file, '' for all"},
         "literal": {"type": "boolean"}}, "required": ["pattern", "path", "literal"], "additionalProperties": False},
     "strict": True},
]
CHECK_TOOL = {
    "type": "function", "name": "run_check",
    "description": ("Run a small bounded check in a sealed container: no network, the repository read-only at /target, "
                    "your files in a writable /scratch, Python 3.12 with pytest, pyyaml, python-hcl2, boto3 and moto. "
                    "Use dummy data only. Stop at the minimum result that shows the boundary failure or its absence."),
    "parameters": {"type": "object", "properties": {
        "files": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "content": {"type": "string"}},
            "required": ["name", "content"], "additionalProperties": False}},
        "command": {"type": "string", "description": "for example: python check.py"},
        "workdir": {"type": "string", "description": "'' to run in /scratch, or a repository folder to run in"}},
        "required": ["files", "command", "workdir"], "additionalProperties": False}, "strict": True}


CLOUD_TOOL = {
    "type": "function", "name": "cloud_facts",
    "description": ("Read the live cloud account as WhiteHats last read it with its read-only role: map nodes (functions, "
                    "APIs, buckets, roles, queues, tables) and their actual settings. Search by name, type, id or a "
                    "setting; an empty query lists everything. Use it for deployment facts source cannot establish."),
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                   "additionalProperties": False}, "strict": True}


def submit_tool(description: str) -> dict:
    return {"type": "function", "name": "submit", "description": description,
            "parameters": {"type": "object", "properties": {"json": {
                "type": "string", "description": "the whole answer as one JSON object"}},
                "required": ["json"], "additionalProperties": False}, "strict": True}


def run(client: Client, snap: tools.Snapshot, run_dir: Path, spec: Spec) -> dict:
    agent_dir = run_dir / "agents" / spec.agent_id
    done = agent_dir / "result.json"
    if done.exists():
        return json.loads(done.read_text())
    (agent_dir / "scratch").mkdir(parents=True, exist_ok=True)
    (agent_dir / "artifacts").mkdir(exist_ok=True)
    transcript = agent_dir / "transcript.jsonl"
    toolset = (READ_TOOLS + ([CLOUD_TOOL] if snap.cloud else []) + ([CHECK_TOOL] if spec.can_run_checks else [])
               + [submit_tool(spec.submit_description)])
    s = sessions.Session.open(agent_dir / "session.json", [
        {"role": "developer", "content": spec.instructions}, {"role": "user", "content": spec.task}])
    if s.resumed():
        _log(transcript, {"turn": s.turn, "resumed": True, "compactions": s.compactions})
    while s.turn < spec.max_turns + 4:
        if s.turn >= spec.max_turns - 3 and not s.warned:
            s.items.append({"role": "user", "content": "You are almost out of turns. Call submit now with your best "
                                                       "complete answer, recording anything unfinished as unresolved."})
            s.warned = True
        if sessions.due(s):
            if sessions.compact(client, s, spec.agent_id):
                _log(transcript, {"turn": s.turn, "compacted": s.compactions, "note_chars": len(s.notes[-1])})
            else:
                _fold(s.items, keep=6)
            s.save()
        body = {"input": list(s.items), "tools": toolset, "reasoning": {"effort": spec.effort},
                "max_output_tokens": spec.max_output_tokens, "parallel_tool_calls": True}
        try:
            response = client.respond(spec.model, body, spec.agent_id)
        except ContextTooLong:
            if not sessions.compact(client, s, spec.agent_id):
                _fold(s.items, keep=2)
            s.save()
            continue
        except PolicyBlocked as exc:
            # The provider refused this turn. Withhold the newest tool outputs (the text it most likely objected to),
            # tell the agent which, and go on: one refused file is a recorded gap, not a failed agent.
            withheld = _withhold(s.items)
            _log(transcript, {"turn": s.turn, "blocked": str(exc)[:300], "withheld": withheld})
            s.blocked += 1
            s.save()
            if not withheld or s.blocked > MAX_BLOCKED:
                raise AgentFailed(f"{spec.agent_id}: the model provider's safety filter refused the request") from exc
            continue
        except ModelError as exc:
            if s.resumed() and "reasoning" in str(exc).lower() and sessions.drop_reasoning(s):
                s.save()  # a resumed session's encrypted reasoning was refused; carry on without it
                continue
            _log(transcript, {"turn": s.turn, "error": str(exc)})
            raise AgentFailed(f"{spec.agent_id}: {exc}") from exc
        output = response.get("output") or []
        s.items.extend(output)
        calls = [o for o in output if o.get("type") == "function_call"]
        _log(transcript, {"turn": s.turn, "output": [_brief(o) for o in output], "usage": response.get("usage")})
        s.turn += 1
        if not calls:
            s.items.append({"role": "user", "content": "Use the tools, and finish only by calling submit."})
            s.save()
            continue
        for call in calls:
            name, call_id = call.get("name"), call.get("call_id")
            try:
                args = json.loads(call.get("arguments") or "{}")
            except json.JSONDecodeError:
                s.items.append(_out(call_id, "Your arguments were not valid JSON. Send them again."))
                continue
            if name == "submit":
                answer, problems = _parse(args.get("json", ""), spec.check)
                if not problems:
                    agent_dir.joinpath("result.json").write_text(json.dumps(answer, indent=1))
                    s.save()
                    return answer
                s.repairs += 1
                if s.repairs > MAX_REPAIRS:
                    s.save()
                    raise AgentFailed(f"{spec.agent_id}: the answer still failed its checks: {problems[:5]}")
                s.items.append(_out(call_id, "Not accepted. Fix these and submit again:\n- "
                                    + "\n- ".join(problems[:30])))
                continue
            s.items.append(_out(call_id, _tool(snap, agent_dir, spec, name, args)))
        s.save()  # every turn is kept: a crash from here resumes at the next turn, not at the start
    raise AgentFailed(f"{spec.agent_id}: ran out of turns without an accepted answer.")


def _tool(snap: tools.Snapshot, agent_dir: Path, spec: Spec, name: str, args: dict) -> str:
    try:
        if name == "list_files":
            return tools.clip(tools.list_files(snap, args.get("path", "")))
        if name == "read_file":
            return tools.clip(tools.read_file(snap, args["path"], args.get("start_line") or 1, args.get("end_line")))
        if name == "search":
            return tools.clip(tools.search(snap, args["pattern"], args.get("path", ""), bool(args.get("literal"))))
        if name == "cloud_facts" and snap.cloud:
            return tools.clip(tools.cloud_facts(snap, args.get("query", "")))
        if name == "run_check" and spec.can_run_checks:
            files = {f["name"]: f["content"] for f in args.get("files") or []}
            shown, _, _ = tools.run_check(snap, agent_dir, spec.agent_id, files, args["command"], args.get("workdir", ""))
            return tools.clip(shown)
        return f"There is no tool called {name}."
    except tools.ToolError as exc:
        return f"Tool error: {exc}"
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return f"Tool error: {type(exc).__name__}: {exc}"


def _parse(raw: str, check: Callable[[dict], list[str]]) -> tuple[dict, list[str]]:
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, [f"The answer is not valid JSON ({exc})."]
    if not isinstance(answer, dict):
        return {}, ["The answer must be one JSON object."]
    try:
        return answer, check(answer)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return answer, [f"The answer has the wrong shape: {type(exc).__name__}: {exc}"]


def _out(call_id: str | None, text: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def _size(items: list[dict]) -> int:
    return len(json.dumps(items)) // 4


def _fold(items: list[dict], keep: int) -> None:
    """Fold the oldest tool outputs until the conversation fits, keeping the last `keep` outputs whole."""
    outputs = [i for i, it in enumerate(items) if it.get("type") == "function_call_output" and it["output"] != FOLDED]
    for i in outputs[:-keep] if keep else outputs:
        items[i] = {**items[i], "output": FOLDED}
        if _size(items) <= CONTEXT_BUDGET * 0.7:
            return


def _withhold(items: list[dict]) -> list[str]:
    """Replace the newest batch of tool outputs with a notice; returns the calls whose output was withheld."""
    names = {it.get("call_id"): f"{it.get('name')} {it.get('arguments', '')[:120]}" for it in items
             if it.get("type") == "function_call"}
    withheld = []
    for i in range(len(items) - 1, -1, -1):
        it = items[i]
        if it.get("type") != "function_call_output":
            if withheld:
                break
            continue
        if it["output"] not in (WITHHELD, FOLDED):
            items[i] = {**it, "output": WITHHELD}
            withheld.append(names.get(it.get("call_id"), "a tool call"))
    return withheld


def _brief(item: dict) -> dict:
    kind = item.get("type")
    if kind == "function_call":
        return {"call": item.get("name"), "args": (item.get("arguments") or "")[:2000]}
    if kind == "message":
        return {"say": " ".join(c.get("text", "") for c in item.get("content") or [])[:2000]}
    return {"type": kind}


def _log(path: Path, entry: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")

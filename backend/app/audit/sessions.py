"""An agent's conversation as a saved session: kept on disk after every turn, resumed after a crash, compacted when
it grows large.

A session is the agent's whole working state: the conversation it sends each turn, and its counters (turns used,
answers refused, filter blocks). It is written to `session.json` after every turn, by writing a new file and renaming
it over the old one, so a crash leaves either the previous turn or this one, never half a file. Running the agent
again continues from the last saved turn instead of starting over.

Compaction keeps each turn small. When the conversation passes its budget, a cheap model reads it and writes a
handoff note: what was read and what it showed, what is established (with file:line), the checks run and their
artifact paths, any candidate findings in full, what is still open, and the next step. The agent then continues from
its original instructions, its task and that note. The note is kept in the session's history, so the trail stays
reviewable. If the note cannot be written, the oldest tool outputs are folded instead, as before.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .azure import Client, ModelError

COMPACT_AT = 45_000  # estimated tokens: past this the conversation is compacted before the next turn
NOTE_TOKENS = 6_000
HEAD = 2  # the developer instructions and the task are never compacted away
NOTE_PROMPT = """You are writing the handoff note for an agent whose conversation is being compacted. The agent will
continue from its original instructions, its task and your note alone, so anything you leave out is lost.

Write it in plain markdown with these sections, using exact repository-relative paths and line numbers:
1. Files read and what each showed that matters for the task.
2. Established facts, each with file:line.
3. Checks run: for each, what it tested, its result, and its artifact path exactly as the tool reported it.
4. Candidate findings so far, in full: every field drafted so far, word for word, so none has to be re-derived.
5. Coverage so far: which assigned units are reviewed, with the paths and checks that support each.
6. Open questions and the exact next steps.

Do not add new analysis. Do not drop a path, line number, artifact path or fingerprint."""


@dataclass
class Session:
    path: Path
    items: list = field(default_factory=list)
    turn: int = 0
    repairs: int = 0
    blocked: int = 0
    warned: bool = False
    compactions: int = 0
    reasoning_dropped: bool = False
    notes: list = field(default_factory=list)  # every handoff note, in order

    @classmethod
    def open(cls, path: Path, start: list[dict]) -> Session:
        """The saved session at `path`, or a new one beginning with `start`."""
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return cls(path=path, **{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (OSError, ValueError, TypeError):
                path.rename(path.with_suffix(".unreadable.json"))  # kept for review, never silently overwritten
        return cls(path=path, items=list(start))

    def save(self) -> None:
        data = {k: v for k, v in asdict(self).items() if k != "path"}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".session-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def resumed(self) -> bool:
        return self.turn > 0


def due(session: Session) -> bool:
    """Compact when the conversation is past its budget and most of that is work after the task: a large task alone
    (the critic reads the whole ledger) is not shrunk by compacting, so it would compact on every turn."""
    return size(session.items) > COMPACT_AT and size(session.items[HEAD:]) > COMPACT_AT // 2


def size(items: list[dict]) -> int:
    """Tokens the conversation counts as, about four characters a token, leaving out encrypted reasoning."""
    visible = [{k: v for k, v in i.items() if k != "encrypted_content"} if isinstance(i, dict) else i for i in items]
    return len(json.dumps(visible)) // 4


def compact(client: Client, session: Session, agent_id: str, model: str = "cheap") -> bool:
    """Replace everything after the instructions and task with a handoff note. False when no note could be written."""
    body = [it for it in session.items[HEAD:] if it.get("type") != "reasoning"]
    if not body:
        return False
    transcript = json.dumps(body)[-400_000:]  # the note model reads the newest part if the history is very long
    try:
        reply = client.respond(model, {"input": [
            {"role": "developer", "content": NOTE_PROMPT},
            {"role": "user", "content": f"The agent's task:\n{_text(session.items[1])}\n\nIts conversation so far "
                                        f"(tool calls and their outputs, as JSON):\n{transcript}"}],
            "reasoning": {"effort": "low"}, "max_output_tokens": NOTE_TOKENS}, f"{agent_id}-compact")
    except ModelError:
        return False
    note = "".join(part.get("text", "") for item in reply.get("output") or [] if item.get("type") == "message"
                   for part in item.get("content") or []).strip()
    if len(note) < 200:
        return False
    session.compactions += 1
    session.notes.append(note)
    session.items = session.items[:HEAD] + [{"role": "user", "content": (
        f"Handoff note {session.compactions}: your conversation was compacted to keep each turn small. This note is "
        f"everything you established so far. Continue from it; re-read a file only if you need its exact text.\n\n"
        f"{note}")}]
    return True


def drop_reasoning(session: Session) -> bool:
    """Remove passed-back reasoning items, once. A resumed session's encrypted reasoning can be refused by the
    provider; the conversation still makes sense without it."""
    if session.reasoning_dropped:
        return False
    session.items = [it for it in session.items if it.get("type") != "reasoning"]
    session.reasoning_dropped = True
    return True


def _text(item: dict) -> str:
    content = item.get("content", "")
    return content if isinstance(content, str) else json.dumps(content)

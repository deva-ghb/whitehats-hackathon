"""Step 2 of a scan: send each escalating suspect, and only that, to a model through the gateway.

With no model reachable, every suspect comes back `unconfirmed` at its Decisions API severity:
the finding is reported, never dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from ..decisions_api.diff import parse_diff
from ..decisions_api.suspect import Decision
from ..services import llm
from .chunk import cut
from .mask import mask_chunk, mask_text
from .prompt import OUTPUT_BUDGET_TOKENS, RESPONSE_FORMAT, SYSTEM, advisory_brief, message
from .verdict import CONFIRMED, UNCONFIRMED, Confirmation, parse_answer, settle

PURPOSE = "triage"  # the gateway purpose whose deployments may confirm findings


@dataclass(frozen=True)
class Reply:
    """What came back from one model call. `text` is empty when no model answered."""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    error: str = ""


Ask = Callable[[str, str], Reply]  # (system, user) -> reply


@dataclass(frozen=True)
class Knowledge:
    """The knowledge base, as confirmation uses it (KB.1): an advisory's facts by id, and the ids a text names that it
    does not hold."""

    advisory: Callable[[str], dict | None]
    unknown_ids: Callable[[str], list[str]]

    @classmethod
    def of(cls, db: Session) -> Knowledge:
        from ..knowledge import lookup

        return cls(lambda advisory_id: lookup.by_id(db, advisory_id), lambda text: lookup.unknown_ids(db, text))


def gateway_ask(db: Session, org_id: int) -> Ask:
    """Ask the company's models for this purpose through the gateway, which records every call."""

    def ask(system: str, user: str) -> Reply:
        result = llm.complete(db, org_id, PURPOSE, system, user, fallback="",
                              max_tokens=OUTPUT_BUDGET_TOKENS, response_format=RESPONSE_FORMAT)
        if result.fallback:
            return Reply("", "", 0, 0, result.error or "No model is connected for confirmation.")
        return Reply(result.text, result.model, result.prompt_tokens, result.completion_tokens)

    return ask


def confirm(decision: Decision, root: Path | str, ask: Ask, *, diff: str | None = None,
            facts: str = "", knowledge: Knowledge | None = None) -> list[Confirmation]:
    """Confirm each escalating suspect of `decision`, judged against the tree at `root`.

    `facts` describe the service and must come from a trusted place (the clone, or the baseline),
    never from the tree under judgement, so a change cannot vouch for itself.
    """
    changes = parse_diff(diff) if diff is not None else None
    confirmations = []
    for suspect in decision.escalating:
        # Secret values never leave for the model (R-051): it is told the kind and the length.
        advisory = advisory_brief(knowledge.advisory(suspect.rule_id)) if knowledge is not None else None
        reply = ask(SYSTEM, message(suspect, mask_chunk(cut(suspect, root, changes)), mask_text(facts), advisory))
        answer = parse_answer(reply.text) if reply.text else None
        error = reply.error
        if reply.text and answer is None:
            error = "The model's answer did not match the confirmation format."
        confirmation = settle(suspect, answer, error=error)
        unknown = knowledge.unknown_ids(confirmation.explanation) if knowledge is not None and confirmation.explanation else []
        if unknown and confirmation.status == CONFIRMED:
            # An advisory id the knowledge base does not hold was made up or misremembered: not shown as confirmed.
            confirmation.status = UNCONFIRMED
            confirmation.error = (f"The model named {', '.join(unknown)}, which the knowledge base does not hold, "
                                  "so this is not shown as confirmed.")
        confirmation.model = reply.model
        confirmation.prompt_tokens = reply.prompt_tokens
        confirmation.completion_tokens = reply.completion_tokens
        confirmations.append(confirmation)
    return confirmations

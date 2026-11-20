"""Turn the model's answer into a confirmation, and keep the Decisions API's severity as the floor.

Pure logic, no I/O. The model may confirm, add context and explain. It can never lower a high or
critical result, and it can never make one disappear: a high or critical suspect the model rejects
stays open at full severity as `disputed`, for a person to decide (R-030).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..decisions_api.suspect import SEVERITIES, Suspect

CONFIRMED = "confirmed"
REJECTED = "rejected"  # medium or low only: recorded as noise
DISPUTED = "disputed"  # the model rejected a high or critical result; it stays open for a person
UNCONFIRMED = "unconfirmed"  # no usable answer from a model; the Decisions API result stands

CATEGORIES = (
    "injection", "access control", "secrets", "vulnerable dependency", "cloud configuration",
    "logging and monitoring", "integrity", "unsafe deserialisation", "error handling", "other",
)
CONFIDENCES = ("low", "medium", "high")
SERIOUS = ("high", "critical")  # only a person can lower these
EXPLANATION_LIMIT = 600  # characters; a confirmation explains, it does not essay


@dataclass(frozen=True)
class ModelAnswer:
    is_confirmed: bool
    category: str
    severity: str
    confidence: str
    explanation: str


@dataclass
class Confirmation:
    """One escalated suspect after confirmation. `severity` is what the finding carries from now on."""

    suspect: Suspect
    status: str
    severity: str
    model_severity: str = ""
    category: str = ""
    confidence: str = ""
    explanation: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""

    @property
    def is_open(self) -> bool:
        """Still a finding a person sees as open. Only a medium or low rejection closes."""
        return self.status != REJECTED

    def to_dict(self) -> dict:
        data = asdict(self)
        data["suspect"] = self.suspect.to_dict()
        return data


def parse_answer(text: str) -> ModelAnswer | None:
    """The model's JSON answer, or None when it does not match the fixed format exactly."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    verdict, category = data.get("verdict"), data.get("category")
    severity, confidence = data.get("severity"), data.get("confidence")
    explanation = data.get("explanation")
    if verdict not in (CONFIRMED, REJECTED) or category not in CATEGORIES:
        return None
    if severity not in SEVERITIES or confidence not in CONFIDENCES or not isinstance(explanation, str):
        return None
    return ModelAnswer(verdict == CONFIRMED, category, severity, confidence,
                       explanation.strip()[:EXPLANATION_LIMIT])


def _higher(first: str, second: str) -> str:
    return max(first, second, key=SEVERITIES.index)


def settle(suspect: Suspect, answer: ModelAnswer | None, *, error: str = "") -> Confirmation:
    """Combine the Decisions API result with the model's answer. The answer can raise severity, never lower it."""
    if answer is None:
        return Confirmation(suspect, UNCONFIRMED, suspect.severity, error=error)
    common = {"model_severity": answer.severity, "category": answer.category,
              "confidence": answer.confidence, "explanation": answer.explanation}
    if answer.is_confirmed:
        return Confirmation(suspect, CONFIRMED, _higher(suspect.severity, answer.severity), **common)
    if suspect.severity in SERIOUS:
        return Confirmation(suspect, DISPUTED, suspect.severity, **common)
    return Confirmation(suspect, REJECTED, suspect.severity, **common)

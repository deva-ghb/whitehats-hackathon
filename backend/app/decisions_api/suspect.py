"""What the Decisions API returns: a verdict and the suspects behind it.

Every engine's result is normalised to the same `Suspect` shape, so the next
step (model confirmation) and the screens never need to know which engine fired.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITIES = ("low", "medium", "high", "critical")

# A fixed score per severity keeps the order stable and explainable; v0 has no
# statistical part yet, so the score says only how bad the rule says it is.
SCORE_BY_SEVERITY = {"low": 10, "medium": 40, "high": 70, "critical": 90}

OPEN = "open"
ACCEPTED = "accepted"
ACCEPTED_PENDING = "accepted_pending"  # a person has been asked to accept it and has not answered yet
NOT_AFFECTED = "not_affected"  # a VEX statement says it does not apply (SCAN.5): reported, never escalated


@dataclass(frozen=True)
class Evidence:
    """Where the weakness is: a file and line range, and the resource address for infrastructure code."""

    path: str
    start_line: int
    end_line: int
    resource: str = ""
    # For a dependency weakness, the package and version as the lockfile pins them ("pypdf2@3.0.1"). Not part of a
    # finding's fingerprint: it lets a fixed registry build of the same package be told apart (REG.1).
    package: str = ""


@dataclass
class Suspect:
    engine: str
    rule_id: str
    title: str
    evidence: Evidence
    severity: str
    cwe: list[str] = field(default_factory=list)
    status: str = OPEN
    note: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r} from {self.engine} rule {self.rule_id}")

    @property
    def score(self) -> int:
        return SCORE_BY_SEVERITY[self.severity]

    @property
    def is_above_low(self) -> bool:
        return self.severity != "low"

    def to_dict(self) -> dict:
        return {**asdict(self), "score": self.score}


@dataclass(frozen=True)
class Acceptance:
    """A finding a person accepted, or has been asked to accept. It stays visible; it only stops escalating."""

    rule_id: str
    path: str
    resource: str
    reason: str
    status: str = ACCEPTED_PENDING

    def covers(self, suspect: Suspect) -> bool:
        return (suspect.rule_id == self.rule_id and suspect.evidence.path == self.path
                and suspect.evidence.resource == self.resource)


@dataclass
class EngineRun:
    engine: str
    image: str
    seconds: float
    error: str = ""


@dataclass
class Decision:
    verdict: str  # "clean" or "suspect"
    suspects: list[Suspect]
    engines: list[EngineRun]
    changed_paths: list[str] | None = None

    @property
    def escalating(self) -> list[Suspect]:
        """Suspects that go on to confirmation: open and above low."""
        return [s for s in self.suspects if s.status == OPEN and s.is_above_low]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "suspects": [s.to_dict() for s in self.suspects],
            "engines": [asdict(e) for e in self.engines],
            "changed_paths": self.changed_paths,
        }


def severity_from_cvss(score: float) -> str:
    """The CVSS v3 bands."""
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"

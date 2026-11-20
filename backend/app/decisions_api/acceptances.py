"""Read the list of findings a person accepted (R-047), so a run can show them as accepted instead of open.

An accepted finding stays in the result and on screen. It only stops escalating. An entry with no named
person, no reason or no date is refused: an acceptance is a decision somebody owns, not a silent skip.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .suspect import ACCEPTED, Acceptance

REQUIRED = ("rule_id", "path", "resource", "reason", "accepted_by", "accepted_on")


class AcceptanceError(ValueError):
    pass


def load_acceptances(path: Path | str) -> tuple[Acceptance, ...]:
    """Acceptances from a YAML file shaped `{accepted: [{rule_id, path, resource, reason, accepted_by, accepted_on}]}`."""
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = document.get("accepted") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise AcceptanceError("The accepted findings file needs an 'accepted' list.")
    accepted = []
    for number, entry in enumerate(entries, start=1):
        missing = [key for key in REQUIRED if not isinstance(entry, dict) or not str(entry.get(key, "")).strip()]
        if missing:
            raise AcceptanceError(f"Accepted finding {number} is missing: {', '.join(missing)}.")
        accepted.append(Acceptance(
            str(entry["rule_id"]).strip(), str(entry["path"]).strip(), str(entry["resource"]).strip(),
            f"{str(entry['reason']).strip()} Accepted by {str(entry['accepted_by']).strip()} "
            f"on {str(entry['accepted_on']).strip()}.",
            status=ACCEPTED,
        ))
    return tuple(accepted)

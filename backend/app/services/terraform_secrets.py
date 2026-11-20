"""Which values in a Terraform state are real secrets, and whether any of them reached an import.

Terraform marks a secret itself: each instance lists `sensitive_attributes`, the paths of the values it
treats as sensitive (an SSM parameter's `value`, a password, a key). Those values, and any string that
looks like a secret on its own, are what must never appear in a change set or a clone.
A reference to a secret is not a secret: an SSM parameter's name, an ARN, a path in a Lambda's
environment. The importer keeps those, because they are what draws the edge from a function to the
parameter it reads.
"""

from __future__ import annotations

from library.secrets import secret_kinds

# Shorter than this and a value matches innocent text by chance; the count of skipped ones is reported.
MIN_LENGTH = 6


def secret_values(state: object) -> tuple[dict[str, str], int]:
    """Each real secret value of a state by where it sits (resource address and attribute path, never the value),
    and how many sensitive values were too short to search for."""
    found: dict[str, str] = {}
    skipped = 0
    for resource in state.get("resources", []) if isinstance(state, dict) else []:
        if not isinstance(resource, dict):
            continue
        for index, instance in enumerate(resource.get("instances") or []):
            where = f"{resource.get('type')}.{resource.get('name')}[{index}]"
            attributes = instance.get("attributes") or {}
            for steps in instance.get("sensitive_attributes") or []:
                value = _follow(attributes, steps)
                for path, text in _strings(value, "/".join(str(step.get("value")) for step in steps)):
                    if len(text) < MIN_LENGTH:
                        skipped += 1
                    else:
                        found[f"{where}/{path}"] = text
            for path, text in _strings(attributes, ""):
                if len(text) >= MIN_LENGTH and secret_kinds(text):
                    found.setdefault(f"{where}/{path}", text)
    return found, skipped


def leaks(state: object, imported: str) -> list[str]:
    """Where in the state a secret value is that appears anywhere in the text of an import. Paths only."""
    return [path for path, value in secret_values(state)[0].items() if value in imported]


def _follow(value: object, steps: object) -> object:
    for step in steps if isinstance(steps, list) else []:
        key = step.get("value") if isinstance(step, dict) else None
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and isinstance(key, int) and 0 <= key < len(value):
            value = value[key]
        else:
            return None
    return value


def _strings(value: object, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [pair for key, item in value.items() for pair in _strings(item, f"{path}/{key}" if path else str(key))]
    if isinstance(value, list):
        return [pair for index, item in enumerate(value) for pair in _strings(item, f"{path}/{index}")]
    return []

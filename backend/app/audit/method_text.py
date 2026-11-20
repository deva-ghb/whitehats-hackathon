"""The method's own words: prompt blocks read from the vendored files in `method/`, copied verbatim into prompts."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

METHOD_DIR = Path(__file__).parent / "method"
ATTACK_CLASSES = "ATTACK-CLASSES.md"
ORDINARY = ("Injection", "Access control", "Resource and file handling", "Cryptography and secrets", "Business logic",
            "Feature abuse and data leakage", "Chained vulnerabilities and trust boundaries", "Wildcard",
            "Obvious things")
COMPANIONS = ("WEB-PROTOCOL-AND-AUTH.md", "CLOUD-AND-DEPLOYMENT.md", "SUPPLY-CHAIN-AND-RELEASE.md",
              "PROTOCOLS-RPC-AND-MESSAGING.md", "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md",
              "DATA-ISOLATION-AND-LIFECYCLE.md", "AI-AND-LLM.md", "CLIENT-SIDE.md", "DESKTOP-MOBILE-AND-LOCAL-IPC.md",
              "MEMORY-SAFETY-AND-BINARY.md")
COMPANION_FRAME = ("Core discipline", "Universal moves", "Validation rules")


@cache
def lines(name: str) -> tuple[str, ...]:
    return tuple((METHOD_DIR / name).read_text().splitlines())


def _heading(line: str) -> tuple[int, str] | None:
    if not line.startswith("#"):
        return None
    level = len(line) - len(line.lstrip("#"))
    text = line[level:].strip()
    return level, text.split(" (", 1)[0].strip()


def _bold_name(line: str) -> str | None:
    if not line.startswith("**"):
        return None
    end = line.find("**", 2)
    return line[2:end].strip() if end > 2 else None


def block(ref: str) -> str | None:
    """The text of `FILE.md#Name`: a heading's section, or a bold-named class up to the next class or heading."""
    name_file, _, name = ref.partition("#")
    if name_file not in (ATTACK_CLASSES, *COMPANIONS) or not name:
        return None
    text = lines(name_file)
    for i, line in enumerate(text):
        h = _heading(line)
        if h and h[1] == name:
            out = [line]
            for nxt in text[i + 1:]:
                nh = _heading(nxt)
                if nh and nh[0] <= h[0]:
                    break
                out.append(nxt)
            return "\n".join(out).strip()
        if _bold_name(line) == name:
            out = [line]
            for nxt in text[i + 1:]:
                if _heading(nxt) or (_bold_name(nxt) and nxt != line):
                    break
                out.append(nxt)
            return "\n".join(out).strip()
    return None


@cache
def companion_classes(name_file: str) -> tuple[str, ...]:
    """The attack classes a companion file names in bold, in order."""
    names = []
    for line in lines(name_file):
        bold = _bold_name(line)
        if bold and bold not in names and not bold.endswith(":"):
            names.append(bold)
    return tuple(names)


def catalogue() -> str:
    """Every attack-class reference a planner may choose, one per line."""
    rows = [f"{ATTACK_CLASSES}#{name}" for name in ORDINARY]
    for f in COMPANIONS:
        rows += [f"{f}#{name}" for name in companion_classes(f) if block(f"{f}#{name}")]
    return "\n".join(rows)


def fenced_after(name_file: str, heading_starts: str) -> str:
    """The first ```text fenced block after the heading that starts with `heading_starts`."""
    text = lines(name_file)
    start = next(i for i, line in enumerate(text) if line.lstrip("#").strip().startswith(heading_starts))
    opened = next(i for i in range(start, len(text)) if text[i].startswith("```"))
    closed = next(i for i in range(opened + 1, len(text)) if text[i].startswith("```"))
    return "\n".join(text[opened + 1:closed])


def recon_prompts() -> dict[str, str]:
    out = {}
    for key, title in (("1a", "Agent 1a"), ("1b", "Agent 1b"), ("1c", "Agent 1c"), ("1d", "Agent 1d")):
        text = lines("RECONNAISSANCE.md")
        start = next(i for i, line in enumerate(text) if line.startswith(f"**{title}"))
        opened = next(i for i in range(start, len(text)) if text[i].startswith("```"))
        closed = next(i for i in range(opened + 1, len(text)) if text[i].startswith("```"))
        out[key] = "\n".join(text[opened + 1:closed])
    return out


def hunting_method() -> str:
    return fenced_after("HUNTING.md", "Core hunting method")


def validation_rules() -> str:
    return fenced_after("HUNTING.md", "Core validation rules")


def hunter_result_contract() -> str:
    text = lines("HUNTING.md")
    start = next(i for i, line in enumerate(text) if line.startswith("## Structured hunter result"))
    end = next(i for i in range(start + 1, len(text)) if text[i].startswith("## "))
    return "\n".join(text[start:end]).strip()


def verifier_prompt() -> str:
    return fenced_after("VALIDATION-AND-REPORTING.md", "Candidate-verifier prompt")


def critic_contract() -> str:
    text = lines("HUNTING.md")
    start = next(i for i, line in enumerate(text) if line.startswith("## Coverage-critic waves"))
    return "\n".join(text[start:start + 30])


@cache
def schema() -> dict:
    return json.loads((METHOD_DIR / "report-schema.json").read_text())


def branch(verdict: str) -> dict:
    for item in schema()["items"]["oneOf"]:
        if item["properties"]["verdict"].get("const") == verdict:
            return item
    raise KeyError(verdict)


def core_principles() -> str:
    text = lines("SKILL.md")
    start = next(i for i, line in enumerate(text) if line.startswith("## Core principles"))
    end = next(i for i in range(start + 1, len(text)) if text[i].startswith("## Full audit workflow"))
    return "\n".join(text[start:end]).strip()

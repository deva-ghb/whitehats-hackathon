"""Every library the app imports is a declared dependency. The server installs only what pyproject.toml declares, so a
library that arrives here through the test tools works on a laptop and stops the app starting on the server (the
release of 2026-10-30 failed on `packaging`, imported by the knowledge base and never declared)."""

from __future__ import annotations

import ast
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

from packaging.requirements import Requirement

BACKEND = Path(__file__).resolve().parents[1]
OURS = {"app", "agent", "__future__"}


def _name(text: str) -> str:
    return text.lower().replace("_", "-")


def test_every_imported_library_is_a_declared_dependency():
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {_name(Requirement(item).name) for item in project["dependencies"]}
    owners = packages_distributions()
    missing = {}
    for path in [*(BACKEND / "app").rglob("*.py"), *(BACKEND / "agent").rglob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top in OURS:
                    continue
                if not {_name(owner) for owner in owners.get(top, [top])} & declared:
                    missing.setdefault(top, str(path.relative_to(BACKEND)))
    assert missing == {}, f"imported but not declared in pyproject.toml: {missing}"

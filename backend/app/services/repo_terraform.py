"""Read the Terraform files of a repository far enough to say what they declare, and where each thing's code lives.

This is not Terraform. It follows variables, locals, module inputs and outputs, and text with `${...}` in it,
and it leaves everything else unknown: a function call, a computed attribute, a value only the provider knows.
Unknown stays unknown (None); nothing is guessed. A variable marked sensitive is never read, even for its default.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import hcl2

_META = ("__is_block__", "__comments__")
_MODULE_SETTINGS = ("source", "version", "count", "for_each", "depends_on", "providers", *_META)
MAX_DEPTH = 40  # references that chain deeper than this are cyclic or not worth following
CLOSURE_LIMIT = 400  # expressions looked at when finding what a resource's code is


def read_terraform(files: dict[str, str], *, account: str | None = None) -> Tree:
    """The Terraform in `files` (path to text, `.tf` only). `account` is the cloud account the repo deploys to, when known."""
    parsed: dict[str, list[dict]] = {}
    unreadable: list[str] = []
    for path, text in sorted(files.items()):
        if not path.endswith(".tf"):
            continue
        try:
            parsed.setdefault(posixpath.dirname(path), []).append(hcl2.loads(text) | {"__file__": path})
        except Exception:  # noqa: BLE001 - the parser raises its own error types; a file it cannot read is reported, not fatal
            unreadable.append(path)
    return Tree(parsed, account, unreadable)


class Tree:
    """Every folder of Terraform in a repository, and the module instances that start from the folders nothing calls."""

    def __init__(self, folders: dict[str, list[dict]], account: str | None, unreadable: list[str]):
        self.account, self.unreadable = account, unreadable
        self.folders = folders
        self.switched_off: list[str] = []  # module calls whose count is 0
        self.not_read: list[str] = []  # module calls whose source is not a folder of this repository

    def roots(self) -> list[Scope]:
        called = {self.module_folder(folder, call) for folder in self.folders for call in _modules(self.folders[folder])
                  .values()}
        return [Scope(self, folder, {}, None, ()) for folder in sorted(self.folders) if folder not in called]

    def module_folder(self, folder: str, call: dict) -> str | None:
        source = call.get("source")
        source = source[1:-1] if isinstance(source, str) and source.startswith('"') else None
        if source is None or not source.startswith(("./", "../")):
            return None
        return posixpath.normpath(posixpath.join(folder, source))

    def scopes(self) -> Iterator[Scope]:
        """Every module instance that will be created, the root ones and each one they call, depth first."""
        def walk(scope: Scope) -> Iterator[Scope]:
            yield scope
            for name in scope.modules:
                child = scope.child(name)
                if child is not None:
                    yield from walk(child)

        for root in self.roots():
            yield from walk(root)

    def region(self) -> str | None:
        """The region the estate is built in: the root module's `region` variable, when it gives one."""
        for scope in self.roots():
            value = scope.variable("region", 0)
            if isinstance(value, str) and value:
                return value
        return None

    def declared(self) -> list[Declared]:
        """Every resource block of every module instance, with the value of its count already applied."""
        found = []
        for scope in self.scopes():
            for (kind, label), body in scope.resources.items():
                if scope.evaluate(body.get("count")) != 0:
                    found.append(Declared(kind, label, scope.path, scope.file_of[(kind, label)], body, scope))
        return found


class Scope:
    """One module instance: its folder, what it was given, and the names it defines."""

    def __init__(self, tree: Tree, folder: str, inputs: dict[str, Any], parent: Scope | None, path: tuple[str, ...]):
        self.tree, self.folder, self.inputs, self.parent, self.path = tree, folder, inputs, parent, path
        self.variables: dict[str, dict] = {}
        self.locals: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}
        self.resources: dict[tuple[str, str], dict] = {}
        self.file_of: dict[tuple[str, str], str] = {}
        self.data: dict[tuple[str, str], dict] = {}
        self.modules = _modules(tree.folders.get(folder, []))
        self._children: dict[str, Scope | None] = {}
        for document in tree.folders.get(folder, []):
            self._collect(document)

    def _collect(self, document: dict) -> None:
        for entry in document.get("variable", []):
            self.variables.update({_unquote(name): body for name, body in entry.items()})
        for entry in document.get("locals", []):
            self.locals.update({name: value for name, value in entry.items() if name not in _META})
        for entry in document.get("output", []):
            self.outputs.update({_unquote(name): body.get("value") for name, body in entry.items()})
        for entry in document.get("resource", []):
            for kind, labelled in entry.items():
                for label, body in labelled.items():
                    key = (_unquote(kind), _unquote(label))
                    self.resources[key], self.file_of[key] = body, document["__file__"]
        for entry in document.get("data", []):
            for kind, labelled in entry.items():
                self.data.update({(_unquote(kind), _unquote(label)): body for label, body in labelled.items()})

    def child(self, name: str) -> Scope | None:
        """The module instance a module call makes, or None when it is off, or its code is not in this repository."""
        if name not in self._children:
            self._children[name] = self._make_child(name)
        return self._children[name]

    def _make_child(self, name: str) -> Scope | None:
        call = self.modules[name]
        folder = self.tree.module_folder(self.folder, call)
        if folder is None or folder not in self.tree.folders:
            self.tree.not_read.append(".".join((*self.path, name)))
            return None
        if self.evaluate(call.get("count")) == 0:
            self.tree.switched_off.append(".".join((*self.path, name)))
            return None
        inputs = {key: value for key, value in call.items() if key not in _MODULE_SETTINGS}
        return Scope(self.tree, folder, inputs, self, (*self.path, name))

    # -- values -------------------------------------------------------------------------

    def evaluate(self, raw: object, depth: int = 0) -> Any:
        """The value of an attribute as the HCL reader gave it, or None when it cannot be known from the files."""
        if depth > MAX_DEPTH:
            return None
        if isinstance(raw, list):
            items = [self.evaluate(item, depth + 1) for item in raw if not isinstance(item, dict) or "__is_block__" not in item]
            return None if any(item is None for item in items) else items
        if isinstance(raw, dict):
            return {key: self.evaluate(value, depth + 1) for key, value in raw.items() if key not in _META}
        if not isinstance(raw, str):
            return raw
        if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            return self.template(raw[1:-1], depth)
        if raw.startswith("${") and raw.endswith("}") and _closes_at_end(raw):
            return self.expression(raw[2:-1], depth)
        return raw

    def template(self, text: str, depth: int = 0) -> str | None:
        """Text with `${...}` pieces in it; None when any piece is unknown."""
        out: list[str] = []
        at = 0
        while at < len(text):
            start = text.find("${", at)
            if start < 0:
                out.append(text[at:])
                break
            if start and text[start - 1] == "$":  # "$${" is a literal "${"
                out.append(text[at:start - 1] + "${")
                at = start + 2
                continue
            out.append(text[at:start])
            end = _matching_brace(text, start + 1)
            if end < 0:
                return None
            piece = self.expression(text[start + 2:end], depth + 1)
            if piece is None or isinstance(piece, (dict, list)):
                return None
            out.append(str(piece).lower() if isinstance(piece, bool) else str(piece))
            at = end + 1
        return "".join(out)

    def expression(self, text: str, depth: int = 0) -> Any:
        """One expression: a reference, a literal, or a choice between two. Anything else is unknown."""
        text = text.strip()
        if depth > MAX_DEPTH or not text:
            return None
        branches = _split_choice(text)
        if branches is not None:
            chosen = self.condition(branches[0], depth + 1)
            return None if chosen is None else self.expression(branches[1] if chosen else branches[2], depth + 1)
        if text.startswith('"') and text.endswith('"') and len(text) >= 2 and _single_literal(text):
            return self.template(text[1:-1], depth + 1)
        if text in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[text]
        number = _number(text)
        if number is not None:
            return number
        return self.reference(text, depth + 1)

    def condition(self, text: str, depth: int) -> bool | None:
        text = text.strip()
        for operator in ("!=", "=="):
            pieces = _split_top(text, operator)
            if pieces is not None:
                left, right = self.expression(pieces[0], depth + 1), self.expression(pieces[1], depth + 1)
                if right is None and pieces[1].strip() == "null":
                    return (left is not None) == (operator == "!=")
                if left is None or right is None:
                    return None
                return (left == right) == (operator == "==")
        if text.startswith("!"):
            inner = self.condition(text[1:], depth + 1)
            return None if inner is None else not inner
        value = self.expression(text, depth + 1)
        return value if isinstance(value, bool) else None

    def reference(self, text: str, depth: int) -> Any:
        parts = text.split(".")
        if any(not part.replace("_", "").replace("-", "").isalnum() for part in parts):
            return None  # an index, a call, an operator: not a plain reference
        head = parts[0]
        if head == "var" and len(parts) == 2:
            return self.variable(parts[1], depth)
        if head == "local" and len(parts) == 2:
            return self.evaluate(self.locals.get(parts[1]), depth + 1)
        if head == "module" and len(parts) == 3:
            child = self.child(parts[1])
            return None if child is None else child.evaluate(child.outputs.get(parts[2]), depth + 1)
        if head == "path" and len(parts) == 2:
            return {"module": self.folder, "root": self.root().folder, "cwd": self.root().folder}.get(parts[1])
        if head == "data" and parts[1:2] == ["aws_caller_identity"] and parts[3:] == ["account_id"]:
            return self.tree.account
        if len(parts) == 3 and (head, parts[1]) in self.resources:
            return self.evaluate(self.resources[(head, parts[1])].get(parts[2]), depth + 1)
        return None

    def variable(self, name: str, depth: int) -> Any:
        declared = self.variables.get(name)
        if declared is None or declared.get("sensitive") is True:
            return None  # a secret never leaves its variable
        if name in self.inputs and self.parent is not None:
            return self.parent.evaluate(self.inputs[name], depth + 1)
        return self.evaluate(declared["default"], depth + 1) if "default" in declared else None

    def resource_attribute(self, kind: str, label: str, key: str) -> Any:
        """One attribute of another resource in this module instance, or None when there is none or it is unknown."""
        return self.evaluate(self.resources.get((kind, label), {}).get(key))

    def root(self) -> Scope:
        return self if self.parent is None else self.parent.root()

    # -- where the code is --------------------------------------------------------------

    def paths_named_by(self, raw: object, seen: set[tuple[int, str]] | None = None) -> set[str]:
        """Every file path that an expression, or anything it refers to, spells out. Paths are relative to the repository."""
        seen = set() if seen is None else seen
        found: set[str] = set()
        queue: list[tuple[Scope, object]] = [(self, raw)]
        while queue and len(seen) < CLOSURE_LIMIT:
            scope, item = queue.pop()
            if isinstance(item, list):
                queue.extend((scope, entry) for entry in item)
                continue
            if isinstance(item, dict):
                queue.extend((scope, value) for key, value in item.items() if key not in _META)
                continue
            if not isinstance(item, str) or (id(scope), item) in seen:
                continue
            seen.add((id(scope), item))
            found.update(path for text in _literals(item) if (path := _as_path(scope.template(text))) is not None)
            queue.extend(scope.depends_on(item))
        return found

    def depends_on(self, raw: str) -> list[tuple[Scope, object]]:
        """What an expression refers to: the expressions that define those things, with the module instance each lives in."""
        out: list[tuple[Scope, object]] = []
        for name in references(raw):
            parts = name.split(".")
            if parts[0] == "var" and len(parts) >= 2 and parts[1] in self.variables:
                if parts[1] in self.inputs and self.parent is not None:
                    out.append((self.parent, self.inputs[parts[1]]))
                elif "default" in self.variables[parts[1]]:
                    out.append((self, self.variables[parts[1]]["default"]))
            elif parts[0] == "local" and len(parts) >= 2:
                out.append((self, self.locals.get(parts[1])))
            elif parts[0] == "module" and len(parts) >= 3 and (child := self.child(parts[1])) is not None:
                out.append((child, child.outputs.get(parts[2])))
                out.extend((self, value) for key, value in self.modules[parts[1]].items() if key not in _MODULE_SETTINGS)
            elif parts[0] == "data" and len(parts) >= 3 and (parts[1], parts[2]) in self.data:
                out.append((self, self.data[(parts[1], parts[2])]))
            elif len(parts) >= 2 and (parts[0], parts[1]) in self.resources:
                out.append((self, self.resources[(parts[0], parts[1])]))
        return [(scope, item) for scope, item in out if item is not None]


@dataclass
class Block:
    """A block of attributes (a resource, or one nested inside it) and the module instance it is read in."""

    body: dict
    scope: Scope

    def value(self, key: str) -> Any:
        return self.scope.evaluate(self.body.get(key))

    def blocks(self, key: str) -> list[Block]:
        return [Block(item, self.scope) for item in self.body.get(key) or [] if isinstance(item, dict)]

    def has(self, key: str) -> bool:
        return key in self.body


class Declared(Block):
    """One `resource` block, with where it is."""

    def __init__(self, kind: str, label: str, module: tuple[str, ...], file: str, body: dict, scope: Scope):
        super().__init__(body, scope)
        self.kind, self.label, self.module, self.file = kind, label, module, file

    @property
    def address(self) -> str:
        """The address Terraform gives it, which is also how a state names it."""
        return ".".join([*(f"module.{name}" for name in self.module), self.kind, self.label])

    def code_paths(self, keys: tuple[str, ...]) -> set[str]:
        """The repository files named by the attributes `keys` (where a package, image or script comes from), and by
        everything those attributes refer to."""
        return self.scope.paths_named_by({key: self.body[key] for key in keys if key in self.body})


# -- text helpers (no pattern matching: plain scans over characters) -----------------------


def _unquote(name: str) -> str:
    return name[1:-1] if len(name) >= 2 and name[0] == name[-1] == '"' else name


def _modules(documents: list[dict]) -> dict[str, dict]:
    return {_unquote(name): body for document in documents for entry in document.get("module", [])
            for name, body in entry.items()}


def _number(text: str) -> int | float | None:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text) if text.replace(".", "", 1).isdigit() else None
    except ValueError:
        return None


def _matching_brace(text: str, opening: int) -> int:
    """The index of the `}` that closes the `{` at `opening`, skipping quoted text; -1 when there is none."""
    depth, quoted, at = 0, False, opening
    while at < len(text):
        char = text[at]
        if char == "\\" and quoted:
            at += 2
            continue
        if char == '"':
            quoted = not quoted
        elif not quoted and char == "{":
            depth += 1
        elif not quoted and char == "}":
            depth -= 1
            if depth == 0:
                return at
        at += 1
    return -1


def _closes_at_end(text: str) -> bool:
    return _matching_brace(text, 1) == len(text) - 1


def _single_literal(text: str) -> bool:
    """True when the quoted text is one string and not two joined by an operator."""
    at = 1
    while at < len(text) - 1:
        if text[at] == "\\":
            at += 2
            continue
        if text.startswith("${", at):
            end = _matching_brace(text, at + 1)
            if end < 0:
                return False
            at = end + 1
            continue
        if text[at] == '"':
            return False
        at += 1
    return True


def _split_top(text: str, operator: str) -> tuple[str, str] | None:
    """Split once at `operator` where it stands outside brackets and quotes."""
    depth, quoted, at = 0, False, 0
    while at < len(text):
        char = text[at]
        if char == "\\" and quoted:
            at += 2
            continue
        if char == '"':
            quoted = not quoted
        elif not quoted and char in "([{":
            depth += 1
        elif not quoted and char in ")]}":
            depth -= 1
        elif not quoted and depth == 0 and text.startswith(operator, at):
            return text[:at], text[at + len(operator):]
        at += 1
    return None


def _split_choice(text: str) -> tuple[str, str, str] | None:
    """`a ? b : c` as (a, b, c), or None when the text is not a choice."""
    asked = _split_top(text, " ? ")
    if asked is None:
        return None
    answered = _split_top(asked[1], " : ")
    return None if answered is None else (asked[0], answered[0], answered[1])


def _literals(text: str) -> list[str]:
    """The bodies of every quoted string in an expression, or the text itself when it is one string."""
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return [text[1:-1]]
    found, at = [], 0
    while at < len(text):
        if text[at] == '"':
            end = at + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            found.append(text[at + 1:end])
            at = end
        at += 1
    return found if found else [text]


def references(text: str) -> list[str]:
    """Names like `var.x`, `local.y`, `module.m.out` or `aws_thing.label.attr` that appear in an expression."""
    names: list[str] = []
    word = []
    for char in [*text, " "]:
        if char.isalnum() or char in "_.-":
            word.append(char)
            continue
        if word:
            joined = "".join(word).strip(".")
            if "." in joined and not joined[0].isdigit():
                names.append(joined)
        word = []
    return names


def _as_path(text: str | None) -> str | None:
    """A file path inside the repository, or None for text that is not one."""
    if text is None or "/" not in text or text.startswith(("/", "arn:", "http")) or "\n" in text or " " in text:
        return None
    path = posixpath.normpath(text)
    return None if path.startswith("..") else path

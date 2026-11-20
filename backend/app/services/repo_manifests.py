"""Read a repository's dependency manifests, lock files and Dockerfiles into projects: what each is written in and uses.

A project is a folder with a manifest. Only the formats named in READERS are read; the rest are reported as not read.
Dependency names are read, never a version constraint's secrets or an index URL's credentials: only names and versions.
"""

from __future__ import annotations

import json
import posixpath
import tomllib
from collections.abc import Callable
from dataclasses import dataclass

# The characters that end a dependency's name in a requirement such as "requests[security]>=2.0; python_version<'3.13'".
_NAME_ENDS = frozenset("<>=!~;[( @,")
# The language of an image's repository name, as the runtime names it (Lambda calls Node "nodejs20.x").
_RUNTIMES = {"python": "python{v}", "node": "nodejs{v}.x", "nodejs": "nodejs{v}.x", "java": "java{v}", "ruby": "ruby{v}",
             "dotnet": "dotnet{v}", "go": "go{v}"}
LOCK_FILES = {"python": ("uv.lock", "poetry.lock", "Pipfile.lock"), "node": ("package-lock.json", "yarn.lock", "pnpm-lock.yaml")}
ECOSYSTEM = {"python": "PyPI", "node": "npm"}  # OSV's name for each language's package index


@dataclass(frozen=True)
class Dockerfile:
    path: str
    base: str  # the image the last stage starts from, with its tag and digest

    @property
    def runtime(self) -> str:
        return image_runtime(self.base)


@dataclass(frozen=True)
class Project:
    """One folder with a manifest."""

    folder: str  # "" for the top of the repository
    manifest: str
    name: str
    language: str
    version: str  # the language version the manifest asks for, as written (">=3.12")
    dependencies: tuple[str, ...]  # direct dependencies, names only
    locked: tuple[str, ...]  # "name==version" for every package the lock file pins, the project itself left out
    lock_file: str = ""
    dockerfile: Dockerfile | None = None
    pinned: tuple[str, ...] = ()  # "name==version" for each direct dependency the manifest pins exactly

    @property
    def packages(self) -> list[str]:
        """Every exact version this project uses, as `<ecosystem>:<name>==<version>` (KB.1 looks each one up)."""
        ecosystem = ECOSYSTEM.get(self.language)
        if ecosystem is None:
            return []
        return sorted({f"{ecosystem}:{item}" for item in (*self.locked, *self.pinned) if item.partition("==")[2]})

    @property
    def stack(self) -> list[str]:
        """The tech stack as short phrases: the language and version, then each direct dependency."""
        language = f"{self.language} {self.version}".strip()
        return [language, *self.dependencies]


def read_projects(paths: frozenset[str], read: Callable[[str], str | None]) -> tuple[list[Project], list[str]]:
    """The projects of a repository, and the manifests found that no reader here understands."""
    projects, skipped = [], []
    for path in sorted(paths):
        name = posixpath.basename(path)
        reader = _READERS.get(name)
        if reader is None:
            if name in _UNREAD:
                skipped.append(path)
            continue
        text = read(path)
        if text is None:
            skipped.append(path)
            continue
        found = reader(path, text, paths, read)
        if found is not None:
            projects.append(found)
    return projects, skipped


def dockerfiles(paths: frozenset[str], read: Callable[[str], str | None]) -> list[Dockerfile]:
    found = []
    for path in sorted(paths):
        name = posixpath.basename(path)
        if name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile"):
            text = read(path)
            base = _last_base(text) if text is not None else ""
            if base:
                found.append(Dockerfile(path, base))
    return found


def image_runtime(image: str) -> str:
    """The runtime a language image carries: `public.ecr.aws/lambda/python:3.12-arm64@sha256:...` is `python3.12`."""
    name = image.partition("@")[0]
    language, _colon, tag = name.rpartition("/")[2].partition(":")
    pattern = _RUNTIMES.get(language.lower())
    version = tag.partition("-")[0]
    return pattern.format(v=version) if pattern and version and version != "latest" else ""


def last_base(text: str) -> str:
    """The image a Dockerfile's last stage starts from (empty for none or `scratch`); the image scan reads it."""
    return _last_base(text)


def _last_base(text: str) -> str:
    """The image named by the last FROM line, which is the image a multi-stage build ends as."""
    base = ""
    for line in _instructions(text):
        words = line.split()
        if words and words[0].upper() == "FROM":
            image = next((word for word in words[1:] if not word.startswith("--")), "")
            base = "" if image.lower() == "scratch" else image
    return base


def _instructions(text: str) -> list[str]:
    """Dockerfile lines with continuations joined and comments dropped."""
    out, current = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        out.append((current + line).strip())
        current = ""
    return out


def exact_pin(requirement: str) -> str | None:
    """`pyyaml==5.3.1` (markers and extras aside) as "pyyaml==5.3.1"; None unless the requirement pins one version."""
    spec = requirement.split(";", 1)[0].strip()
    name = dependency_name(spec)
    _, found, version = spec.partition("==")
    version = version.strip()
    if not found or not name or not version or any(mark in version for mark in (",", "*", "<", ">", "=", "!", " ")):
        return None
    return f"{name}=={version}"


def dependency_name(requirement: str) -> str:
    """The package a requirement names, lower case: `Requests[security]>=2` is `requests`."""
    end = next((at for at, char in enumerate(requirement) if char in _NAME_ENDS), len(requirement))
    return requirement[:end].strip().lower()


# -- readers: each returns a Project, or None when the file holds no project -------------------


def _pyproject(path: str, text: str, paths: frozenset[str], read: Callable[[str], str | None]) -> Project | None:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    project = document.get("project") or (document.get("tool") or {}).get("poetry") or {}
    name = str(project.get("name") or "")
    if not name:
        return None
    asked = project.get("dependencies") or []
    if isinstance(asked, dict):  # poetry lists dependencies by name; "python" is the language, not a package
        version = str(asked.get("python") or "")
        names = sorted(key.lower() for key in asked if key != "python")
    else:
        version = str(project.get("requires-python") or "")
        names = sorted(dependency_name(item) for item in asked if isinstance(item, str))
    pins = tuple(sorted(pin for item in asked if isinstance(item, str) and (pin := exact_pin(item))))
    folder = posixpath.dirname(path)
    lock_path, locked = _python_lock(folder, name, paths, read)
    return Project(folder, path, name, "python", version, tuple(names), locked, lock_path, pinned=pins)


def _requirements(path: str, text: str, paths: frozenset[str], read: Callable[[str], str | None]) -> Project | None:
    folder = posixpath.dirname(path)
    if posixpath.join(folder, "pyproject.toml") in paths:
        return None  # the pyproject is the project; requirements beside it are an export of the same thing
    lines = [line.strip() for line in text.splitlines()]
    wanted = [line for line in lines if line and not line.startswith(("#", "-"))]
    names = sorted(dependency_name(line) for line in wanted)
    pins = tuple(sorted(pin for line in wanted if (pin := exact_pin(line))))
    return Project(folder, path, posixpath.basename(folder) or "project", "python", "", tuple(names), (), pinned=pins)


def _package_json(path: str, text: str, paths: frozenset[str], read: Callable[[str], str | None]) -> Project | None:
    try:
        document = json.loads(text)
    except ValueError:
        return None
    if not isinstance(document, dict) or not document.get("name"):
        return None
    engines = document.get("engines") if isinstance(document.get("engines"), dict) else {}
    dependencies = document.get("dependencies") if isinstance(document.get("dependencies"), dict) else {}
    folder = posixpath.dirname(path)
    lock_path, locked = _node_lock(folder, paths, read)
    return Project(folder, path, str(document["name"]), "node", str(engines.get("node") or ""),
                   tuple(sorted(dependencies)), locked, lock_path)


def _python_lock(folder: str, own_name: str, paths: frozenset[str], read: Callable[[str], str | None]) -> tuple[str, tuple[str, ...]]:
    for name in LOCK_FILES["python"]:
        path = posixpath.join(folder, name)
        text = read(path) if path in paths else None
        if text is None:
            continue
        if name == "Pipfile.lock":
            pinned = _pipfile_lock(text)
            if pinned is None:
                continue
            return path, pinned
        try:
            packages = tomllib.loads(text).get("package") or []
        except tomllib.TOMLDecodeError:
            continue
        pinned = sorted(f"{item['name']}=={item.get('version', '')}" for item in packages
                        if isinstance(item, dict) and item.get("name") and item["name"] != own_name)
        return path, tuple(pinned)
    return "", ()


def _pipfile_lock(text: str) -> tuple[str, ...] | None:
    """Pipfile.lock: JSON with `default` and `develop`, each package's version written `==1.2.3`."""
    try:
        document = json.loads(text)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    found = set()
    for section in ("default", "develop"):
        for name, entry in (document.get(section) or {}).items():
            version = str((entry or {}).get("version") or "") if isinstance(entry, dict) else ""
            if version.startswith("=="):
                found.add(f"{name.lower()}=={version[2:]}")
    return tuple(sorted(found))


def _node_lock(folder: str, paths: frozenset[str], read: Callable[[str], str | None]) -> tuple[str, tuple[str, ...]]:
    """The first Node lock file of the folder: package-lock.json, then yarn.lock, then pnpm-lock.yaml."""
    for name, reader in (("package-lock.json", _package_lock), ("yarn.lock", _yarn_lock), ("pnpm-lock.yaml", _pnpm_lock)):
        path = posixpath.join(folder, name)
        text = read(path) if path in paths else None
        if text is None:
            continue
        pinned = reader(text)
        if pinned is not None:
            return path, tuple(sorted(set(pinned)))
    return "", ()


def _package_lock(text: str) -> list[str] | None:
    try:
        entries = json.loads(text).get("packages") or {}
    except (ValueError, AttributeError):
        return None
    return [f"{key.rpartition('node_modules/')[2]}=={value.get('version', '')}" for key, value in entries.items()
            if key and isinstance(value, dict)]


def _yarn_lock(text: str) -> list[str]:
    """yarn.lock (classic and Berry): an unindented line of specs ending in `:`, then an indented `version` line."""
    found, names = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.rstrip().endswith(":"):
            names = sorted({_spec_name(spec) for spec in line.rstrip()[:-1].split(",") if _spec_name(spec)})
            continue
        stripped = line.strip()
        if names and stripped.startswith("version"):
            version = stripped[len("version"):].lstrip(" :").strip().strip('"')
            found.extend(f"{name}=={version}" for name in names if version)
            names = []
    return found


def _spec_name(spec: str) -> str:
    """`"@babel/core@^7.0.0"` is `@babel/core`; `lodash@npm:^4.17.21` is `lodash`."""
    spec = spec.strip().strip('"')
    name, at, _ = spec[1:].partition("@") if spec.startswith("@") else spec.partition("@")
    return ("@" + name if spec.startswith("@") else name) if at else ""


def _pnpm_lock(text: str) -> list[str] | None:
    """pnpm-lock.yaml: each key under `packages` names a package and version (`/lodash@4.17.21`, `lodash@4.17.21`,
    `/lodash/4.17.21` in old files), with any peer suffix in brackets after it."""
    import yaml

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    found = []
    for key in (document.get("packages") or {}):
        entry = str(key).lstrip("/").split("(", 1)[0]
        body = entry.removeprefix("@")
        name, at, version = body.rpartition("@")
        if at:
            name = ("@" + name) if entry.startswith("@") else name
        else:
            name, _, version = entry.rpartition("/")
        if name and version:
            found.append(f"{name}=={version}")
    return found


_READERS: dict[str, Callable[..., Project | None]] = {
    "pyproject.toml": _pyproject, "requirements.txt": _requirements, "package.json": _package_json,
}
# Manifests other tools use, which the repository's discovery lists but this scan does not read yet.
_UNREAD = frozenset({"go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
                     "Pipfile", "setup.py"})

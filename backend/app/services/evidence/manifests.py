"""The dependencies a delivered manifest names, and the exact version it asks for.

The Verifier's ``grounded`` check (``services.evidence.grounding``) reads the
manifests a delivery wrote or changed — ``requirements*.txt``,
``pyproject.toml``, ``package.json``, ``Cargo.toml`` and ``go.mod`` — and
looks each dependency up at its registry. A delivery that names a package no
registry has, or an exact version that does not exist, is refuted; a manifest
whose dependencies were never looked up is a finding.

An edit's manifest is a fragment (``contracts.edit_blocks.scanned_text``):
``package.json`` and ``requirements`` files are read line by line, TOML with
the standard library when the fragment parses and a section-aware scanner
when it does not. Path, Git and URL dependencies are left out: they name no
registry package.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from typing import Any

from services.contracts import edit_blocks

PYPI = "pypi"
NPM = "npm"
CRATES = "crates"
GO = "go"


@dataclass(frozen=True)
class Dependency:
    """A dependency a delivered manifest names, with an exact version if it pins one."""

    ecosystem: str
    name: str
    version: str | None = None
    path: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.ecosystem, normalize_name(self.ecosystem, self.name)


def normalize_name(ecosystem: str, name: str) -> str:
    """A package name as its registry compares it (PyPI folds ``-_.`` runs)."""
    name = (name or "").strip()
    if ecosystem == PYPI:
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


_REQUIREMENTS_NAME = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$"
)
_REQUIREMENTS_SKIP = ("-", "git+", "http://", "https://", "file:", ".")
_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")
_PEP508_EXACT = re.compile(r"^===?\s*([A-Za-z0-9][A-Za-z0-9._+!-]*)")


def _requirements(content: str, path: str) -> list[Dependency]:
    found = []
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(_REQUIREMENTS_SKIP):
            continue
        if "://" in line or line.startswith(("--", "-r", "-c", "-e")):
            continue
        line = line.split(";", 1)[0].strip()
        match = _REQUIREMENTS_NAME.match(line)
        if not match:
            continue
        name, rest = match.group(1), match.group(2)
        if not rest:
            found.append(Dependency(PYPI, name, None, path))
            continue
        exact = re.match(r"^===?\s*([^,;\s]+)$", rest.strip())
        found.append(Dependency(PYPI, name, exact.group(1) if exact else None, path))
    return found


def _pep508(text: str, path: str) -> Dependency | None:
    """One ``name[extras] specifier ; marker`` string (PEP 508)."""
    text = str(text or "").strip()
    if not text or text.startswith(("-", ".")):
        return None
    match = _PEP508.match(text)
    if not match:
        return None
    name, rest = match.group(1), match.group(2).strip()
    if rest.startswith("@") or "://" in rest:
        return None  # a direct URL: no registry package
    exact = _PEP508_EXACT.match(rest)
    return Dependency(PYPI, name, exact.group(1) if exact else None, path)


def _poetry_value(value: Any, path: str, name: str) -> Dependency | None:
    if name.lower() == "python":
        return None
    if isinstance(value, str):
        spec = value.strip()
        if (
            spec.startswith(("^", "~", ">", "<", "!", "*"))
            or not spec
            or "*" in spec
            or spec.casefold() in ("x", "latest")
        ):
            return Dependency(PYPI, name, None, path)
        # Poetry reads a bare version as exact; ``=1.2.3`` says so explicitly.
        return Dependency(PYPI, name, spec.lstrip("=") or None, path)
    if isinstance(value, list):
        return _poetry_value(value[0], path, name) if value else None
    if isinstance(value, dict):
        if value.get("path") or value.get("git") or value.get("url"):
            return None
        version = value.get("version")
        if isinstance(version, str):
            spec = version.strip()
            exact = spec.startswith("=")
            return Dependency(PYPI, name, spec.lstrip("=") if exact else None, path)
        return Dependency(PYPI, name, None, path)
    return None


def _pyproject_doc(doc: dict[str, Any], path: str) -> list[Dependency]:
    found: list[Dependency] = []
    project = doc.get("project") if isinstance(doc.get("project"), dict) else {}
    for item in project.get("dependencies") or []:
        dep = _pep508(item, path)
        if dep:
            found.append(dep)
    optional = project.get("optional-dependencies") or {}
    if isinstance(optional, dict):
        for items in optional.values():
            for item in items or []:
                dep = _pep508(item, path)
                if dep:
                    found.append(dep)
    groups = doc.get("dependency-groups") or {}
    if isinstance(groups, dict):
        for items in groups.values():
            for item in items or []:
                if isinstance(item, str):
                    dep = _pep508(item, path)
                    if dep:
                        found.append(dep)
    poetry = doc.get("tool", {}).get("poetry", {})
    if isinstance(poetry, dict):
        tables = [poetry.get("dependencies"), poetry.get("dev-dependencies")]
        for group in (poetry.get("group") or {}).values():
            if isinstance(group, dict):
                tables.append(group.get("dependencies"))
        for table in tables:
            if isinstance(table, dict):
                for name, value in table.items():
                    dep = _poetry_value(value, path, str(name))
                    if dep:
                        found.append(dep)
    return found


_POETRY_TABLE = re.compile(
    r"^\s*\[tool\.poetry(?:\.group\.[^.\]]+)?\.(?:dev-)?dependencies\]\s*$"
)
_POETRY_ROW = re.compile(
    r"""^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*(?:"([^"]*)"|\{[^}]*version\s*=\s*"([^"]+)")"""
)
_PROJECT_DEPS_START = re.compile(
    r"^\s*(?:dependencies|optional-dependencies)\s*=\s*\[", re.IGNORECASE
)
_PROJECT_OPTIONAL_TABLE = re.compile(r"^\s*\[project\.optional-dependencies\]\s*$")
_PROJECT_ROW = re.compile(r"""^\s*[A-Za-z0-9_.-]+\s*=\s*\[""")
_PROJECT_DEP = re.compile(r'"([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?[^"]*)"')


def _pyproject_fragment(content: str, path: str) -> list[Dependency]:
    """The dependencies named by an edit's fragment of ``pyproject.toml``."""
    found: list[Dependency] = []
    in_poetry = False
    in_project = False
    collecting = False
    optional = False
    for line in content.splitlines():
        if _POETRY_TABLE.match(line):
            in_poetry, in_project, collecting = True, False, False
            continue
        if _PROJECT_OPTIONAL_TABLE.match(line):
            in_poetry, in_project, collecting, optional = False, True, False, True
            continue
        if line.lstrip().startswith("["):
            in_poetry = False
            in_project = line.strip() == "[project]"
            collecting = False
            optional = False
        if in_poetry:
            match = _POETRY_ROW.match(line)
            if match and match.group(1).lower() != "python":
                dep = _poetry_value(
                    match.group(2)
                    if match.group(2) is not None
                    else {"version": match.group(3) or ""},
                    path,
                    match.group(1),
                )
                if dep:
                    found.append(dep)
            continue
        if not in_project:
            continue
        if _PROJECT_DEPS_START.match(line) or (optional and _PROJECT_ROW.match(line)):
            collecting = True
        if collecting:
            for match in _PROJECT_DEP.finditer(line):
                dep = _pep508(match.group(1), path)
                if dep:
                    found.append(dep)
            if "]" in line:
                collecting = False
    return found


def _pyproject(content: str, path: str) -> list[Dependency]:
    try:
        doc = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, ValueError):
        return _pyproject_fragment(content, path)
    return _pyproject_doc(doc, path)


_NPM_RANGE = re.compile(r"[\^~><=*| ]|^\s*(?:latest|x|X|\*)\s*$")
_NPM_SKIP = ("file:", "link:", "portal:", "workspace:", "git", "http", "npm:")


def _npm_version(spec: Any) -> str | None:
    """The exact version an npm spec pins, or ``None`` for a range/URL."""
    if not isinstance(spec, str):
        return None
    spec = spec.strip()
    if not spec or spec.casefold().startswith(_NPM_SKIP):
        return None
    if spec.startswith("="):
        spec = spec[1:].strip()
    if not spec or _NPM_RANGE.search(spec):
        return None
    if not re.match(r"^\d", spec):
        return None
    return spec


def _package_json(content: str, path: str) -> list[Dependency]:
    try:
        doc = json.loads(content)
    except ValueError:  # an edit's fragment
        doc = {"dependencies": edit_blocks.json_dependency_pairs(content)}
    if not isinstance(doc, dict):
        return []
    found = []
    for key in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        table = doc.get(key)
        if not isinstance(table, dict):
            continue
        for name, spec in table.items():
            if str(spec).strip().casefold().startswith(_NPM_SKIP):
                continue
            found.append(Dependency(NPM, str(name), _npm_version(spec), path))
    return found


_CARGO_SECTION = re.compile(
    r"^\s*\[(?:workspace|target\.[^.\]]+)\.(dev-|build-)?dependencies\]\s*$|"
    r"^\s*\[(dev-|build-)?dependencies\]\s*$"
)
_CARGO_ROW = re.compile(
    r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*(?:"([^"]*)"|\{([^}]*)\})'
)
_CARGO_VERSION = re.compile(r'version\s*=\s*"([^"]+)"')
_CARGO_SKIP = re.compile(r"\b(?:path|git|workspace)\s*=")


def _cargo_value(value: Any, path: str, name: str) -> Dependency | None:
    if isinstance(value, str):
        spec = value.strip()
        exact = spec.startswith("=")
        return Dependency(CRATES, name, spec.lstrip("=") if exact else None, path)
    if isinstance(value, dict):
        if value.get("path") or value.get("git") or value.get("workspace"):
            return None
        version = value.get("version")
        if isinstance(version, str) and version.strip().startswith("="):
            return Dependency(CRATES, name, version.strip().lstrip("="), path)
        return Dependency(CRATES, name, None, path)
    return None


def _cargo_fragment(content: str, path: str) -> list[Dependency]:
    found = []
    in_deps = False
    for line in content.splitlines():
        if _CARGO_SECTION.match(line):
            in_deps = True
            continue
        if line.lstrip().startswith("["):
            in_deps = False
        if not in_deps:
            continue
        match = _CARGO_ROW.match(line)
        if not match or _CARGO_SKIP.search(line):
            continue
        value = (
            match.group(2)
            if match.group(2) is not None
            else {
                "version": (_CARGO_VERSION.search(match.group(3) or "") or [None, ""])[
                    1
                ]
            }
        )
        dep = _cargo_value(value, path, match.group(1))
        if dep:
            found.append(dep)
    return found


def _cargo(content: str, path: str) -> list[Dependency]:
    try:
        doc = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, ValueError):
        return _cargo_fragment(content, path)
    found: list[Dependency] = []

    def read(table: Any) -> None:
        if not isinstance(table, dict):
            return
        for name, value in table.items():
            dep = _cargo_value(value, path, str(name))
            if dep:
                found.append(dep)

    read(doc.get("dependencies"))
    read(doc.get("dev-dependencies"))
    read(doc.get("build-dependencies"))
    workspace = doc.get("workspace")
    if isinstance(workspace, dict):
        read(workspace.get("dependencies"))
    for target in (doc.get("target") or {}).values():
        if isinstance(target, dict):
            read(target.get("dependencies"))
            read(target.get("dev-dependencies"))
            read(target.get("build-dependencies"))
    return found


_GO_REQUIRE = re.compile(r"^\s*([A-Za-z0-9][^\s]+)\s+(v[^\s]+)\s*(?://.*)?$")


def _go_mod(content: str, path: str) -> list[Dependency]:
    found = []
    in_require = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if in_require and stripped.startswith(")"):
            in_require = False
            continue
        body = None
        if in_require:
            body = stripped
        elif stripped.startswith("require "):
            body = stripped[len("require ") :].strip()
        if not body:
            continue
        match = _GO_REQUIRE.match(body)
        if match:
            found.append(Dependency(GO, match.group(1), match.group(2), path))
    return found


def go_module(files: list[dict[str, Any]]) -> str | None:
    """The module path a delivered ``go.mod`` declares, if one was delivered."""
    for file in files:
        if str(file.get("path") or "").rsplit("/", 1)[-1] != "go.mod":
            continue
        for line in edit_blocks.scanned_text(file).splitlines():
            match = re.match(r"^\s*module\s+(\S+)", line)
            if match:
                return match.group(1)
    return None


def dependencies_in(files: list[dict[str, Any]]) -> list[Dependency]:
    """Every dependency the delivered manifests name, one entry per package."""
    found: list[Dependency] = []
    seen: set[tuple[str, str, str | None]] = set()
    for file in files:
        path = str(file.get("path") or "")
        base = path.rsplit("/", 1)[-1].lower()
        if not base:
            continue
        content = edit_blocks.scanned_text(file)
        if base.startswith("requirements") and base.endswith(".txt"):
            entries = _requirements(content, path)
        elif base == "pyproject.toml":
            entries = _pyproject(content, path)
        elif base == "package.json":
            entries = _package_json(content, path)
        elif base == "cargo.toml":
            entries = _cargo(content, path)
        elif base == "go.mod":
            entries = _go_mod(content, path)
        else:
            continue
        for dep in entries:
            mark = (dep.ecosystem, normalize_name(dep.ecosystem, dep.name), dep.version)
            if mark not in seen:
                seen.add(mark)
                found.append(dep)
    return found


def version_matches(ecosystem: str, requested: str, published: str) -> bool:
    """Whether the registry's *published* version answers the exact *requested*."""
    requested, published = requested.strip(), published.strip()
    if ecosystem == PYPI:
        return _python_release(requested) == _python_release(published)
    if ecosystem == GO:
        return requested == published or requested.lstrip("v") == published.lstrip("v")
    return requested.lstrip("=").lstrip("v") == published.lstrip("=").lstrip("v")


def _python_release(version: str) -> tuple[Any, str]:
    """A comparable release core: ``1.2`` and ``1.2.0`` are the same version."""
    text = version.strip().lower()
    text = text.removeprefix("v")
    text = text.split("+", 1)[0]
    match = re.match(r"^(\d+(?:\.\d+)*)(.*)$", text)
    if not match:
        return text, ""
    numbers = tuple(int(part) for part in match.group(1).split("."))
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers = numbers[:-1]
    return numbers, match.group(2)

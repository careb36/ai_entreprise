"""The direct dependencies a project's manifests declare (``project_facts``, issue #766).

For ``workspace.related`` with ``lookup.kind: "dependencies"`` (PROTOCOL.md
§5.10): every ``pyproject.toml``, ``requirements*.txt`` (and ``*.txt`` in a
``requirements/`` folder) and ``package.json`` in the project, found with the
listing's guards, read whole (at most ``MAX_MANIFEST_BYTES``) and hashed.
Each row is a dependency as declared: the manifest, the ecosystem, the name,
the version specifier as written (``""`` when none) and the group (``main``,
``dev``, ``optional:<extra>``, ``group:<name>``, ``peer``).

A direct reference (a URL, a Git or local path) is reported as
``(direct reference)``, never with its URL, which may carry a token. Nothing
is resolved, installed or fetched: a lockfile's versions and the licences are
other questions (the licence is a registry's claim, the backend's research
tool). ``pyproject.toml`` is read with ``tomllib`` (Python 3.11+) or
``tomli`` when installed; without either it is listed with
``error: "no TOML reader"``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import repo_map

MAX_MANIFESTS = 40
MAX_MANIFEST_BYTES = 512 * 1024
DIRECT = "(direct reference)"
_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;#]*)")
_NPM_GROUPS = (
    ("dependencies", "main"), ("devDependencies", "dev"),
    ("peerDependencies", "peer"), ("optionalDependencies", "optional"),
)

try:  # Python 3.11+
    import tomllib as _toml  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - older Pythons
    try:
        import tomli as _toml  # type: ignore[import-not-found,no-redef]
    except ImportError:
        _toml = None  # type: ignore[assignment]


def is_manifest(name: str, dirpath: str) -> bool:
    lowered = name.lower()
    if lowered in ("pyproject.toml", "package.json"):
        return True
    if not lowered.endswith(".txt"):
        return False
    return lowered.startswith("requirements") or Path(dirpath).name.lower() == "requirements"


def requirement(line: str) -> Tuple[str, str] | None:
    """``(name, specifier)`` of one PEP 508 requirement, or ``None``."""
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith("-") or re.match(r"^[a-z+]+://", text):
        return None  # An option, an include, or a bare URL with no name.
    match = _REQUIREMENT.match(text)
    if not match:
        return None
    rest = match.group(3).strip()
    if rest.startswith("@") or "://" in rest:
        return match.group(1), DIRECT
    return match.group(1), rest


def _requirements(text: str) -> List[Dict[str, str]]:
    rows = []
    for line in text.splitlines():
        found = requirement(line)
        if found:
            rows.append({"name": found[0], "version": found[1], "group": "main"})
    return rows


def _poetry_version(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if any(k in value for k in ("git", "url", "path")):
            return DIRECT
        return str(value.get("version") or "")
    return ""


def _pyproject(raw: bytes) -> List[Dict[str, str]]:
    if _toml is None:
        raise ValueError("no TOML reader")
    doc = _toml.loads(raw.decode("utf-8", errors="replace"))
    rows: List[Dict[str, str]] = []

    def pep508(items: Any, group: str) -> None:
        for item in items if isinstance(items, list) else []:
            found = requirement(item) if isinstance(item, str) else None
            if found:
                rows.append({"name": found[0], "version": found[1], "group": group})

    project = doc.get("project") if isinstance(doc.get("project"), dict) else {}
    pep508(project.get("dependencies"), "main")
    for extra, items in (project.get("optional-dependencies") or {}).items():
        pep508(items, f"optional:{extra}")
    for name, items in (doc.get("dependency-groups") or {}).items():
        pep508(items, f"group:{name}")
    poetry = ((doc.get("tool") or {}).get("poetry") or {}) if isinstance(doc.get("tool"), dict) else {}
    tables = [(poetry.get("dependencies"), "main"), (poetry.get("dev-dependencies"), "dev")]
    for name, body in (poetry.get("group") or {}).items():
        tables.append(((body or {}).get("dependencies") if isinstance(body, dict) else None, f"group:{name}"))
    for table, group in tables:
        for name, value in table.items() if isinstance(table, dict) else []:
            if name.lower() != "python":
                rows.append({"name": name, "version": _poetry_version(value), "group": group})
    return rows


def _package_json(raw: bytes) -> List[Dict[str, str]]:
    doc = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(doc, dict):
        raise ValueError("not an object")
    rows = []
    for key, group in _NPM_GROUPS:
        for name, value in (doc.get(key) or {}).items() if isinstance(doc.get(key), dict) else []:
            version = str(value) if isinstance(value, str) else ""
            if re.match(r"^(git\+|git:|github:|https?:|file:|link:)", version) or "://" in version:
                version = DIRECT
            rows.append({"name": str(name), "version": version[:120], "group": group})
    return rows


def read(root: Path, scope: str, walk: Callable[..., Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """The manifests (path, sha256, ecosystem, error?), their rows, and whether the walk was cut."""
    found, cut = walk(root, is_manifest, scope)
    manifests: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    for relative, path in found[:MAX_MANIFESTS]:
        name = relative.rsplit("/", 1)[-1].lower()
        ecosystem = "npm" if name == "package.json" else "pypi"
        raw = repo_map.read_source(path, MAX_MANIFEST_BYTES)
        if raw is None:
            manifests.append({"path": relative, "ecosystem": ecosystem, "error": "not a regular file of at most 512 KiB"})
            continue
        entry: Dict[str, Any] = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "ecosystem": ecosystem}
        try:
            if name == "package.json":
                parsed = _package_json(raw)
            elif name == "pyproject.toml":
                parsed = _pyproject(raw)
            else:
                parsed = _requirements(raw.decode("utf-8", errors="replace"))
        except (ValueError, TypeError, AttributeError, RecursionError) as exc:
            entry["error"] = str(exc)[:120] if str(exc) == "no TOML reader" else "does not parse"
            parsed = []
        entry["count"] = len(parsed)
        manifests.append(entry)
        rows += [{**r, "manifest": relative, "ecosystem": ecosystem} for r in parsed]
    return manifests, rows, cut or len(found) > MAX_MANIFESTS

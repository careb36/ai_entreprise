"""A stack profile per folder, and the commands every delivery runs (F5).

A repository may hold several projects: La Fabrique has ``backend/``
(Poetry), ``frontend/`` (npm) and ``llm-proxy/`` (Poetry), and no manifest
at its root. A **folder** (a root of its own) is a sub-folder with its own
manifest, up to ``MAX_DEPTH`` folders deep, each with its own install and
test commands (``stack_profiles.detect`` run in that folder). Taken from
GitHub Copilot coding agent and OpenHands, which run each workspace's own CI
and checks, and from Factory and OpenAI Codex cloud, which cite every command
they ran (design only, no code).

Which folders count:

- detected: only when the repository's root has no stack of its own, so a
  single-root project behaves exactly as before (its sub-folders are never
  searched); a folder inside another folder is that folder's;
- named by the company (``Company.stack_profile["folders"]``), whatever the
  root holds: ``{"backend": {"stack": "python", "install": "...",
  "test": "..."}}``, each field overriding the detected one, an empty
  command turning that step off for that folder.

A company may also name commands that run on every delivery
(``every_delivery``: a list of commands, or of ``{"command", "folder"}``),
La Fabrique's architecture checks for instance. The rest of the stored
profile is the root's, as before: a profile in the old shape is one root.

Configuration, never evidence: what ran and how it ended is the daemon's
reply (``services/loop/delivery_suites.py``), judged by
``services/evidence/command_runs.py``.
"""

from __future__ import annotations

import os
import re
from typing import Any

from services.workspace import stack_profiles

# The keys of a stored profile that are not the root's own fields.
EXTENSION_KEYS = stack_profiles.EXTENSION_KEYS
# What a folder's profile carries, and what a company may override in it.
FOLDER_COMMANDS = ("install", "test")
FOLDER_FIELDS = ("stack", *FOLDER_COMMANDS)
MAX_DEPTH = 2
MAX_FOLDERS = 20
MAX_EVERY_DELIVERY = 10
# Folders never searched for a project: dependencies, build output, caches,
# and the sample projects a repository keeps for its own tests.
SKIP_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "vendor",
        "coverage",
        "fixtures",
        "testdata",
        "examples",
    }
)
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


# ---------------------------------------------------------------------------
# The stored shape, checked
# ---------------------------------------------------------------------------


def folder_name(raw: Any) -> str:
    """A folder relative to the repository's root, as a POSIX path without
    a leading or trailing slash. Raises ``ValueError``: an absolute path, a
    ``..`` or hidden segment and any character a shell would read are
    refused, so ``cd <folder>`` needs no quoting."""
    if not isinstance(raw, str):
        raise TypeError("a folder must be text")
    name = raw.strip().replace("\\", "/").rstrip("/")
    parts = name.split("/") if name else []
    if not parts or len(name) > 200 or not all(_SEGMENT.match(p) for p in parts):
        raise ValueError(f"not a folder of the repository: {raw!r}")
    return "/".join(parts)


def _command(raw: Any, what: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 500 or "\n" in raw:
        raise ValueError(f"{what} must be one line of text (at most 500 characters)")
    return raw.strip()


def validate_overrides(raw: Any) -> dict[str, Any]:
    """A company's stack profile, checked: the root's fields as
    ``stack_profiles.validate_overrides`` reads them, plus ``folders`` and
    ``every_delivery``. Raises ``ValueError`` or ``TypeError`` with the
    reason."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError("the stack profile must be an object")
    out = stack_profiles.validate_overrides(root_overrides(raw))
    folders = raw.get("folders")
    if folders is not None:
        if not isinstance(folders, dict) or len(folders) > MAX_FOLDERS:
            raise ValueError(
                f"folders must be an object of at most {MAX_FOLDERS} folders"
            )
        checked: dict[str, Any] = {}
        for name, fields in folders.items():
            folder = folder_name(name)
            if not isinstance(fields, dict):
                raise TypeError(f"folders.{folder} must be an object")
            unknown = sorted(set(fields) - set(FOLDER_FIELDS))
            if unknown:
                raise ValueError(f"unknown field for folders.{folder}: {unknown[0]}")
            checked[folder] = stack_profiles.validate_overrides(fields)
        if checked:
            out["folders"] = checked
    commands = raw.get("every_delivery")
    if commands is not None:
        if not isinstance(commands, list) or len(commands) > MAX_EVERY_DELIVERY:
            raise ValueError(
                f"every_delivery must be a list of at most {MAX_EVERY_DELIVERY} commands"
            )
        listed = []
        for entry in commands:
            if isinstance(entry, str):
                entry = {"command": entry}
            if not isinstance(entry, dict) or set(entry) - {"command", "folder"}:
                raise ValueError(
                    "every_delivery holds commands, or objects with command and folder"
                )
            item = {"command": _command(entry.get("command"), "every_delivery")}
            if entry.get("folder"):
                item["folder"] = folder_name(entry["folder"])
            listed.append(item)
        if listed:
            out["every_delivery"] = listed
    return out


def root_overrides(raw: Any) -> dict[str, Any]:
    """The root's own fields of a stored profile: the whole of an old one."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k not in EXTENSION_KEYS}


def every_delivery(raw: Any) -> list[dict[str, str]]:
    """The company's every-delivery commands; a row that no longer passes
    the check runs none rather than stopping the delivery."""
    try:
        return list(validate_overrides(raw).get("every_delivery") or [])
    except (TypeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# Detection and resolution
# ---------------------------------------------------------------------------


def detect_folders(root: str, max_depth: int = MAX_DEPTH) -> dict[str, dict[str, Any]]:
    """Each sub-folder of *root* (up to *max_depth* deep) whose own files
    name a stack, with its detected profile. A folder found is not searched
    further: what is under it is its own."""
    found: dict[str, dict[str, Any]] = {}

    def walk(relative: str, depth: int) -> None:
        if depth > max_depth or len(found) >= MAX_FOLDERS:
            return
        try:
            names = sorted(os.listdir(os.path.join(root, relative)))
        except OSError:
            return
        for name in names:
            if name.startswith(".") or name in SKIP_DIRS:
                continue
            child = f"{relative}/{name}" if relative else name
            if not os.path.isdir(os.path.join(root, child)) or not _SEGMENT.match(name):
                continue
            profile = stack_profiles.detect(os.path.join(root, child))
            if profile is not None:
                found[child] = profile
            else:
                walk(child, depth + 1)

    walk("", 1)
    return found


def resolve_folders(
    detected_root: dict[str, Any] | None,
    detected: dict[str, dict[str, Any]] | None,
    company_overrides: Any,
) -> list[dict[str, Any]]:
    """The folders a delivery may run, sorted: detected ones when the root
    has no stack of its own, and every folder the company names, each with
    ``install``, ``test``, ``manifests`` and where each came from
    (``source``). The deployment's ``DELIVERY_*`` settings are the root's."""
    try:
        named = validate_overrides(company_overrides).get("folders") or {}
    except (TypeError, ValueError):
        named = {}  # Refused when written; an old row never stops a run.
    candidates = dict(detected or {}) if not (detected_root or {}).get("stack") else {}
    folders = []
    for folder in sorted(set(candidates) | set(named)):
        own = named.get(folder) or {}
        profile = dict(candidates.get(folder) or {})
        if own.get("stack") and own["stack"] != profile.get("stack"):
            profile = stack_profiles.template_for(own["stack"]) or profile
        source = {f: "detected" for f in FOLDER_FIELDS if profile.get(f)}
        for field, value in own.items():
            profile[field] = value if value != "" else None
            source[field] = "company"
        resolved = {
            "folder": folder,
            "stack": profile.get("stack"),
            "manifests": list(
                profile.get("manifests") or stack_profiles.manifest_patterns()
            ),
        }
        for field in FOLDER_COMMANDS:
            resolved[field] = profile.get(field) or None
        resolved["source"] = {k: v for k, v in source.items() if resolved.get(k)}
        folders.append(resolved)
    return folders


def folder_of(path: str, folders: list[dict[str, Any]]) -> str | None:
    """The deepest folder holding *path*, or ``None`` for a file in none."""
    relative = (path or "").replace("\\", "/").removeprefix("./")
    best = None
    for entry in folders:
        name = entry["folder"]
        if relative.startswith(f"{name}/") and (best is None or len(name) > len(best)):
            best = name
    return best


def installed_by(folder: dict[str, Any], path: str) -> bool:
    """Whether the folder's install, run in that folder, installs *path*
    (relative to the repository's root): a named command every manifest
    under the folder, a detected one its own stack's in the folder itself."""
    relative = (path or "").replace("\\", "/")
    prefix = f"{folder['folder']}/"
    if not relative.startswith(prefix):
        return False
    inner = {
        "manifests": folder.get("manifests") or (),
        "source": {"install": (folder.get("source") or {}).get("install")},
    }
    return stack_profiles.installed_by(inner, relative[len(prefix) :])


def in_folder(command: str, folder: str | None) -> str:
    """The shell command that runs *command* in *folder* (already checked by
    ``folder_name``): the daemon runs every command in the project's root."""
    return f"cd {folder} && {command}" if folder else command


def changed_paths(evidence: dict[str, Any]) -> list[str] | None:
    """The files the delivery wrote, as the daemon reported them read back
    from disk (``workspace_writes``), never as the agent declared them;
    ``None`` when there is no such report (a sprint's integration)."""
    writes = evidence.get("workspace_writes")
    if not isinstance(writes, list):
        return None
    return [
        w["path"]
        for w in writes
        if isinstance(w, dict)
        and w.get("status") == "written"
        and isinstance(w.get("path"), str)
    ]


def selected(
    folders: list[dict[str, Any]], changed: list[str] | None, root_suite: bool
) -> tuple[list[dict[str, Any]], str]:
    """The folders whose suites a delivery runs, and why.

    A file in a folder runs that folder's suite. A file in no folder (at the
    root, or under a folder with no manifest such as ``docs/``) is covered by
    the root's suite when the root has one, which always runs; without one
    it runs every folder's suite. No report of what changed (a sprint's
    integration) runs every folder's."""
    if not folders:
        return [], ""
    if changed is None:
        return list(folders), "every folder: no report of the files changed"
    owners = {folder_of(path, folders) for path in changed}
    if None in owners and not root_suite:
        return list(folders), "every folder: a file outside them changed"
    touched = [f for f in folders if f["folder"] in owners]
    names = ", ".join(f"{f['folder']}/" for f in touched)
    return touched, (f"the folders the delivery wrote in: {names}" if names else "")

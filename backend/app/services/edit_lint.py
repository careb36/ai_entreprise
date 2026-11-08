"""Lint after an edit: the linter's errors in the same turn as the edit's result.

After ``workspace__edit`` applies a change (the daemon has already checked
that the file still parses), the stack profile's lint command
(``services/stack_profiles.py``, the company's override first) runs on the
edited file through ``workspace.execute``, in the company's sandbox and under
the daemon's command policy, bounded by ``EDIT_LINT_TIMEOUT_SECONDS``. Its
errors go back to the model inside the edit's own result, so it fixes them
before delivering rather than after a review sends the task back. Taken from
Aider's lint-and-fix loop and SWE-agent's linting edits (ideas; no code).

A command that ends in `` .`` (``python -m ruff check .``) is narrowed to the
edited file; another runs as named and only what it says about the file is
shown. A linter that is not installed where the project runs says so, as
does a project whose profile names none (the syntax check is then the only
one). The run is part of the edit's observation (``lint``: the command, the
exit status, whether it was available, the sha256 of its output), never a
verdict: the delivery's own checks decide.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

from config import settings

logger = logging.getLogger("fabrique-task-worker")

LINT_MAX_CHARS = 1500
LINT_MAX_LINES = 40
# The file types each stack's linter reads; another file's edit is not linted.
STACK_SUFFIXES = {
    "python": {".py"},
    "node": {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"},
    "go": {".go"},
    "rust": {".rs"},
    "java-maven": {".java"},
    "java-gradle": {".java", ".kt"},
    "dotnet": {".cs"},
    "php": {".php"},
    "ruby": {".rb"},
}
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")
_UNAVAILABLE = (
    "command not found",
    "no module named",
    "not recognized as an internal or external command",
    "is not installed",
    "executable file not found",
    "missing script",
)
_UNSET = object()


def load_profile(
    bridge: dict[str, Any], company_id: int, root: str, engine: Any
) -> dict[str, Any] | None:
    """The stack profile of *root* with the company's overrides, through the
    worker's loop (the tool loop runs in a thread)."""
    from services import app_check, stack_profiles

    overrides: dict[str, Any] = {}
    if engine is not None:
        from models.company import Company
        from sqlmodel import Session

        with Session(engine) as session:
            company = session.get(Company, company_id)
            overrides = dict(company.stack_profile or {}) if company else {}
    detected = asyncio.run_coroutine_threadsafe(
        app_check._detect_profile(bridge.get("nats"), root), bridge["loop"]
    ).result(60)
    return stack_profiles.resolve(detected, overrides)


def profile_of(box: Any) -> dict[str, Any] | None:
    """The box's profile, loaded once (``box.lint_profile`` is the loader)."""
    cached = getattr(box, "_lint_profile", _UNSET)
    if cached is not _UNSET:
        return cached
    loader = getattr(box, "lint_profile", None)
    profile = None
    if loader is not None:
        try:
            profile = loader()
        except Exception as exc:  # noqa: BLE001 - No profile, no lint.
            logger.warning("Could not read the stack profile for the lint: %s", exc)
    box._lint_profile = profile
    return profile


def narrowed(command: str, path: str) -> tuple[str, bool]:
    """*command* on *path* alone when it ends in `` .``, and whether it was."""
    if command.endswith(" .") and _SAFE_PATH.match(path) and ".." not in path:
        return f"{command[:-2]} {path}", True
    return command, False


def _about(output: str, path: str, narrow: bool) -> str:
    """What the linter said, cut; from a whole-project run, only from the
    first line that names the file on."""
    lines = output.strip().splitlines()
    if not narrow:
        first = next((i for i, line in enumerate(lines) if path in line), None)
        if first is None:
            return ""
        lines = lines[first:]
    text = "\n".join(lines[:LINT_MAX_LINES])
    if len(lines) > LINT_MAX_LINES:
        text += f"\n[{len(lines) - LINT_MAX_LINES} more lines]"
    return text[:LINT_MAX_CHARS]


def after_edit(
    box: Any, path: str, reply: dict[str, Any], extra: dict[str, Any]
) -> str:
    """The text to add to an applied edit's result; ``extra["lint"]`` gets
    the observation. ``""`` when there is nothing to say."""
    if not settings.EDIT_LINT_ENABLED or getattr(box, "lint_profile", None) is None:
        return ""
    profile = profile_of(box) or {}
    command = str(profile.get("lint") or "").strip()
    checked = reply.get("checked") or "no syntax check for this file type"
    if not command:
        extra["lint"] = {"ran": False, "reason": "no lint command"}
        return f"\n\nLint: the project names no lint command; the syntax check ({checked}) is the only check."
    suffix = (
        "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    )
    suffixes = STACK_SUFFIXES.get(str(profile.get("stack") or ""))
    if suffixes is not None and suffix not in suffixes:
        extra["lint"] = {
            "ran": False,
            "reason": f"the {profile.get('stack')} linter does not read {suffix or 'such'} files",
        }
        return ""
    run, narrow = narrowed(command, path)
    record: dict[str, Any] = {"ran": False, "command": run, "narrowed": narrow}
    extra["lint"] = record
    if getattr(box, "sandbox_error", None):
        record["reason"] = box.sandbox_error
        return f"\n\nLint not run: {box.sandbox_error}"
    timeout = float(settings.EDIT_LINT_TIMEOUT_SECONDS)
    request: dict[str, Any] = {
        "workspace_root": box.workspace_root,
        "command": run,
        "timeout_seconds": timeout,
    }
    if getattr(box, "sandbox", None) is not None:
        request["sandbox"] = box.sandbox
    try:
        result = box.requester("workspace.execute", request, timeout + 15)
    except Exception as exc:  # noqa: BLE001 - The edit stands; the lint says why it did not run.
        record["reason"] = f"{type(exc).__name__}: {exc}"[:300]
        return f"\n\nLint could not run: {record['reason']}"
    output = f"{result.get('stdout') or ''}{result.get('stderr') or ''}"
    exit_code = result.get("exit_code")
    record.update(
        {
            "ran": not result.get("refused"),
            "exit_code": exit_code,
            "timed_out": bool(result.get("timed_out")),
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_chars": len(output),
        }
    )
    if isinstance(result.get("sandbox"), dict):
        record["sandbox"] = result["sandbox"]
    if result.get("refused"):
        record["refused"] = True
        return f"\n\nLint refused by the command policy: {result.get('error')}"
    if record["timed_out"]:
        return f"\n\nLint (`{run}`) ran past {timeout:g} s and was stopped."
    lowered = f"{output}\n{result.get('error') or ''}".lower()
    if exit_code == 127 or (
        exit_code not in (0, None) and any(s in lowered for s in _UNAVAILABLE)
    ):
        record["available"] = False
        first = next((line for line in output.splitlines() if line.strip()), "")[:200]
        return f"\n\nLint unavailable: `{run}` is not installed where the project runs ({first or f'exit {exit_code}'})."
    if exit_code is None:
        record["reason"] = str(result.get("error") or "no exit status")[:300]
        return f"\n\nLint could not run: {record['reason']}"
    record["available"] = True
    if exit_code == 0:
        return f"\n\nLint (`{run}`): no problems."
    said = _about(output, path, narrow)
    if not said:
        return (
            f"\n\nLint (`{run}`) exited {exit_code}; nothing it reported names {path}."
        )
    return (
        f"\n\nLint (`{run}`) exited {exit_code}. Fix these before you deliver:\n{said}"
    )

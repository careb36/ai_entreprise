"""``workspace__lint``: the project's lint on demand, narrowed to given paths (issue #766).

The lint after an edit (``edit_lint``) runs only on the file just edited. An
agent in an execution turn could not ask what the linter says about the
files it is about to deliver, or about a file it moved, before the review
did (``docs/design/agent-toolsets.md``, next change 7). This tool runs the
stack profile's lint (``workspace.project_profile.resolved``, the company's
override first), and its type check when the profile names one
(``typecheck``; no built-in stack profile does today), on the ``paths`` the
model gives, the way ``edit_lint`` runs the lint after an edit:

- through ``workspace.execute``, in the company's sandbox and under the
  daemon's command policy, bounded by ``EDIT_LINT_TIMEOUT_SECONDS``, off with
  ``EDIT_LINT_ENABLED``;
- never a command that may rewrite files (``edit_lint.writes_files``: an
  autofix option, a formatter, the package's lint script naming one, or
  shell syntax whose effect cannot be read before it runs);
- a command that ends in `` .`` is narrowed to the paths; another runs as
  named and only what it says about the paths is shown.

The model gives paths, never command text: each is relative to the tool root,
never absolute, never ``..``, never VCS metadata or a file that may hold
secrets (``workspace_git.clean_path``), and only plain path characters, since
it goes into a command line. At most ``MAX_PATHS``; a file the stack's linter
does not read (``edit_lint.STACK_SUFFIXES``) is left out and the reply says so.

Offered only where the step allows family X, run (``developer_tools.toolbox_for`` calls
``offer`` with the profile's loader), never to the read-only toolboxes: it
runs project tooling; and only when the profile, read once when the toolbox
is made, names a lint or type check command (as ``run_tests`` needs a test
command). The lint after an edit reuses that reading. Each call is an observation (``lint``: per command the
command run, whether it was narrowed, the exit status, whether the linter was
available, the sha256 of its output as it was); what the model sees is
redacted (``context_read.redact_secrets``) and cut. The linter's output is
never a verdict: the delivery's own checks decide.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from config import settings

from services.evidence.context_read import redact_secrets
from services.tools import edit_lint, mcp_tools
from services.tools.developer_tool_schemas import SERVER
from services.tools.workspace_git import clean_path

logger = logging.getLogger("fabrique-task-worker")

TOOL = "lint"
NAME = f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{TOOL}"
MAX_PATHS = 20
OUTPUT_MAX_LINES = 60
OUTPUT_MAX_CHARS = 4000
# The profile's commands this tool runs, in order.
COMMANDS = (("lint", "lint"), ("typecheck", "type check"))
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Run the project's linter (and type checker, when the project has "
            "one) on the given files or folders, in the sandbox, and see what "
            "it reports about them. Nothing is rewritten. Use it before you "
            "deliver."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Files or folders relative to the project root (1 to {MAX_PATHS}).",
                },
            },
            "required": ["paths"],
        },
    },
}


def offer(box: Any, loader: Any = None) -> None:
    """Offer the lint on *box* (an execution turn's toolbox) when the lint is
    enabled and the stack profile, read now with *loader*, names a lint or
    type check command, as ``run_tests`` is offered only with a test command.
    The profile is kept for the calls, and for the lint after an edit
    (``edit_lint.profile_of``'s cache) where edits may go."""
    if box is None or not settings.EDIT_LINT_ENABLED or loader is None:
        return
    profile = None
    try:
        profile = loader()
    except Exception as exc:  # noqa: BLE001 - No profile, no lint offered.
        logger.warning("Could not read the stack profile for the lint: %s", exc)
    if not isinstance(profile, dict):
        return
    box._on_demand_profile = profile
    if getattr(box, "lint_profile", None) is not None:
        box._lint_profile = profile  # Read once for both.
    box.lint_offered = any(str(profile.get(key) or "").strip() for key, _ in COMMANDS)


def profile_of(box: Any) -> dict[str, Any] | None:
    """The stack profile read when the lint was offered."""
    return getattr(box, "_on_demand_profile", None)


def schemas(box: Any) -> list[dict[str, Any]]:
    return [SCHEMA] if getattr(box, "lint_offered", False) else []


def clean_paths(value: Any) -> list[str]:
    """The paths, each inside the tool root and safe on a command line; or ValueError."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError(
            "`paths` is required: files or folders relative to the project root"
        )
    if len(value) > MAX_PATHS:
        raise ValueError(f"at most {MAX_PATHS} paths at once")
    out: list[str] = []
    for raw in value:
        path = clean_path(raw)
        if not _SAFE_PATH.match(path) or path.startswith("-"):
            raise ValueError(f"only letters, digits and `_ . / -` in a path: {raw!r}")
        if path not in out:
            out.append(path)
    return out


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _readable(paths: list[str], stack: str) -> tuple[list[str], list[str]]:
    """The paths the stack's linter reads (a folder always), and the others."""
    suffixes = edit_lint.STACK_SUFFIXES.get(stack)
    if suffixes is None:
        return paths, []
    kept = [p for p in paths if not _suffix(p) or _suffix(p) in suffixes]
    return kept, [p for p in paths if p not in kept]


def narrowed(command: str, paths: list[str]) -> tuple[str, bool]:
    """*command* on *paths* when it ends in `` .``, and whether it was."""
    if command.endswith(" .") and paths:
        return f"{command[:-2]} {' '.join(paths)}", True
    return command, False


def _about(output: str, paths: list[str], narrow: bool) -> str:
    """What the linter said, cut; from a whole-project run, only the lines that
    name one of the paths."""
    lines = output.strip().splitlines()
    if not narrow:
        lines = [line for line in lines if any(p in line for p in paths)]
    text = "\n".join(lines[:OUTPUT_MAX_LINES])
    if len(lines) > OUTPUT_MAX_LINES:
        text += f"\n[{len(lines) - OUTPUT_MAX_LINES} more lines]"
    return text[:OUTPUT_MAX_CHARS]


def _one(
    box: Any, label: str, command: str, paths: list[str], profile: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Run one of the profile's commands on *paths*: the text and its record."""
    run, narrow = narrowed(command, paths)
    record: dict[str, Any] = {
        "check": label,
        "ran": False,
        "command": run,
        "narrowed": narrow,
    }
    writes = edit_lint.writes_files(run, profile)
    if writes:
        record["reason"] = f"the command may rewrite files: {writes}"
        return (
            f"{label.capitalize()} not run: `{run}` may rewrite files ({writes}).",
            record,
        )
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
    except Exception as exc:  # noqa: BLE001 - The reply says why it did not run.
        record["reason"] = f"{type(exc).__name__}: {exc}"[:300]
        return f"{label.capitalize()} could not run: {record['reason']}", record
    output = f"{result.get('stdout') or ''}{result.get('stderr') or ''}"
    exit_code = result.get("exit_code")
    record.update(
        ran=not result.get("refused"),
        exit_code=exit_code,
        timed_out=bool(result.get("timed_out")),
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        output_chars=len(output),
    )
    if isinstance(result.get("sandbox"), dict):
        record["sandbox"] = result["sandbox"]
    output = redact_secrets("lint", output)
    if result.get("refused"):
        record["refused"] = True
        return (
            f"{label.capitalize()} refused by the command policy: {result.get('error')}",
            record,
        )
    if record["timed_out"]:
        return (
            f"{label.capitalize()} (`{run}`) ran past {timeout:g} s and was stopped.",
            record,
        )
    lowered = f"{output}\n{result.get('error') or ''}".lower()
    if exit_code == 127 or (
        exit_code not in (0, None) and any(s in lowered for s in edit_lint._UNAVAILABLE)
    ):
        record["available"] = False
        first = next((ln for ln in output.splitlines() if ln.strip()), "")[:200]
        why = first or f"exit {exit_code}"
        text = f"{label.capitalize()} unavailable: `{run}` is not installed where the project runs ({why})."
        return text, record
    if exit_code is None:
        record["reason"] = str(result.get("error") or "no exit status")[:300]
        return f"{label.capitalize()} could not run: {record['reason']}", record
    record["available"] = True
    if exit_code == 0:
        return f"{label.capitalize()} (`{run}`): no problems.", record
    said = _about(output, paths, narrow)
    if not said:
        return (
            f"{label.capitalize()} (`{run}`) exited {exit_code}; nothing it reported names the paths.",
            record,
        )
    return f"{label.capitalize()} (`{run}`) exited {exit_code}:\n{said}", record


def run(box: Any, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call of ``workspace__lint``: the text the model sees; ``extra``
    gets the observation."""
    if not getattr(box, "lint_offered", False):
        raise RuntimeError("the lint is not offered in this turn")
    paths = clean_paths(arguments.get("paths"))
    extra["paths"] = paths
    if getattr(box, "sandbox_error", None):
        extra["lint"] = [{"ran": False, "reason": box.sandbox_error}]
        raise RuntimeError(f"lint not run: {box.sandbox_error}")
    profile = profile_of(box) or {}
    kept, skipped = _readable(paths, str(profile.get("stack") or ""))
    notes = (
        [
            f"Left out (the {profile.get('stack')} linter does not read them): {', '.join(skipped)}."
        ]
        if skipped
        else []
    )
    commands = [
        (label, str(profile.get(key) or "").strip())
        for key, label in COMMANDS
        if str(profile.get(key) or "").strip()
    ]
    records: list[dict[str, Any]] = []
    extra["lint"] = records
    if not commands:
        return "\n".join([*notes, "The project names no lint command."])
    if not kept:
        return "\n".join([*notes, "Nothing to lint."])
    parts = list(notes)
    for label, command in commands:
        text, record = _one(box, label, command, kept, profile)
        parts.append(text)
        records.append(record)
    return "\n\n".join(parts)

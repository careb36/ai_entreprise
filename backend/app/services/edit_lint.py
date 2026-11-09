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

The lint never writes: a file it rewrote would sit outside the edit's
working copy (``workspace.edit.finish`` would neither restore nor deliver
it, and the next edit of it would fail the saved-hash check). A command
naming an autofix or write option (``--fix``, ``--write``, ``-w``,
``rubocop -a``, ``ruff format``, ``cargo fix``, ``dotnet format`` without
``--verify-no-changes``…), or the package's ``lint`` script (with the
``prelint`` and ``postlint`` scripts npm runs around it) when its text names
one (``npm run lint`` with ``eslint . --fix``), is not run, and the result
says why; dry runs (``--fix-dry-run``) are fine. The command is split into
its simple commands (``&&``, ``||``, ``;``, ``|``, line breaks) and each is
judged on its own words, quotes removed (``ruff check '--fix' .``, ``black
--check . && isort .``); a substitution, a here-document, ``eval``, a
nested ``sh -c`` or a shell comment (``black . # --check``, a ``#`` that
starts a word outside quotes) is refused, since what runs cannot be read
from the words alone. What else a company's own wrapper does is not visible here.

What the linter printed is shown with every secret-looking line redacted
(``context_read.redact_secrets``, as the read replies are), before it is
cut; the observation keeps the sha256 of the output as it was.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shlex
from typing import Any

from config import settings

from services.context_read import redact_secrets

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
# Options that make a linter rewrite files, and commands that only format.
_WRITE_FLAGS = {
    "--fix",
    "--fix-only",
    "--unsafe-fixes",
    "--write",
    "-w",
    "--apply",
    "--autofix",
    "--autocorrect",
    "--auto-correct",
    "--autocorrect-all",
    "--auto-correct-all",
    "--in-place",
}
# Short options that write only for the linter that defines them
# (``cargo clippy -- -A lint`` allows a lint; ``rubocop -A`` rewrites).
_WRITE_SHORT = {"rubocop": {"-a", "-A"}}
# Formatters that rewrite unless told to check (``--check``, ``--diff``).
_WRITE_COMMANDS = (
    ("ruff", "format"),
    ("black",),
    ("isort",),
    ("prettier",),
    ("rustfmt",),
    ("cargo", "fmt"),
    ("go", "fmt"),
    ("cargo", "fix"),
)
_SCRIPT_RUNNERS = (
    ("npm", "run", "lint"),
    ("yarn", "lint"),
    ("yarn", "run", "lint"),
    ("pnpm", "lint"),
    ("pnpm", "run", "lint"),
)


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


# Shell syntax whose effect the words alone do not show: what it runs is
# decided when it runs (a substitution, a here-document, a nested shell).
_OPAQUE_TEXT = ("$(", "`", "<<")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_SHELL_C = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")  # -c, -lc, -ec…
_SEPARATORS = {"&&", "||", ";", "|", "&", ";;", "|&", "(", ")"}


def _has_comment(command: str) -> bool:
    """Whether *command* holds a shell comment: a ``#`` that starts a word
    outside quotes (``black . # --check``). One inside quotes or within a
    word (``a#b``) is text."""
    quote = ""
    previous = "\n"
    escaped = False
    for char in command:
        if escaped:
            escaped = False
        elif quote:
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                escaped = True
        elif char == "\\":
            escaped = True
        elif char in "'\"":
            quote = char
        elif char == "#" and (previous.isspace() or previous in ";&|()"):
            return True
        previous = char
    return False


def simple_commands(command: str) -> list[list[str]] | str:
    """*command* split into its simple commands (on ``&&``, ``||``, ``;``,
    ``|``, ``&`` and line breaks), each as the words the shell would pass,
    quotes removed; or why it cannot be read that way."""
    for marker in _OPAQUE_TEXT:
        if marker in command:
            return f"it uses `{marker}`, whose effect cannot be read before it runs"
    if _has_comment(command):
        return "it holds a shell comment, which hides words from the shell but not from this check"
    commands: list[list[str]] = []
    for line in command.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            tokens = list(lexer)
        except ValueError:
            return "its quoting cannot be read"
        current: list[str] = []
        for token in tokens:
            if token in _SEPARATORS or set(token) <= set("&|;()"):
                if current:
                    commands.append(current)
                current = []
            else:
                current.append(token)
        if current:
            commands.append(current)
    for words in commands:
        bare = [w.rsplit("/", 1)[-1] for w in words]
        if "eval" in bare or any(
            w in _SHELLS and any(_SHELL_C.match(o) for o in bare[i + 1 :])
            for i, w in enumerate(bare)
        ):
            return "it runs text through `eval` or a nested shell, which cannot be read before it runs"
    return commands


def _writes(words: list[str]) -> str:
    """Why one simple command may rewrite files, or ``""``."""
    for word in words:
        flag = word.split("=", 1)[0]
        if flag in _WRITE_FLAGS:
            return f"it names `{word}`, which rewrites files"
    bare = [w.rsplit("/", 1)[-1] for w in words if "=" not in w or w.startswith("-")]
    for tool, shorts in _WRITE_SHORT.items():
        if tool in bare and shorts.intersection(bare):
            return (
                f"`{tool} {' '.join(sorted(shorts.intersection(bare)))}` rewrites files"
            )
    for i in range(len(bare)):
        for verb in _WRITE_COMMANDS:
            if tuple(bare[i : i + len(verb)]) == verb:
                if verb[:2] not in (("go", "fmt"), ("cargo", "fix")) and (
                    "--check" in bare or "--diff" in bare
                ):
                    continue
                return f"`{' '.join(verb)}` rewrites files"
        if (
            bare[i : i + 2] == ["dotnet", "format"]
            and "--verify-no-changes" not in bare
        ):
            return "`dotnet format` without --verify-no-changes rewrites files"
    return ""


def writes_files(command: str, profile: dict[str, Any] | None = None) -> str:
    """Why *command* (or the package ``lint`` script it runs, with its
    ``prelint`` and ``postlint``) may rewrite files, or ``""``. Each simple
    command is judged on its own words; syntax that cannot be read safely
    is refused."""
    commands = simple_commands(command)
    if isinstance(commands, str):
        return commands
    script = str((profile or {}).get("lint_script") or "")
    for words in commands:
        why = _writes(words)
        if why:
            return why
        bare = [
            w.rsplit("/", 1)[-1] for w in words if "=" not in w or w.startswith("-")
        ]
        if script and any(tuple(bare[: len(r)]) == r for r in _SCRIPT_RUNNERS):
            why = writes_files(script)
            if why:
                shown = "; ".join(script.splitlines())[:120]
                return f"the package's lint script (`{shown}`): {why}"
    return ""


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
    writes = writes_files(run, profile)
    if writes:
        record["reason"] = f"the lint command may rewrite files: {writes}"
        return (
            f"\n\nLint not run: `{run}` may rewrite files outside the edit ({writes}); "
            f"the syntax check ({checked}) is the only check."
        )
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
    # What the model sees, without the values of secret-looking lines; the
    # hash above is of the output as it was.
    output = redact_secrets(path, output)
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

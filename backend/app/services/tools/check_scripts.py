"""The project's own check scripts through ``workspace__run_tests`` (issue #766).

In sprint 6 (2026-11-24) a QA criterion said "run scripts/check_file_sizes.py".
``run_tests`` ran only the folder's test runner, so the script never ran, the
review sent the task back every time, and QA at last tried to overwrite the
script with four lines that would run. A check the project committed is now
run when the command asks for exactly that and nothing else:

- the command is ``python3 scripts/<name>.py [args]`` (or ``python``): one
  script directly under ``scripts/``, arguments from a short list of plain
  characters (letters, digits, ``_ . = : / + -``), no ``..`` and no absolute
  path, even after an option's ``=``. Nothing a shell (POSIX, cmd or PowerShell) reads as an operator,
  quote, variable, glob or redirection can be in it, so the words are joined
  as they are and the daemon's command policy reads one simple command;
- the script is committed on the default branch (``workspace.git.read_at``,
  never the checkout) and its bytes on disk now are those bytes: the sha256
  is computed here over the committed bytes the daemon sent and compared with
  the daemon's ``workspace.hash`` of the file on disk. A script this task
  edited (an ``edit`` call writes on disk at once), moved or delivered over
  differs, and is not run: a check the task rewrote is not evidence about it
  (``services/evidence/command_runs.rewritten_by_delivery`` refutes the same
  after a delivery);
- it runs through the same ``workspace.execute`` request as a test run, at
  the project root, in the task's sandbox and under the daemon's command
  policy; no install runs first.

Any other command (or a script that fails a condition: the reason goes on the
observation and before the reply) keeps today's answer: the folder's test
command runs instead, and the reply says so (``developer_tool_replies.tests_text``).
The run is an observation the backend made, recorded like a test run; what
the model says about it is a claim, and the delivery's checks still judge it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from typing import Any

from services.tools import developer_tool_replies, test_runs
from services.workspace import daemon_liveness

READ_AT = "workspace.git.read_at"
HASH = "workspace.hash"
VERIFY_TIMEOUT_SECONDS = 30.0
INTERPRETERS = ("python3", "python")
_SCRIPT = re.compile(r"scripts/[A-Za-z0-9_][A-Za-z0-9_-]{0,80}\.py")
_ARGUMENT = re.compile(r"[A-Za-z0-9_.-][A-Za-z0-9_.=:/+-]{0,199}")
# No parent folder, and no absolute path after an option's ``=`` or a drive.
_BANNED = ("..", "=/", ":/", "//")
MAX_ARGUMENTS = 20


def parse(command: Any) -> tuple[str, str, list[str]] | None:
    """``(interpreter, script, arguments)`` when *command* is exactly a check
    script's command line, else ``None``. Words split on single spaces only:
    a tab, a newline or any other character is not accepted."""
    words = str(command or "").strip(" ").split(" ")
    words = [w for w in words if w]
    if len(words) < 2 or words[0] not in INTERPRETERS:
        return None
    script, arguments = words[1], words[2:]
    if not _SCRIPT.fullmatch(script) or len(arguments) > MAX_ARGUMENTS:
        return None
    for word in arguments:
        if not _ARGUMENT.fullmatch(word) or any(b in word for b in _BANNED):
            return None
    return words[0], script, arguments


def _entry(reply: Any, script: str) -> dict[str, Any]:
    """*script*'s entry in a daemon reply's ``files``, or ``{}``."""
    files = reply.get("files") if isinstance(reply, dict) else None
    return next(
        (f for f in files or [] if isinstance(f, dict) and f.get("path") == script),
        {},
    )


def _committed(reply: Any, script: str) -> bytes | None:
    entry = _entry(reply, script)
    if entry.get("status") != "present":
        return None
    try:
        return base64.b64decode(str(entry.get("content_b64") or ""), validate=True)
    except (binascii.Error, ValueError):
        return None


def verify(box: Any, script: str) -> tuple[dict[str, Any], str | None]:
    """The record of *script*'s check, and why it may not run (``None``: it may)."""
    record: dict[str, Any] = {"path": script}
    for subject in (READ_AT, HASH):
        if daemon_liveness.announces(subject) is False:
            return record, daemon_liveness.not_announced_reason(subject)
    root = box.workspace_root
    try:
        base = box.requester(
            READ_AT,
            {"workspace_root": root, "paths": [script]},
            VERIFY_TIMEOUT_SECONDS,
        )
        disk = box.requester(
            HASH, {"workspace_root": root, "paths": [script]}, VERIFY_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - Unverified: not run.
        return record, f"the script could not be checked ({type(exc).__name__}: {exc})"
    committed = _committed(base, script)
    if isinstance(base, dict):
        record.update({"commit": base.get("commit"), "base": base.get("base")})
    if committed is None:
        return record, f"{script} is not a file committed on the default branch"
    record["sha256"] = hashlib.sha256(committed).hexdigest()
    on_disk = _entry(disk, script)
    record["sha256_on_disk"] = on_disk.get("sha256")
    if on_disk.get("status") != "present" or on_disk.get("sha256") != record["sha256"]:
        return record, (
            f"{script} on disk is not the committed file (this task changed it, or "
            "it is not there): a check the task rewrote is not evidence about it"
        )
    return record, None


def attempt(
    box: Any, arguments: dict[str, Any], extra: dict[str, Any]
) -> tuple[str | None, str | None]:
    """``(reply, None)`` when the command was a check script and it ran;
    ``(None, why)`` when it was one that may not run; ``(None, None)`` when
    it was not one. *extra* gets the observation's fields."""
    parsed = parse(arguments.get("command"))
    if parsed is None:
        return None, None
    interpreter, script, words = parsed
    record, why = verify(box, script)
    extra["check_script"] = record
    if why:
        record["refused"] = why
        return None, why
    if box.sandbox_error:
        raise RuntimeError(f"check not run: {box.sandbox_error}")
    # Every word matched the plain characters above: nothing to quote.
    command = " ".join([interpreter, script, *words])
    extra["command"], extra["folder"] = command, test_runs.ROOT
    exit_code, output, timed_out = test_runs.execute(box, command, extra)
    if timed_out:
        return (
            f"The check ran past the time limit and was stopped.\n{output[-2000:]}",
            None,
        )
    where = (
        f" at {str(record.get('commit') or '')[:12]}" if record.get("commit") else ""
    )
    head = f"[ran `{command}`, the committed {script}{where}, unchanged on disk]\n"
    return head + developer_tool_replies.tests_text(
        exit_code, output, box.result_max_chars
    ), None

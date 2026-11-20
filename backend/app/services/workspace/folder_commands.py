"""Running one configured command in a folder of the project, through the daemon.

The folders' suites, their installs and the every-delivery commands
(``stack_roots``) each go to ``workspace.execute`` as
``cd <folder> && <command>``: the daemon runs every command in the project's
root, and a folder name is checked (``stack_roots.folder_name``) so it needs
no quoting. The reply is kept as the daemon sent it, with the sha256 of the
output computed here over the bytes received, never taken from a reply.
Observations only: ``services/evidence/command_runs.py`` judges them.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from services.workspace import sandbox, stack_roots
from services.workspace.project_profile import Context
from services.workspace.workspace_daemon import TEST_OUTPUT_TAIL_CHARS


def command_record(
    kind: str, folder: str | None, command: str, reply: Any
) -> tuple[dict[str, Any], str]:
    """A ``workspace.execute`` reply as kept on the evidence, and its output."""
    reply = reply if isinstance(reply, dict) else {}
    output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    record: dict[str, Any] = {
        "kind": kind,
        "folder": folder or "",
        "command": command,
        "run": stack_roots.in_folder(command, folder),
        "required": True,
        "exit_code": reply.get("exit_code"),
        "timed_out": bool(reply.get("timed_out")),
        "output_tail": output[-TEST_OUTPUT_TAIL_CHARS:],
        "output_bytes": len(output.encode("utf-8", errors="replace")),
        "output_truncated": len(output) > TEST_OUTPUT_TAIL_CHARS,
        # Computed here over what the daemon sent, never taken from a reply.
        "output_sha256": hashlib.sha256(
            output.encode("utf-8", errors="replace")
        ).hexdigest(),
        "reason": reply.get("error"),
    }
    if reply.get("refused"):
        record["refused"] = True
    if record["timed_out"]:
        record["timeout_seconds"] = reply.get("timeout_seconds")
    if isinstance(reply.get("sandbox"), dict):
        record["sandbox"] = reply["sandbox"]
    return record, output


async def execute(
    ctx: Context,
    kind: str,
    folder: str | None,
    command: str,
    box: dict[str, Any] | None,
    unavailable: str | None,
    **extra: Any,
) -> tuple[dict[str, Any], str]:
    """Run *command* in *folder* through the daemon; nothing is run without
    the company's sandbox (``unavailable`` says why there is none)."""
    if box is None:
        record = {
            "kind": kind,
            "folder": folder or "",
            "command": command,
            "required": True,
            "exit_code": None,
            "reason": unavailable or "no sandbox policy",
        }
        return record, ""
    # A suite or an every-delivery command runs as long as a test run may
    # (issue #766); an install names its own bounds.
    extra.setdefault("timeout_seconds", sandbox.test_seconds())
    timeout = float(extra.pop("timeout", sandbox.test_wait()))
    try:
        reply = await ctx.nats.request(
            "workspace.execute",
            {
                "command": stack_roots.in_folder(command, folder),
                "workspace_root": ctx.evidence["workspace_root"],
                "sprint_id": ctx.sprint.get("id"),
                "sandbox": box,
                **extra,
            },
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves it unverified.
        reply = {"error": f"the workspace daemon did not reply ({type(e).__name__})"}
    return command_record(kind, folder, command, reply)


async def sandbox_box(ctx: Context) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return await asyncio.to_thread(
            sandbox.request_for_sprint, ctx.sprint, ctx.engine
        ), None
    except sandbox.PolicyUnavailable as e:
        # Never run agent code under a guessed sandbox: it stays unverified.
        return None, str(e)

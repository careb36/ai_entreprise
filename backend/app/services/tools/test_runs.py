"""What the Developer's ``run_tests`` runs, and the install before it (issue #766).

The tool used to know one command, the root's: a repository whose suites
live in folders (``backend/``, ``frontend/``; ``services/workspace/stack_roots.py``)
was offered no test run at all. ``detect`` now asks the same profile the
delivery uses (``project_profile.Context``) for the root's command and each
folder's, and the tool takes a ``folder``.

In an isolated sandbox (container or bwrap) nothing the project needs is
there until an install ran: the first run of a folder (or the root) in a
toolbox installs it first, with the delivery's own install settings (the
network as ``DELIVERY_INSTALL_NETWORK`` says, ``purpose: install``). The
install's exit status goes on the call's observation; a failed one stops
there and the model sees its output, never a test run that could only fail
on a missing package.

Everything here is a request to the daemon and its reply: what the model
says about a run is a claim; the delivery's own run is the evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from config import settings

from services.workspace import sandbox
from services.workspace.project_profile import Context

ROOT = ""


def detect(
    loop: Any, nats_client: Any, company_id: int, root: str, engine: Any = None
) -> tuple[str | None, dict[str, dict[str, str | None]]]:
    """The root's test command and each folder's ``{test, install}``, from a
    worker thread through the worker's *loop*; nothing when tests are off."""
    if not settings.DELIVERY_TESTS_ENABLED or loop is None:
        return None, {}

    async def _ask() -> tuple[str | None, dict[str, dict[str, str | None]]]:
        ctx = Context(
            nats_client, {"company_id": company_id}, {"workspace_root": root}, engine
        )
        command, _reason = await ctx.test_command()
        folders = {
            f["folder"]: {"test": f.get("test"), "install": f.get("install")}
            for f in await ctx.folders()
            if f.get("test")
        }
        if command:
            profile = await ctx.profile()
            folders[ROOT] = {"test": command, "install": profile.get("install")}
        return command, folders

    return asyncio.run_coroutine_threadsafe(_ask(), loop).result(60)


def in_folder(command: str, folder: str) -> str:
    """*command* run in *folder* (the daemon runs every command at the root)."""
    return f"cd {folder} && {command}" if folder else command


def target(
    plans: dict[str, dict[str, str | None]], folder: Any
) -> tuple[str, dict[str, str | None]]:
    """The folder a call names and its plan; raises with the choices."""
    name = str(folder or "").strip().strip("/")
    if name in plans:
        return name, plans[name]
    choices = ", ".join(f"`{n}`" for n in sorted(plans) if n) or "none"
    if not name and ROOT not in plans:
        raise RuntimeError(f"name the folder whose tests to run: {choices}")
    raise RuntimeError(
        f"no test command for folder `{name}`; the folders are {choices}"
    )


def install_first(
    requester: Callable[[str, dict[str, Any], float], dict[str, Any]],
    root: str,
    box: dict[str, Any] | None,
    folder: str,
    command: str | None,
) -> dict[str, Any] | None:
    """Install *folder* in an isolated sandbox before its first test run; the
    install's record, or ``None`` when none is needed."""
    if not command or not box or box.get("mode", "host") == "host":
        return None
    timeout = float(settings.DELIVERY_INSTALL_TIMEOUT_SECONDS)
    reply = requester(
        "workspace.execute",
        {
            "workspace_root": root,
            "command": in_folder(command, folder),
            "timeout_seconds": timeout,
            "sandbox": {
                **box,
                "network": bool(settings.DELIVERY_INSTALL_NETWORK),
                "purpose": "install",
            },
        },
        timeout + sandbox.REPLY_MARGIN_SECONDS,
    )
    output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    return {
        "folder": folder,
        "command": command,
        "exit_code": reply.get("exit_code"),
        "timed_out": bool(reply.get("timed_out")),
        "error": reply.get("error"),
        "output_tail": output[-2000:],
    }


def install_failed(record: dict[str, Any] | None) -> str | None:
    """The text the model sees when the install did not succeed, else ``None``."""
    if record is None or (record["exit_code"] == 0 and not record["timed_out"]):
        return None
    where = f"in {record['folder']}/" if record["folder"] else "at the root"
    why = (
        "did not finish in time"
        if record["timed_out"]
        else f"exited {record['exit_code']}"
        if record["exit_code"] is not None
        else f"could not run: {record['error']}"
    )
    return (
        f"The install `{record['command']}` {where} {why}, so the tests were not run.\n"
        f"{record['output_tail']}"
    )


def execute(box: Any, command: str, extra: dict[str, Any]) -> tuple[Any, str, bool]:
    """Run *command* at *box*'s root through ``workspace.execute``, in its
    sandbox: ``(exit_code, output, timed_out)``, with the observation's fields
    on *extra*; raises when it could not run (``check_scripts`` runs here too)."""
    request: dict[str, Any] = {
        "workspace_root": box.workspace_root,
        "command": command,
        "timeout_seconds": sandbox.test_seconds(),
    }
    if box.sandbox is not None:
        request["sandbox"] = box.sandbox
    reply = box.requester("workspace.execute", request, sandbox.test_wait())
    exit_code = reply.get("exit_code")
    extra["exit_code"] = exit_code
    if isinstance(reply.get("sandbox"), dict):
        extra["sandbox"] = reply["sandbox"]
    extra["timed_out"] = bool(reply.get("timed_out"))
    output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    if reply.get("error") and exit_code is None and not reply.get("timed_out"):
        raise RuntimeError(str(reply["error"]))
    return exit_code, output, bool(reply.get("timed_out"))

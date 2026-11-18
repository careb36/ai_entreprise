"""A delivery's daemon commands: the workspace's one entry point for the loop.

After the daemon wrote a task's files, the loop has the daemon run, in the
company's sandbox, what the Verifier then judges (``loop/delivery_checks.py``,
``loop/delivery_suites.py``). This module composes each of those requests and
returns what the daemon replied, as observations (ADR 0012, ADR 0018):

- ``install_dependencies``: the install when a manifest changed
  (``dependency_install.py``);
- ``run_tests``: the project's test command through ``workspace.execute``,
  kept as ``evidence["test_run"]`` and ``evidence["ci"]``
  (``workspace_daemon.record_test_run``), with its whole output read back and
  hashed (``run_logs.py``);
- ``folders_to_run``, ``sandbox_box`` and ``run_in_folder``: the suites of the
  folders the delivery wrote in and the every-delivery commands, each as
  ``cd <folder> && <command>`` (``stack_roots.py``, ``folder_commands.py``);
- ``check_app``: the application started in the sandbox and probed
  (``workspace.app.check``, ``vscode-daemon/app_run.py``), bounded by the
  daemon's own phases.

The loop chooses the command (the acceptance tests' verbose flag, QA's browser
smoke tests as the e2e command) and reads the output against the criteria;
nothing here judges a reply.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config import settings

from services.workspace import (
    dependency_install,
    folder_commands,
    run_logs,
    sandbox,
    stack_roots,
    workspace_daemon,
)
from services.workspace.project_profile import Context

# The application check's bounded phases beyond the ready and e2e bounds, as
# ``vscode-daemon/app_run.py`` sets them: ``docker run -d`` (LAUNCH_SECONDS,
# 120 s, a cold image is pulled), the last inspect and probe and the teardown
# (logs, removal), each a runtime call of RUNTIME_CALL_SECONDS (30 s).
APP_OVERHEAD_SECONDS = 120.0 + 4 * 30.0
# The browser probe before a browser e2e command (vscode-daemon/browser_probe.py,
# PROBE_SECONDS) and the full log's upload after the run.
BROWSER_PROBE_SECONDS = 90.0
# The security probe once the application listens (vscode-daemon/security_probe.py,
# PROBE_SECONDS: its own 40 s bound and the in-container margin).
SECURITY_PROBE_SECONDS = 60.0
FULL_LOG_UPLOAD_SECONDS = 60.0
REPLY_MARGIN_SECONDS = dependency_install.REPLY_MARGIN_SECONDS

# The fields of the application check's reply the checks read.
_APP_FIELDS = (
    "started",
    "listening",
    "ready_seconds",
    "ready_timeout_seconds",
    "exit_code",
    "probe",
    "e2e",
    "log",
    "stopped",
    "sandbox",
    "refused",
    "browser_probe",
    "security_probe",
)


async def install_dependencies(ctx: Context) -> None:
    """Install the dependencies when a manifest the delivery wrote changed;
    the reply goes on the evidence (``dependency_install.install_if_needed``)."""
    await dependency_install.install_if_needed(ctx)


async def run_tests(ctx: Context, command: str) -> str | None:
    """Run *command*, the project's tests, through the daemon in the company's
    sandbox; ``evidence["test_run"]`` and ``evidence["ci"]`` record the reply
    and its full log. Returns the output received, or ``None`` when nothing
    ran (no sandbox policy, a silent daemon: ``test_run`` says why)."""
    evidence = ctx.evidence
    try:
        box = await asyncio.to_thread(
            sandbox.request_for_sprint, ctx.sprint, ctx.engine
        )
    except sandbox.PolicyUnavailable as e:
        # Never run agent code under a guessed sandbox: the tests stay unverified.
        evidence["test_run"] = {"command": command, "reason": str(e)}
        return None
    full_log = run_logs.request_spec(ctx.nats)
    try:
        result = await ctx.nats.request(
            "workspace.execute",
            {
                "command": command,
                "workspace_root": evidence["workspace_root"],
                "sprint_id": ctx.sprint["id"],
                # Agent-written code: run as the company's sandbox says.
                "sandbox": box,
                **run_logs.request_field(full_log),
            },
            timeout=settings.DELIVERY_TEST_TIMEOUT_SECONDS
            + (FULL_LOG_UPLOAD_SECONDS if full_log.get("bucket") else 0),
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves the tests unverified.
        evidence["test_run"] = {
            "command": command,
            "reason": f"the workspace daemon did not reply ({type(e).__name__})",
        }
        return None
    workspace_daemon.record_test_run(evidence, command, result)
    evidence["test_run"]["full_log"] = await run_logs.observe(
        ctx.nats, full_log, result
    )
    reply = result if isinstance(result, dict) else {}
    return f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"


async def folders_to_run(
    ctx: Context, root_command: str | None
) -> tuple[list[dict[str, Any]], str]:
    """The folders whose suites the delivery runs, and why
    (``stack_roots.selected``): the files are the daemon's report of what it
    wrote, never the agent's list."""
    return stack_roots.selected(
        await ctx.folders(),
        stack_roots.changed_paths(ctx.evidence),
        bool(root_command),
    )


async def sandbox_box(ctx: Context) -> tuple[dict[str, Any] | None, str | None]:
    """The company's sandbox for the delivery's commands, or ``None`` and why."""
    return await folder_commands.sandbox_box(ctx)


async def run_in_folder(
    ctx: Context,
    kind: str,
    folder: str | None,
    command: str,
    box: dict[str, Any] | None,
    unavailable: str | None,
) -> tuple[dict[str, Any], str]:
    """Run *command* in *folder* through the daemon: the record kept in
    ``evidence["command_runs"]`` and the output received
    (``folder_commands.execute``)."""
    return await folder_commands.execute(ctx, kind, folder, command, box, unavailable)


def app_record(reply: Any) -> dict[str, Any]:
    """The application check's reply, kept with only the fields the checks read."""
    reply = reply if isinstance(reply, dict) else {}
    record = {k: reply[k] for k in _APP_FIELDS if k in reply}
    if reply.get("error"):
        record["reason"] = reply["error"]
    return record


async def check_app(
    ctx: Context,
    profile: dict[str, Any],
    box: dict[str, Any],
    e2e: str | None,
    browser: bool,
) -> tuple[dict[str, Any], bool]:
    """Start the application with *profile*'s command in *box*, probe it, run
    *e2e* when given (after a browser probe when *browser*) and stop it.

    Returns the record for ``evidence["app_run"]`` and whether the daemon
    replied: its kept fields, the probes asked for and the full log; without
    a reply, only ``reason``."""
    ready = float(settings.DELIVERY_APP_READY_TIMEOUT_SECONDS)
    e2e_timeout = float(settings.DELIVERY_E2E_TIMEOUT_SECONDS)
    full_log = run_logs.request_spec(ctx.nats)
    try:
        reply = await ctx.nats.request(
            "workspace.app.check",
            {
                "workspace_root": ctx.evidence["workspace_root"],
                "start_command": profile.get("start"),
                "port": int(profile["port"]),
                "probe_path": profile.get("probe_path") or "/",
                "e2e_command": e2e,
                "ready_timeout_seconds": ready,
                "e2e_timeout_seconds": e2e_timeout,
                "sandbox": box,
                "sprint_id": ctx.sprint.get("id"),
                "security_probe": True,
                **({"browser_probe": True} if browser else {}),
                **run_logs.request_field(full_log),
            },
            timeout=ready
            + (e2e_timeout if e2e else 0)
            + APP_OVERHEAD_SECONDS
            + SECURITY_PROBE_SECONDS
            + (BROWSER_PROBE_SECONDS if browser else 0)
            + (FULL_LOG_UPLOAD_SECONDS if full_log.get("bucket") else 0)
            + REPLY_MARGIN_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves it unverified.
        return {
            "reason": f"the workspace daemon did not reply ({type(e).__name__})"
        }, False
    record = app_record(reply)
    # Asked for on every check: a reply without it is not observed, never clean.
    record["security_probe_requested"] = True
    if browser:
        record["browser_probe_requested"] = True
    record["full_log"] = await run_logs.observe(
        ctx.nats, full_log, reply, "workspace.app.check"
    )
    return record, True

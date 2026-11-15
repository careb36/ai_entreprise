"""Running a workspace command on the host shell, and describing how it ended.

Split out of ``daemon.py`` so the NATS handlers stay readable and the daemon
keeps to the repository's file-size ratchet. The module holds no policy: it
arranges the environment, picks the shell and reports the exit — including the
case where there is no exit, because the command ran past its wall clock.

That last distinction is the point of ``timed_out``. The backend verifies what a
producer says it did, and "the suite hung" is a result, not a missing one.

Every ``workspace.execute`` passes through ``execute_shell_command``: the
command policy first, then the sandbox's rules (``sandbox.normalize``) whether
or not the request named a sandbox, so the daemon's
``FABRIQUE_SANDBOX_REQUIRED`` reaches every command; only then the host shell
(``run_on_host``).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import command_policy

logger = logging.getLogger("fabrique-daemon.shell")

IS_WINDOWS = os.name == "nt"

# Wall clock before a workspace command is killed. Kept under the backend's
# DELIVERY_TEST_TIMEOUT_SECONDS (130s) so this side, not the NATS client,
# reports the timeout and the caller learns the command hung.
COMMAND_TIMEOUT_SECONDS = 120
# The longest a request may ask for (``timeout_seconds``): a dependency
# install takes longer than a test run, never without a bound.
MAX_COMMAND_TIMEOUT_SECONDS = 900


def bounded_timeout(requested: Any) -> int:
    """The request's ``timeout_seconds`` within 1 and the maximum; the
    default when it names none (or nothing readable)."""
    try:
        seconds = int(float(requested))
    except (TypeError, ValueError):
        return COMMAND_TIMEOUT_SECONDS
    return min(max(seconds, 1), MAX_COMMAND_TIMEOUT_SECONDS)


def build_command_env() -> dict[str, str]:
    """Provide a stable host-like environment for assistant CLI execution."""
    env = os.environ.copy()
    home = env.get("HOME") or str(Path.home())
    env["HOME"] = home
    if IS_WINDOWS:
        return env

    nvm_root = Path(home) / ".nvm" / "versions" / "node"
    nvm_bins = sorted(nvm_root.glob("*/bin"), reverse=True) if nvm_root.exists() else []

    path_entries = [
        *(str(path) for path in nvm_bins),
        os.path.join(home, ".local", "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(entry for entry in path_entries if entry)
    return env


def build_shell_command(command: str) -> list[str]:
    """Run commands through the user's shell when available instead of bare /bin/sh."""
    configured_shell = os.getenv("FABRIQUE_EXEC_SHELL")
    if IS_WINDOWS:
        shell_path = configured_shell or os.environ.get("COMSPEC", "cmd.exe")
        if Path(shell_path).stem.lower() in {"powershell", "pwsh"}:
            return [shell_path, "-NoProfile", "-NonInteractive", "-Command", command]
        return [shell_path, "/d", "/s", "/c", command]
    shell_path = configured_shell or (
        "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh"
    )
    if shell_path.endswith("zsh"):
        return [shell_path, "-dfc", command]
    if shell_path.endswith("bash"):
        return [shell_path, "--noprofile", "--norc", "-c", command]
    return [shell_path, "-c", command]


def is_assistant_command(command: str) -> bool:
    """A console assistant's run: its output is advice, not a status line."""
    normalized = (command or "").lower()
    return "copilot" in normalized or "claude" in normalized


def summarize_command_result(command: str, result: dict[str, Any]) -> str:
    """One Glassroom line for a finished workspace command."""
    command_preview = " ".join((command or "").strip().split())[:180]
    exit_code = result.get("exit_code", -1)
    output = (result.get("stdout") or result.get("stderr") or result.get("error") or "").strip()
    if result.get("success"):
        return f"Workspace command finished successfully (exit {exit_code}): {command_preview}"
    if output:
        return f"Workspace command failed (exit {exit_code}): {output[:240]}"
    return f"Workspace command failed (exit {exit_code}): {command_preview}"


def _text(data: Any) -> str:
    """Output captured before a timeout: text, or bytes on some platforms."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _with_full(result: dict[str, Any], keep_full: bool) -> dict[str, Any]:
    """A host run captured each stream whole: kept for the full log when asked."""
    if keep_full:
        import run_logs

        run_logs.keep(result, "stdout", run_logs.spool_of(result.get("stdout")))
        run_logs.keep(result, "stderr", run_logs.spool_of(result.get("stderr")))
    return result


def execute_shell_command(
    command: str, cwd: str = ".", sandbox: Any = None, policy: Any = None, timeout: Any = None,
    keep_full: bool = False,
) -> dict[str, Any]:
    """Execute a shell command and return a structured result.

    Args:
        command: Shell command string.
        cwd:     Working directory for the command.
        sandbox: The request's ``sandbox``. Without one the request reads as
                 ``{"mode": "host"}``, so the daemon's
                 ``FABRIQUE_SANDBOX_REQUIRED`` applies to it as well.
        policy:  The request's ``command_policy``; with it, or with the
                 daemon's ``FABRIQUE_COMMAND_DENY``, a command the policy
                 refuses is not run (``command_policy.py``) and the result
                 says ``refused`` with the reason.
        timeout: The request's ``timeout_seconds`` (``bounded_timeout``).
        keep_full: Keep each stream whole (up to the full log's cap) under
                 ``run_logs.KEY`` for the caller to upload (``run_logs.py``).

    Returns:
        Dict with keys ``success`` (bool), ``stdout``, ``stderr``,
        ``exit_code`` — or ``{"success": False, "error": ...}`` on exception.
        A command killed by the timeout keeps ``timed_out: True`` and the output
        captured before the kill, so the caller can tell "hung" from "ran and
        failed".
    """
    seconds = bounded_timeout(timeout)
    refused = command_policy.refusal(command, policy)
    if refused:
        logger.warning(f"Not running {command!r}: {refused}")
        return {"success": False, "refused": True, "error": refused}
    # Every command goes through the sandbox's rules (``sandbox.py``), the
    # daemon's own floor included: a request without ``sandbox`` is a
    # ``host`` request, which ``FABRIQUE_SANDBOX_REQUIRED`` raises to its mode
    # (issue #525). A mode that cannot run is an error, never a quiet run on
    # the host.
    import sandbox as sandbox_runner

    asked = sandbox is not None
    request = sandbox if asked else {"mode": "host"}
    try:
        box = sandbox_runner.normalize(request)
        if box["mode"] != "host":
            return sandbox_runner.run_sandboxed(command, cwd, request, seconds, keep_full)
    except sandbox_runner.SandboxUnavailable as exc:
        return {"success": False, "error": f"sandbox unavailable: {exc}"}
    result = run_on_host(command, cwd, seconds, keep_full)
    if asked:
        # Only a request that asked is told what was applied on the host; one
        # that did not gets the reply it always got. A run the floor moved
        # off the host always carries its report (``run_sandboxed``).
        result["sandbox"] = sandbox_runner.report(box)
    return result


def run_on_host(command: str, cwd: str, seconds: int, keep_full: bool = False) -> dict[str, Any]:
    """*command* in the host shell; called only by ``execute_shell_command``,
    once the command policy and the sandbox's rules leave it on the host."""
    logger.info(f"Executing: {command} in {cwd}")
    try:
        result = subprocess.run(
            build_shell_command(command),
            cwd=cwd,
            check=False,
            env=build_command_env(),
            capture_output=True,
            text=True,
            timeout=seconds,
        )
        return _with_full({
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }, keep_full)
    except subprocess.TimeoutExpired as e:
        return _with_full({
            "success": False,
            "stdout": _text(e.output),
            "stderr": _text(e.stderr),
            "timed_out": True,
            "timeout_seconds": seconds,
            "error": f"command timed out after {seconds}s",
        }, keep_full)
    except Exception as e:  # noqa: BLE001 - a command that cannot start must not kill the daemon
        return {"success": False, "error": str(e)}

"""The sandbox the daemon reports it applied to a test run, read as a check.

``check_sandbox`` reads ``evidence["test_run"]["sandbox"]``, the daemon's
observation, and writes the Verifier's ``sandbox`` record (ADR 0013): a pure
function of the reply's shape, so the evidence reads it without importing the
workspace context. ``services/sandbox.py`` (workspace) re-exports it.
"""

from __future__ import annotations

from typing import Any


def check_sandbox(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's ``sandbox`` check: where the tests ran, as the daemon
    reported it. A record, never the verdict; absent without a test run."""
    run = (evidence or {}).get("test_run") or {}
    if not run.get("command"):
        return None
    applied = run.get("sandbox")
    if (
        run.get("exit_code") is None
        and not run.get("timed_out")
        and not isinstance(applied, dict)
    ):
        return None  # Nothing ran: tests_run says why, there is nowhere to report.
    if not isinstance(applied, dict):
        return {
            "name": "sandbox",
            "outcome": "pass",
            "isolated": None,
            "finding": True,
            "observation": (
                "the daemon reported no sandbox, so where the tests ran and "
                "whether they were isolated is unknown"
            ),
        }
    mode = applied.get("mode")
    forced = " (the daemon's own floor)" if applied.get("forced_by_daemon") else ""
    if mode == "host" or not applied.get("isolated"):
        return {
            "name": "sandbox",
            "outcome": "pass",
            "isolated": False,
            "finding": True,
            "sandbox": applied,
            "observation": (
                "ran on the host, unisolated: the agent-written code had the "
                "daemon's user, files, network and environment"
            ),
        }
    parts = [f"ran in {mode}{forced}"]
    if mode == "container":
        parts.append(
            f"{applied.get('runtime') or 'container'} image {applied.get('image')}"
        )
    parts.append(f"network {applied.get('network')}")
    if applied.get("cpus"):
        parts.append(f"{applied['cpus']:g} CPUs")
    if applied.get("memory_mb"):
        parts.append(f"{applied['memory_mb']} MB")
    if applied.get("pids_limit"):
        parts.append(f"{applied['pids_limit']} processes")
    if applied.get("capabilities"):
        parts.append(f"capabilities {applied['capabilities']}")
    if applied.get("limits"):
        parts.append(str(applied["limits"]))
    env = applied.get("env") or []
    parts.append(f"environment: {', '.join(env)}" if env else "no environment passed")
    return {
        "name": "sandbox",
        "outcome": "pass",
        "isolated": True,
        "finding": False,
        "sandbox": applied,
        "observation": "; ".join(parts),
    }

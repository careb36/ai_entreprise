"""The Verifier's ``console_run`` record of a console producer's run.

Who ran, how it ended and what the daemon saw it change, read from
``evidence["console_run"]``. Moved out of ``services/producers/console_clis.py``
(producers), which re-exports it, so the evidence never imports a producer
(ADR 0012, issue #469 step 5).
"""

from __future__ import annotations

from typing import Any


def check_console_run(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's record of a console producer's run: who ran, how it ended, what it changed."""
    run = (evidence or {}).get("console_run")
    if not isinstance(run, dict):
        return None
    files = [f for f in run.get("files") or [] if isinstance(f, dict)]
    delivered = [f["path"] for f in files if f.get("delivered")]
    how = f"{run.get('label') or run.get('cli')} (exit {run.get('exit_code')}, {run.get('duration_seconds')} s)"
    left = [
        f"{f.get('path')} ({f.get('reason') or f.get('status')})"
        for f in files
        if not f.get("delivered")
    ]
    if run.get("outcome") == "changed":
        outcome, text = (
            "pass",
            f"{how} changed {len(delivered)} file(s): {', '.join(delivered)}",
        )
        if left:
            text += f"; {len(left)} change(s) not delivered: {', '.join(left[:10])}"
    else:
        outcome = "unverifiable" if run.get("kind") == "not_run" else "refuted"
        text = f"{how}: {run.get('reason')}"
    return {
        "name": "console_run",
        "outcome": outcome,
        "observation": text,
        "via": run.get("via"),
        "command": run.get("command"),
        "exit_code": run.get("exit_code"),
        "duration_seconds": run.get("duration_seconds"),
        "not_delivered": left,
        "daemon_refused": run.get("daemon_refused") or [],
    }

"""The Verifier's checks of the dependency install and the application run.

The loop asks the daemon (``services/loop/delivery_checks.py``, with
``workspace/dependency_install.py``) and keeps its replies on the evidence:

- ``evidence["dependency_install"]``: the profile's install command, run when
  a manifest the delivery wrote changed (``DELIVERY_INSTALL_ENABLED``); the
  ``dependencies_installed`` check reads it.
- ``evidence["app_run"]``: the application started in the sandbox, probed and
  stopped, with the e2e command's run (``DELIVERY_APP_CHECK_ENABLED``); the
  ``application_started`` and ``e2e_run`` checks read it.

Every outcome here is the daemon's report; a failure is refuted and the gate
sends the task back as for a failed test run.
"""

from __future__ import annotations

from typing import Any

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.evidence.browser_smoke import CHECK_E2E, check_e2e

CHECK_INSTALL = "dependencies_installed"
CHECK_APP = "application_started"


# ---------------------------------------------------------------------------
# The Verifier's checks
# ---------------------------------------------------------------------------


def _tail(record: dict[str, Any], limit: int = 400) -> str:
    tail = (record.get("output_tail") or "").strip()[-limit:]
    return f": {tail}" if tail else ""


def check_install(evidence: dict[str, Any]) -> dict[str, Any] | None:
    """``dependencies_installed``: absent when no manifest changed."""
    record = evidence.get("dependency_install")
    if not isinstance(record, dict):
        return None
    files = ", ".join(m.get("path", "?") for m in record.get("manifests") or [])
    target = f"`{record['command']}`" if record.get("command") else "install"
    after = f" after {files} changed" if files else ""
    if record.get("timed_out"):
        outcome = VERDICT_REFUTED
        seconds = record.get("timeout_seconds")
        within = f" within {seconds:g}s" if isinstance(seconds, (int, float)) else ""
        observation = f"{target} did not finish{within}{after} (killed)"
    elif isinstance(record.get("exit_code"), int) and not isinstance(
        record.get("exit_code"), bool
    ):
        code = record["exit_code"]
        if code == 0:
            outcome, observation = "pass", f"{target} exited 0{after}"
        else:
            outcome = VERDICT_REFUTED
            observation = f"{target} exited {code}{after}{_tail(record)}"
    else:
        outcome = VERDICT_UNVERIFIABLE
        observation = (
            f"{target} not run{after}: {record.get('reason') or 'no exit status'}"
        )
    return {
        "name": CHECK_INSTALL,
        "outcome": outcome,
        "observation": observation,
        "manifests": record.get("manifests") or [],
    }


def _log_tail(app: dict[str, Any]) -> str:
    log = app.get("log") or {}
    tail = (log.get("output_tail") or "").strip()[-400:]
    return f"; log: {tail}" if tail else ""


def check_app(evidence: dict[str, Any]) -> dict[str, Any] | None:
    """``application_started``: absent when nothing was to be started (no
    start command, or an earlier failure made the run pointless)."""
    app = evidence.get("app_run")
    if not isinstance(app, dict) or not app.get("command") or app.get("skipped"):
        return None
    target = f"`{app['command']}` on port {app.get('port')}"
    sandbox = app.get("sandbox") or {}
    where = f" ({sandbox.get('mode')})" if sandbox.get("mode") else ""
    if not app.get("started"):
        outcome = VERDICT_UNVERIFIABLE
        observation = (
            f"{target} not started: {app.get('reason') or 'no report from the daemon'}"
        )
    elif not app.get("listening"):
        outcome = VERDICT_REFUTED
        if app.get("exit_code") is not None:
            observation = f"{target} exited {app['exit_code']} before listening{where}"
        elif app.get("reason"):
            outcome = VERDICT_UNVERIFIABLE
            observation = f"{target} could not be probed: {app['reason']}"
        else:
            observation = (
                f"{target} did not listen within "
                f"{app.get('ready_timeout_seconds')}s{where}"
            )
        observation += _log_tail(app)
    else:
        probe = app.get("probe") or {}
        status = probe.get("status")
        if isinstance(status, int) and status < 500:
            outcome = "pass"
            observation = (
                f"{target} listened after {app.get('ready_seconds')}s{where}; "
                f"GET {probe.get('url')} answered {status}"
            )
        else:
            outcome = VERDICT_REFUTED
            answer = (
                f"answered {status}"
                if status is not None
                else f"got no answer ({probe.get('error') or 'unknown'})"
            )
            observation = (
                f"{target} listened{where} but GET {probe.get('url')} {answer}"
                + _log_tail(app)
            )
        if app.get("stopped") is False:
            observation += "; the daemon could not confirm it stopped"
    return {
        "name": CHECK_APP,
        "outcome": outcome,
        "observation": observation,
        "sandbox": sandbox or None,
    }


def checks(
    evidence: dict[str, Any] | None,
    only: tuple[str, ...] = (CHECK_INSTALL, CHECK_APP, CHECK_E2E),
) -> list[dict[str, Any]]:
    """The checks this module's evidence supports, in order."""
    evidence = evidence or {}
    builders = {
        CHECK_INSTALL: check_install,
        CHECK_APP: check_app,
        CHECK_E2E: check_e2e,
    }
    out = []
    for name in only:
        check = builders[name](evidence)
        if check:
            out.append(check)
    return out

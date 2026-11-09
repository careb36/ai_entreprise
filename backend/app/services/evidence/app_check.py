"""Installing the dependencies and running the application, as evidence.

Around the delivery test run (``workspace_daemon.run_delivery_tests``):

- **Install before test** (``DELIVERY_INSTALL_ENABLED``): when a dependency
  manifest the delivery wrote differs from what the company's last successful
  install in that folder saw (the sha256 in the daemon's write report against
  ``Company.dependency_installs``), the profile's install command runs through
  ``workspace.execute`` in the same sandbox as the tests, bounded by
  ``DELIVERY_INSTALL_TIMEOUT_SECONDS``, with the network as
  ``DELIVERY_INSTALL_NETWORK`` says whatever the sandbox's own setting (open
  by default: a registry is needed). The daemon's reply is
  ``evidence["dependency_install"]``; the Verifier's
  ``dependencies_installed`` check reads it. Only the manifests that install
  covers (``stack_profiles.installed_by``: a detected command, its own
  stack's manifests in the root; a named one, all) are recorded as installed,
  and only after it exited 0, so a failed one is retried and a manifest no
  install ran for (``not_covered``) is never taken as installed.
- **Run the application** (``DELIVERY_APP_CHECK_ENABLED``, off by default:
  it runs agent-written code): the profile's start command runs in the
  sandbox (``workspace.app.check``, ``vscode-daemon/app_run.py``), the daemon
  waits for the port, sends one GET to the probe path, runs the profile's e2e
  command when there is one, stops the application and replies with what it
  saw. That reply is ``evidence["app_run"]``; the Verifier's
  ``application_started`` and ``e2e_run`` checks read it. Without an e2e
  command, QA's browser smoke tests run as the e2e command when QA wrote
  them (``services/browser_smoke.py``), read back per criterion. QA's own
  test-writing task changes no application code: it is not started for it.

The commands come from the stack profile (``services/stack_profiles.py``):
configuration, never evidence. Every outcome here is the daemon's report; a
failure is refuted and the gate sends the task back as for a failed test run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings

from services import stack_profiles
from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.evidence import browser_smoke
from services.evidence.browser_smoke import CHECK_E2E, check_e2e
from services.evidence.criteria_tests import ACCEPTANCE_KIND

logger = logging.getLogger(__name__)

CHECK_INSTALL = "dependencies_installed"
CHECK_APP = "application_started"
OUTPUT_TAIL_CHARS = 2000
# What the backend adds to the daemon's own bounds before it stops waiting.
_REPLY_MARGIN_SECONDS = 30.0
# The application check's bounded phases beyond the ready and e2e bounds, as
# ``vscode-daemon/app_run.py`` sets them: ``docker run -d`` (LAUNCH_SECONDS,
# 120 s, a cold image is pulled), the last inspect and probe and the teardown
# (logs, removal), each a runtime call of RUNTIME_CALL_SECONDS (30 s).
_APP_OVERHEAD_SECONDS = 120.0 + 4 * 30.0


class Context:
    """One delivery's company settings and profile, read at most once."""

    def __init__(
        self, nats_client: Any, sprint: dict[str, Any], evidence: dict, engine: Any
    ):
        self.nats = nats_client
        self.sprint = sprint or {}
        self.evidence = evidence
        self.engine = engine
        self._company: dict[str, Any] | None = None
        self._profile: dict[str, Any] | None = None

    def company(self) -> dict[str, Any]:
        """``id``, ``overrides`` and ``installs`` of the sprint's company."""
        if self._company is None:
            self._company = _read_company(self.sprint, self.engine)
        return self._company

    async def profile(self) -> dict[str, Any]:
        if self._profile is None:
            detected = await _detect_profile(
                self.nats, self.evidence.get("workspace_root") or ""
            )
            self._profile = stack_profiles.resolve(
                detected, self.company().get("overrides")
            )
        return self._profile

    async def test_command(self) -> tuple[str | None, str]:
        """The delivery's test command and, without one, why.

        An empty ``test`` in the company's profile turns the tests off; a
        command the company or the deployment named runs as named; otherwise
        the detected profile's, which is absent when the project has no suite
        (npm's placeholder script, nothing for pytest to collect), and then
        the older detector (another runner's suite beside the root stack)."""
        own = self.company().get("overrides") or {}
        if "test" in own and own["test"] is not None and not str(own["test"]).strip():
            return None, "tests turned off by the company's stack profile"
        # A command a person named needs no look at the project.
        named = (
            str(own.get("test") or "").strip() or settings.DELIVERY_TEST_COMMAND.strip()
        )
        if named:
            return named, ""
        command = (await self.profile()).get("test")
        if not command:
            from services import workspace_daemon

            command = await workspace_daemon.detect_project_test_command(
                self.nats, self.evidence.get("workspace_root") or ""
            )
        return (command or None), "no test command found"


def _read_company(sprint: dict[str, Any], engine: Any) -> dict[str, Any]:
    from database import engine as default_engine
    from models.company import Company
    from models.sprint import Sprint
    from sqlmodel import Session

    try:
        with Session(engine or default_engine) as session:
            company_id = sprint.get("company_id")
            if company_id is None and sprint.get("id") is not None:
                row = session.get(Sprint, sprint["id"])
                company_id = row.company_id if row is not None else None
            company = session.get(Company, company_id) if company_id else None
            return {
                "id": company_id,
                "overrides": dict(company.stack_profile or {}) if company else {},
                "installs": dict(company.dependency_installs or {}) if company else {},
            }
    except Exception as e:  # noqa: BLE001 - The detected profile still applies.
        logger.warning("Could not read the company's stack profile: %s", e)
        return {"id": sprint.get("company_id"), "overrides": {}, "installs": {}}


async def _detect_profile(nats_client: Any, workspace_root: str) -> dict | None:
    """The profile of the project as the daemon's snapshot shows it."""
    from services import workspace_daemon

    try:
        project = await workspace_daemon.inspect_project(
            nats_client, workspace_root, snapshot=True
        )
    except (workspace_daemon.DaemonUnavailable, workspace_daemon.DaemonError):
        project = None
    except Exception as e:  # noqa: BLE001 - A fake or odd daemon reply.
        logger.info("Could not inspect %s for its profile: %s", workspace_root, e)
        project = None
    if not project or not project.get("is_dir"):
        return await asyncio.to_thread(stack_profiles.detect, workspace_root)

    def _in_snapshot() -> dict | None:
        with workspace_daemon.materialized_snapshot(
            project.get("snapshot") or {}
        ) as mirror:
            return stack_profiles.detect(mirror)

    return await asyncio.to_thread(_in_snapshot)


def changed_manifests(
    evidence: dict[str, Any], installs: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The manifests the delivery wrote whose sha256 (the daemon's, read back
    from disk) differs from the last successful install's in that folder."""
    root = evidence.get("workspace_root") or ""
    seen = ((installs or {}).get(root) or {}) if isinstance(installs, dict) else {}
    changed = []
    for write in evidence.get("workspace_writes") or []:
        if not isinstance(write, dict) or write.get("status") != "written":
            continue
        path, digest = write.get("path"), write.get("sha256")
        if (
            not isinstance(path, str)
            or not digest
            or not stack_profiles.is_manifest(path)
        ):
            continue
        if seen.get(path) != digest:
            changed.append({"path": path, "sha256": digest, "previous": seen.get(path)})
    return changed


def _command_record(command: str, reply: Any) -> dict[str, Any]:
    reply = reply if isinstance(reply, dict) else {}
    output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    record: dict[str, Any] = {
        "command": command,
        "exit_code": reply.get("exit_code"),
        "timed_out": bool(reply.get("timed_out")),
        "output_tail": output[-OUTPUT_TAIL_CHARS:],
        "output_bytes": len(output),
        "output_truncated": len(output) > OUTPUT_TAIL_CHARS,
        "reason": reply.get("error"),
    }
    if reply.get("refused"):
        record["refused"] = True
    if record["timed_out"]:
        record["timeout_seconds"] = reply.get("timeout_seconds")
    if isinstance(reply.get("sandbox"), dict):
        record["sandbox"] = reply["sandbox"]
    return record


def _record_installed(
    engine: Any, company_id: Any, root: str, manifests: list[dict[str, Any]]
) -> None:
    from database import engine as default_engine
    from models.company import Company
    from sqlmodel import Session

    with Session(engine or default_engine) as session:
        company = session.get(Company, company_id)
        if company is None:
            return
        installs = dict(company.dependency_installs or {})
        seen = dict(installs.get(root) or {})
        seen.update({m["path"]: m["sha256"] for m in manifests})
        installs[root] = seen
        company.dependency_installs = installs
        session.add(company)
        session.commit()


async def install_if_needed(ctx: Context) -> None:
    """Install the dependencies when a manifest changed; the daemon's reply
    becomes ``evidence["dependency_install"]``. Nothing is recorded when no
    manifest changed."""
    if not settings.DELIVERY_INSTALL_ENABLED:
        return
    evidence = ctx.evidence
    changed = changed_manifests(evidence, ctx.company().get("installs"))
    if not changed:
        return
    profile = await ctx.profile()
    command = profile.get("install")
    covered = [m for m in changed if stack_profiles.installed_by(profile, m["path"])]
    base: dict[str, Any] = {
        "manifests": covered if command else changed,
        "stack": profile.get("stack"),
        "source": (profile.get("source") or {}).get("install"),
    }
    if command and len(covered) < len(changed):
        # Changed, but not what this install installs: never recorded as installed.
        base["not_covered"] = [m for m in changed if m not in covered]
    if not command:
        evidence["dependency_install"] = {
            **base,
            "command": None,
            "reason": "no install command for this project's stack",
        }
        return
    if not covered:
        paths = ", ".join(m["path"] for m in changed)
        evidence["dependency_install"] = {
            **base,
            "command": None,
            "reason": f"`{command}` in the project's root does not install {paths}",
        }
        return
    from services import sandbox

    try:
        box = await asyncio.to_thread(
            sandbox.request_for_sprint, ctx.sprint, ctx.engine
        )
    except sandbox.PolicyUnavailable as e:
        evidence["dependency_install"] = {**base, "command": command, "reason": str(e)}
        return
    # The install's own setting, both ways: a registry may be needed, and a
    # lifecycle script must not reach the network when it is turned off.
    box = {**box, "network": bool(settings.DELIVERY_INSTALL_NETWORK)}
    timeout = float(settings.DELIVERY_INSTALL_TIMEOUT_SECONDS)
    try:
        reply = await ctx.nats.request(
            "workspace.execute",
            {
                "command": command,
                "workspace_root": evidence["workspace_root"],
                "sprint_id": ctx.sprint.get("id"),
                "sandbox": box,
                "timeout_seconds": timeout,
            },
            timeout=timeout + _REPLY_MARGIN_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves it unverified.
        evidence["dependency_install"] = {
            **base,
            "command": command,
            "reason": f"the workspace daemon did not reply ({type(e).__name__})",
        }
        return
    record = {**base, **_command_record(command, reply)}
    evidence["dependency_install"] = record
    if record["exit_code"] == 0 and not record["timed_out"]:
        company_id = ctx.company().get("id")
        if company_id is not None:
            try:
                await asyncio.to_thread(
                    _record_installed,
                    ctx.engine,
                    company_id,
                    evidence["workspace_root"],
                    covered,
                )
            except Exception as e:  # noqa: BLE001 - The next delivery installs again.
                logger.warning("Could not record the install: %s", e)


def _earlier_failure(evidence: dict[str, Any]) -> str | None:
    """Why starting the application would prove nothing new: the install or
    the tests already failed (the task goes back for that), or the delivery
    is QA's acceptance tests, which change no application code."""
    if (evidence.get("acceptance") or {}).get("kind") == ACCEPTANCE_KIND:
        return "QA's acceptance tests change no application code"
    for check in checks(evidence, only=(CHECK_INSTALL,)):
        if check["outcome"] == VERDICT_REFUTED:
            return "the dependency install failed"
    if evidence.get("test_run", {}).get("command"):
        from services.evidence.verification import check_tests_run

        if check_tests_run(evidence)["outcome"] == VERDICT_REFUTED:
            return "the tests failed"
    return None


async def check_application(ctx: Context) -> None:
    """Start the application in the sandbox, probe it and stop it; the
    daemon's reply becomes ``evidence["app_run"]``."""
    if not settings.DELIVERY_APP_CHECK_ENABLED:
        return
    evidence = ctx.evidence
    profile = await ctx.profile()
    start, port = profile.get("start"), profile.get("port")
    base = {
        "command": start,
        "port": port,
        "stack": profile.get("stack"),
        "source": (profile.get("source") or {}).get("start"),
    }
    if not start or not port:
        evidence["app_run"] = {
            **base,
            "command": None,
            "reason": "no start command or port for this project's stack",
        }
        return
    skipped = _earlier_failure(evidence)
    if skipped:
        evidence["app_run"] = {**base, "skipped": skipped}
        return
    from services import sandbox

    try:
        box = await asyncio.to_thread(
            sandbox.request_for_sprint, ctx.sprint, ctx.engine
        )
    except sandbox.PolicyUnavailable as e:
        evidence["app_run"] = {**base, "reason": str(e)}
        return
    ready = float(settings.DELIVERY_APP_READY_TIMEOUT_SECONDS)
    e2e_timeout = float(settings.DELIVERY_E2E_TIMEOUT_SECONDS)
    e2e = profile.get("e2e") or browser_smoke.default_command(
        ctx.company().get("overrides"), profile, evidence
    )
    if e2e and not profile.get("e2e"):
        base["e2e_source"] = browser_smoke.SOURCE_SMOKE
    try:
        reply = await ctx.nats.request(
            "workspace.app.check",
            {
                "workspace_root": evidence["workspace_root"],
                "start_command": start,
                "port": int(port),
                "probe_path": profile.get("probe_path") or "/",
                "e2e_command": e2e,
                "ready_timeout_seconds": ready,
                "e2e_timeout_seconds": e2e_timeout,
                "sandbox": box,
                "sprint_id": ctx.sprint.get("id"),
            },
            timeout=ready
            + (e2e_timeout if e2e else 0)
            + _APP_OVERHEAD_SECONDS
            + _REPLY_MARGIN_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves it unverified.
        evidence["app_run"] = {
            **base,
            "reason": f"the workspace daemon did not reply ({type(e).__name__})",
        }
        return
    evidence["app_run"] = {**base, **_app_record(reply)}
    browser_smoke.read_back(evidence)


def _app_record(reply: Any) -> dict[str, Any]:
    """The daemon's reply, kept with only the fields the checks read."""
    reply = reply if isinstance(reply, dict) else {}
    keep = (
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
    )
    record = {k: reply[k] for k in keep if k in reply}
    if reply.get("error"):
        record["reason"] = reply["error"]
    return record


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

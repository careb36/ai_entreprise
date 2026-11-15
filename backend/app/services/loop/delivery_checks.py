"""A delivery's checks through the daemon: install, tests, application, QA's tests.

After the daemon wrote a task's files, the loop asks it for what the
Verifier then judges, each reply kept on the run's evidence:

- the acceptance test files on disk against what QA delivered
  (``guard_test_files``, ``workspace.hash``), restored when they differ;
- the dependency install when a manifest changed
  (``workspace/dependency_install.py``);
- the project's tests (``workspace.execute``): ``test_run`` and ``ci``, with
  the runner's per-test lines read back per criterion
  (``evidence/criteria_tests.py``);
- opt-in (``DELIVERY_APP_CHECK_ENABLED``, off by default: it runs
  agent-written code), the application: the profile's start command runs in
  the sandbox (``workspace.app.check``, ``vscode-daemon/app_run.py``), the
  daemon waits for the port, sends one GET to the probe path, runs the
  profile's e2e command when there is one, stops the application and replies
  with what it saw. That reply is ``evidence["app_run"]``; the Verifier's
  ``application_started`` and ``e2e_run`` checks read it. Without an e2e
  command, QA's browser smoke tests run as the e2e command when QA wrote them
  (``evidence/browser_smoke.py``), read back per criterion. QA's own
  test-writing task changes no application code: it is not started for it.
  A browser e2e command asks the daemon to probe for a browser first
  (``browser_probe``) and, in container mode with ``DELIVERY_BROWSER_IMAGE``,
  runs in that image instead of ``DELIVERY_SANDBOX_IMAGE``.

The install, the tests and the application check each ask for the whole
output too (``workspace/run_logs.py``): the daemon uploads it to the object
store, the backend reads it back, hashes it and records ``full_log`` on the
record beside the tail.

The workspace sends the requests and returns observations; the evidence
judges them; this module, in the loop, puts the two together (ADR 0012). The
commands come from the stack profile (``workspace/project_profile.py``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings
from sqlalchemy.engine import Engine

from services.contracts.verdicts import VERDICT_REFUTED
from services.evidence import app_check, browser_smoke, criteria_tests
from services.evidence.acceptance import test_sources_of
from services.evidence.criteria_tests import ACCEPTANCE_KIND
from services.evidence.verification import check_tests_run
from services.workspace import dependency_install, run_logs, sandbox, workspace_daemon
from services.workspace.dependency_install import REPLY_MARGIN_SECONDS
from services.workspace.project_profile import Context

logger = logging.getLogger(__name__)

# The application check's bounded phases beyond the ready and e2e bounds, as
# ``vscode-daemon/app_run.py`` sets them: ``docker run -d`` (LAUNCH_SECONDS,
# 120 s, a cold image is pulled), the last inspect and probe and the teardown
# (logs, removal), each a runtime call of RUNTIME_CALL_SECONDS (30 s).
_APP_OVERHEAD_SECONDS = 120.0 + 4 * 30.0
# The browser probe before a browser e2e command (vscode-daemon/browser_probe.py,
# PROBE_SECONDS) and the full log's upload after the run.
_BROWSER_PROBE_SECONDS = 90.0
_FULL_LOG_UPLOAD_SECONDS = 60.0


async def run_delivery_tests(
    nats_client: Any,
    sprint: dict[str, Any],
    evidence: dict[str, Any],
    engine: Any = None,
    application: bool = True,
) -> None:
    """Install the dependencies when a manifest changed, run the project's
    tests, then (opt-in) start the application and probe it, each through the
    daemon; every result goes on *evidence*. ``application=False`` runs the
    tests alone (a sprint's integration, whose status reads only the test
    run)."""
    ctx = Context(nats_client, sprint, evidence, engine)
    await dependency_install.install_if_needed(ctx)
    await _run_tests(nats_client, sprint, evidence, engine, ctx)
    if application:
        await check_application(ctx)


async def _run_tests(
    nats_client: Any,
    sprint: dict[str, Any],
    evidence: dict[str, Any],
    engine: Any,
    ctx: Context,
) -> None:
    """Run the project's tests through the daemon and add the result to *evidence*:
    ``test_run`` (command, exit code, output tail) and ``ci``, the shape
    ``check_claimed_tests_evidenced`` reads. The exit status is the daemon's.
    The command is the resolved stack profile's (``Context.test_command``):
    the company's, the deployment's, or the detected one; an empty one in the
    company's profile turns the tests off."""
    workspace_root = evidence["workspace_root"]
    command, reason = await ctx.test_command()
    if not command:
        evidence["test_run"] = {"command": None, "reason": reason}
        return
    if evidence.get("acceptance"):
        # Per-test lines, so the criteria's tests can be told apart.
        command = criteria_tests.verbose_command(command)
    try:
        box = await asyncio.to_thread(sandbox.request_for_sprint, sprint, engine)
    except sandbox.PolicyUnavailable as e:
        # Never run agent code under a guessed sandbox: the tests stay unverified.
        evidence["test_run"] = {"command": command, "reason": str(e)}
        return
    full_log = run_logs.request_spec(nats_client)
    try:
        result = await nats_client.request(
            "workspace.execute",
            {
                "command": command,
                "workspace_root": workspace_root,
                "sprint_id": sprint["id"],
                # Agent-written code: run as the company's sandbox says.
                "sandbox": box,
                **run_logs.request_field(full_log),
            },
            timeout=settings.DELIVERY_TEST_TIMEOUT_SECONDS
            + (_FULL_LOG_UPLOAD_SECONDS if full_log.get("bucket") else 0),
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves the tests unverified.
        evidence["test_run"] = {
            "command": command,
            "reason": f"the workspace daemon did not reply ({type(e).__name__})",
        }
        return
    workspace_daemon.record_test_run(evidence, command, result)
    evidence["test_run"]["full_log"] = await run_logs.observe(
        nats_client, full_log, result
    )
    reply = result if isinstance(result, dict) else {}
    criteria_tests.record_results(
        evidence, f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    )


def _earlier_failure(evidence: dict[str, Any]) -> str | None:
    """Why starting the application would prove nothing new: the install or
    the tests already failed (the task goes back for that), or the delivery
    is QA's acceptance tests, which change no application code."""
    if (evidence.get("acceptance") or {}).get("kind") == ACCEPTANCE_KIND:
        return "QA's acceptance tests change no application code"
    for check in app_check.checks(evidence, only=(app_check.CHECK_INSTALL,)):
        if check["outcome"] == VERDICT_REFUTED:
            return "the dependency install failed"
    if (
        evidence.get("test_run", {}).get("command")
        and check_tests_run(evidence)["outcome"] == VERDICT_REFUTED
    ):
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
    browser = browser_smoke.is_browser_e2e(e2e, profile, base.get("e2e_source"))
    if browser and box.get("mode") == "container" and settings.DELIVERY_BROWSER_IMAGE:
        # The pinned browser image (vscode-daemon/images/browser); the
        # daemon records which image ran (its content ID and digest).
        box = {**box, "image": settings.DELIVERY_BROWSER_IMAGE.strip()}
        base["image_source"] = "DELIVERY_BROWSER_IMAGE"
    full_log = run_logs.request_spec(ctx.nats)
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
                **({"browser_probe": True} if browser else {}),
                **run_logs.request_field(full_log),
            },
            timeout=ready
            + (e2e_timeout if e2e else 0)
            + _APP_OVERHEAD_SECONDS
            + (_BROWSER_PROBE_SECONDS if browser else 0)
            + (_FULL_LOG_UPLOAD_SECONDS if full_log.get("bucket") else 0)
            + REPLY_MARGIN_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves it unverified.
        evidence["app_run"] = {
            **base,
            "reason": f"the workspace daemon did not reply ({type(e).__name__})",
        }
        return
    evidence["app_run"] = {**base, **_app_record(reply)}
    if browser:
        evidence["app_run"]["browser_probe_requested"] = True
    evidence["app_run"]["full_log"] = await run_logs.observe(ctx.nats, full_log, reply)
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
        "browser_probe",
    )
    record = {k: reply[k] for k in keep if k in reply}
    if reply.get("error"):
        record["reason"] = reply["error"]
    return record


async def guard_test_files(
    nats_client: Any, engine: Engine, evidence: dict[str, Any], timeout: float
) -> None:
    """Before an implementation task's tests run: the acceptance test files on
    disk must be the ones QA delivered. The daemon reports their sha256
    (``workspace.hash``); a file that differs or is missing is restored from
    QA's deliverable (``acceptance.test_sources_of``) and recorded in
    ``acceptance["tampered"]`` and ``acceptance["restored"]``, so
    ``criteria_tests.check_untouched`` refutes the delivery and the criteria are
    judged against QA's tests. A daemon without the operation leaves things
    as they were.

    Under the Git workflow, ``acceptance["other_test_hashes"]`` holds the
    sibling tasks' test files (``acceptance.evidence_for``): one is guarded
    only when it is on the task branch's disk (its base holds it); one the
    daemon does not report ``present`` is listed in ``acceptance["not_on_branch"]``,
    never restored, never an edit and never run for this task."""
    acceptance = evidence.get("acceptance") or {}
    hashes = acceptance.get("test_hashes") or {}
    others = acceptance.get("other_test_hashes") or {}
    if (not hashes and not others) or acceptance.get("kind") == ACCEPTANCE_KIND:
        return
    root = evidence.get("workspace_root")
    try:
        reply = await workspace_daemon.daemon_request(
            nats_client,
            "workspace.hash",
            {"workspace_root": root, "paths": [*hashes, *others]},
            timeout,
        )
    except Exception as exc:  # noqa: BLE001 - An older daemon has no workspace.hash; the delivery is judged as before.
        logger.warning("Acceptance test files not checked on disk: %s", exc)
        return
    files_seen = [f for f in reply.get("files") or [] if isinstance(f, dict)]
    on_disk = {f.get("path"): f.get("sha256") for f in files_seen}
    if others:
        present = {f.get("path") for f in files_seen if f.get("status") == "present"}
        held = {p: h for p, h in others.items() if p in present}
        acceptance["not_on_branch"] = [p for p in others if p not in present]
        hashes = acceptance["test_hashes"] = {**hashes, **held}
        guarded = acceptance.setdefault("test_files", [])
        guarded += [p for p in held if p not in guarded]
    tampered = [path for path, sha in hashes.items() if on_disk.get(path) != sha]
    if not tampered:
        return
    acceptance["tampered"] = tampered
    sources = test_sources_of(engine, acceptance.get("sprint_id"), tampered)
    files = [
        {"path": path, "content": sources[path], "overwrite_existing": True}
        for path in tampered
        if path in sources
    ]
    restored: list[str] = []
    if files:
        try:
            written = await workspace_daemon.daemon_request(
                nats_client,
                "workspace.generate",
                {
                    "workspace_root": root,
                    "files": files,
                    "interaction_id": "acceptance-restore",
                },
                timeout,
            )
            restored = [
                f["path"]
                for f in written.get("files") or []
                if isinstance(f, dict) and f.get("status") == "written"
            ]
        except Exception as exc:  # noqa: BLE001 - Recorded as tampered and not restored.
            logger.warning("Acceptance test files not restored: %s", exc)
    acceptance["restored"] = restored

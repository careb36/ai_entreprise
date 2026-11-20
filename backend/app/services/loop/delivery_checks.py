"""A delivery's checks through the daemon: install, tests, application, QA's tests.

After the daemon wrote a task's files, the loop asks it for what the
Verifier then judges, each reply kept on the run's evidence:

- the acceptance test files on disk against what QA delivered
  (``guard_test_files``, ``workspace.hash``), restored when they differ;
- the dependency install when a manifest changed
  (``delivery_commands.install_dependencies``);
- the project's tests (``workspace.execute``): ``test_run`` and ``ci``, with
  the runner's per-test lines read back per criterion
  (``evidence/criteria_tests.py``); then the suites of the folders the
  delivery wrote in and the company's every-delivery commands, each in
  ``command_runs`` (``delivery_suites.py``);
- opt-in (``DELIVERY_APP_CHECK_ENABLED``, off by default: it runs
  agent-written code), the application: the profile's start command runs in
  the sandbox (``workspace.app.check``, ``vscode-daemon/app_run.py``), the
  daemon waits for the port, sends one GET to the probe path, runs the
  profile's e2e command when there is one, stops the application and replies
  with what it saw. That reply is ``evidence["app_run"]``; the Verifier's
  ``application_started`` and ``e2e_run`` checks read it. The daemon also
  sends the running application a fixed set of security probes
  (``security_probe``, issue #540), read by the ``security_probe`` check. Without an e2e
  command, QA's browser smoke tests run as the e2e command when QA wrote them
  (``evidence/browser_smoke.py``), read back per criterion. QA's own
  test-writing task changes no application code: it is not started for it.
  A browser e2e command asks the daemon to probe for a browser first
  (``browser_probe``) and, in container mode with ``DELIVERY_BROWSER_IMAGE``,
  runs in that image instead of ``DELIVERY_SANDBOX_IMAGE``; with
  ``DELIVERY_BROWSER_IMAGES``, in the image of the Playwright release the
  project pins (``workspace/browser_images.py``), the default one with the
  mismatch recorded, and not at all when the version cannot be told.

The install, the tests and the application check each ask for the whole
output too (``workspace/run_logs.py``, through ``delivery_commands``): the
daemon uploads it to the object store, the backend reads it back, hashes it
and records ``full_log`` on the record beside the tail.

The workspace sends the requests and returns observations; the evidence
judges them; this module, in the loop, puts the two together (ADR 0012). The
daemon's requests go through the workspace's one entry point for them
(``workspace/delivery_commands.py``, ADR 0018); the commands come from the
stack profile (``workspace/project_profile.py``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings
from sqlalchemy.engine import Engine

from services.contracts.verdicts import VERDICT_REFUTED
from services.evidence import (
    app_check,
    browser_smoke,
    command_runs,
    criteria_tests,
    wstg_tests,
)
from services.evidence.acceptance import test_sources_of
from services.evidence.criteria_tests import ACCEPTANCE_KIND
from services.evidence.verification import check_tests_run
from services.loop import delivery_suites
from services.workspace import (
    daemon_liveness,
    delivery_commands,
    sandbox,
    workspace_daemon,
)
from services.workspace.project_profile import Context

logger = logging.getLogger(__name__)


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
    # The project's sandbox image first, on its own timeout (issue #766).
    await delivery_commands.prepare_stack_image(ctx)
    await delivery_commands.install_dependencies(ctx)
    await _run_tests(nats_client, sprint, evidence, engine, ctx)
    # The suites of the folders the delivery wrote in and the company's
    # every-delivery commands (``delivery_suites``); the root's run above.
    await delivery_suites.run(ctx, (evidence.get("test_run") or {}).get("command"))
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
    ``check_claimed_tests_evidenced`` reads (``delivery_commands.run_tests``).
    The exit status is the daemon's. The command is the resolved stack
    profile's (``Context.test_command``): the company's, the deployment's, or
    the detected one; an empty one in the company's profile turns the tests
    off."""
    command, reason = await ctx.test_command()
    if not command:
        evidence["test_run"] = {"command": None, "reason": reason}
        return
    if evidence.get("acceptance"):
        # Per-test lines, so the criteria's tests can be told apart.
        command = criteria_tests.verbose_command(command)
    output = await delivery_commands.run_tests(ctx, command)
    if output is None:
        return
    criteria_tests.record_results(evidence, output)
    # Tests named after a WSTG test (``test_wstg_athz_02_…``, issue #539).
    wstg_tests.record(evidence, output)


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
    if command_runs.failed(evidence):
        return "a required command failed"
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
    e2e = profile.get("e2e") or browser_smoke.default_command(
        ctx.company().get("overrides"), profile, evidence
    )
    if e2e and not profile.get("e2e"):
        base["e2e_source"] = browser_smoke.SOURCE_SMOKE
    browser = browser_smoke.is_browser_e2e(e2e, profile, base.get("e2e_source"))
    choice = None
    if browser and box.get("mode") == "container":
        # The image of the project's Playwright release when the deployment
        # lists them (DELIVERY_BROWSER_IMAGES), else the one browser image.
        choice = await delivery_commands.browser_image(ctx)
    if choice is not None:
        base["browser_image"] = choice
        if choice.get("image"):
            box = {**box, "image": choice["image"]}
            base["image_source"] = choice["source"]
    elif browser and box.get("mode") == "container" and settings.DELIVERY_BROWSER_IMAGE:
        # The pinned browser image (vscode-daemon/images/browser); the
        # daemon records which image ran (its content ID and digest).
        box = {**box, "image": settings.DELIVERY_BROWSER_IMAGE.strip()}
        base["image_source"] = "DELIVERY_BROWSER_IMAGE"
    # No image is known to match a version nobody could tell: the application
    # is still started and probed, the browser suite is not run.
    unknown = choice is not None and choice["match"] == "unknown"
    record, replied = await delivery_commands.check_app(
        ctx, profile, box, None if unknown else e2e, browser and not unknown
    )
    evidence["app_run"] = {**base, **record}
    if replied:
        browser_smoke.read_back(evidence)
    if unknown and e2e:
        evidence["app_run"]["e2e"] = delivery_commands.unverifiable_e2e(e2e, choice)


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
    if daemon_liveness.announces("workspace.hash") is False:
        # The daemons say they have no workspace.hash (their claim): not asked.
        logger.warning(
            "Acceptance test files not checked on disk: %s",
            daemon_liveness.not_announced_reason("workspace.hash"),
        )
        return
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

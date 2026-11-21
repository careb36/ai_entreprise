"""Where the self-hosting run's code ran, as the daemon reported it (issue #701).

The first self-hosting run never used the container sandbox, and nothing in
its report could have said so: the report's ``company.sandbox`` is the mode the
harness *asked* the company for, which is configuration, not what ran
(retrospective F8). This module keeps the two apart.

- ``--container-mode`` configures a run the way the e2e's ``e2e (container)``
  job does (``scripts/e2e/run.sh`` with ``E2E_SANDBOX=container``): the
  company's sandbox ``container``, one root command run with the image's own
  ``python`` (``--profile sprint-tests``, unless ``--profile`` says otherwise),
  and the ``sandbox`` step after ``wait``. The stack's side, which the harness
  cannot set, is in ``README.md`` (``DELIVERY_SANDBOX=container`` and
  ``DELIVERY_SANDBOX_IMAGE`` on the backend, the image on the daemon's
  computer).
- The ``sandbox`` step reads, for every run of the sprint, the sandbox the
  daemon's reply to its test run reported (``evidence.test_run.sandbox``, the
  reply ``vscode-daemon/sandbox.py`` writes and the backend records as it
  came), and labels the run with the mode those replies show: ``container``,
  ``host``, ``bwrap``, ``mixed``, or ``not observed`` when no reply carried a
  sandbox. The daemon's announced abilities (``workspace.status``) say what it
  *can* honour; they are a claim and never label a run.
- ``container_mode`` is true only when every reply reported an isolated
  container. A ``--container-mode`` run that is not exits with status 5 and
  says why; it is never called container mode.

Taken from the e2e's own check (step 4 of ``scripts/e2e/run.sh``: "the
delivery's tests ran where DELIVERY_SANDBOX says, and the Verifier recorded
the daemon's own report"), and from Codex cloud's practice of citing what the
sandboxed run did rather than what was asked of it (design only, no code).
"""

from __future__ import annotations

import argparse
from typing import Any

NOT_OBSERVED = "not observed"
MIXED = "mixed"
EXIT_NOT_CONTAINER = 5
# The e2e container job's command: the sandbox image's python, never a path
# on the harness's machine (scripts/e2e/run.sh, TEST_COMMAND for container).
TEST_COMMAND = "python -m pytest -q -p no:cacheprovider tests"
RUNS_LIMIT = 500


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Where agent-written code runs (company, sandbox) and what it runs."""
    parser.add_argument(
        "--sandbox",
        default=None,
        choices=("host", "container", "bwrap"),
        help="company: the sandbox asked for (default container)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=("folders", "sprint-tests"),
        help="company: La Fabrique's suites per folder and its every-delivery checks (default), or one root command (--test-command)",
    )
    parser.add_argument(
        "--test-command",
        default=None,
        help="company: the root's one command, with --profile sprint-tests",
    )
    parser.add_argument(
        "--container-mode",
        action="store_true",
        help="run as the e2e container job does: sandbox container, --profile sprint-tests, and the sandbox step; exit 5 unless the daemon reported a container",
    )


def apply(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    all_steps: tuple[str, ...],
) -> tuple[str, ...]:
    """The steps to run, with ``--container-mode``'s settings applied to *args*."""
    if args.container_mode:
        if args.sandbox not in (None, "container"):
            parser.error(
                f"--container-mode runs in the container sandbox, not --sandbox {args.sandbox}"
            )
        args.sandbox = "container"
        args.profile = args.profile or "sprint-tests"
    args.sandbox = args.sandbox or "container"
    args.profile = args.profile or "folders"
    args.test_command = args.test_command or TEST_COMMAND
    steps = (
        all_steps
        if args.steps == "all"
        else tuple(s.strip() for s in args.steps.split(","))
    )
    if args.container_mode and "wait" in steps and "sandbox" not in steps:
        steps = (
            *steps[: steps.index("wait") + 1],
            "sandbox",
            *steps[steps.index("wait") + 1 :],
        )
    return steps


def applied(verification: dict[str, Any]) -> dict[str, Any] | None:
    """The sandbox the daemon's reply to a verification's test run reported,
    or ``None`` when it reported none (an older daemon, or nothing ran)."""
    run = (verification.get("evidence") or {}).get("test_run") or {}
    reply = run.get("sandbox")
    if not isinstance(reply, dict) or not isinstance(reply.get("mode"), str):
        return None
    identity = (
        reply.get("image_identity")
        if isinstance(reply.get("image_identity"), dict)
        else {}
    )
    return {
        "mode": reply["mode"],
        "isolated": reply.get("isolated") is True,
        "forced_by_daemon": reply.get("forced_by_daemon") is True,
        "image": reply.get("image") if reply["mode"] == "container" else None,
        "image_id": identity.get("id"),
    }


def summarize(
    asked: str | None, runs: list[dict[str, Any]], verified: int
) -> dict[str, Any]:
    """The run's label from the daemon's replies (*runs*, one per test run
    that reported a sandbox; *verified*, the sprint's runs looked at), kept
    apart from *asked*, the mode the harness configured."""
    modes = sorted({_mode(r) for r in runs})
    observed = NOT_OBSERVED if not modes else modes[0] if len(modes) == 1 else MIXED
    out: dict[str, Any] = {
        "asked": asked,
        "observed": observed,
        "observed_by": "daemon" if runs else None,
        "container_mode": observed == "container",
        "replies": len(runs),
        "runs_looked_at": verified,
        "modes": {m: sum(1 for r in runs if _mode(r) == m) for m in modes},
        "images": sorted({r["image_id"] for r in runs if r.get("image_id")}),
        "runs": runs,
    }
    if observed == NOT_OBSERVED:
        out["note"] = (
            f"not observed: {verified} run(s) of the sprint, and no reply to a test run "
            "reported a sandbox; this run is not labelled with any mode"
        )
    elif asked and observed != asked:
        out["note"] = (
            f"asked for {asked}, but the daemon reported {observed}: "
            f"this run is labelled {observed}, not {asked} mode"
        )
    return out


def _mode(run: dict[str, Any]) -> str:
    # A container or bwrap reply that says it was not isolated ran like the host.
    return (
        run["mode"]
        if run["isolated"] or run["mode"] == "host"
        else f"{run['mode']} (not isolated)"
    )


def observe(api: Any, cid: int, sprint_id: int) -> tuple[list[dict[str, Any]], int]:
    """Every sandbox the daemon reported for the sprint's runs, and how many runs."""
    runs = api.get(
        f"/v1/agent_runs?company_id={cid}&sprint_id={sprint_id}&limit={RUNS_LIMIT}"
    )["agent_runs"]
    seen = []
    for run in runs:
        verifications = api.get(
            f"/v1/agent_runs/{run['id']}/verifications?company_id={cid}"
        )["verifications"]
        for verification in verifications:
            reply = applied(verification)
            if reply is not None:
                seen.append(
                    {
                        "run_id": run["id"],
                        "verification_id": verification.get("id"),
                        **reply,
                    }
                )
    return seen, len(runs)


def step_sandbox(api, args, state, report) -> None:
    """The ``sandbox`` step: the mode the sprint's runs used, as observed."""
    asked = (report.get("company") or {}).get("sandbox") or args.sandbox
    sprint_id = state.get("sprint_id")
    if not sprint_id:
        out = summarize(asked, [], 0)
        out["note"] = (
            "not observed: no sprint was started, so no run reported a sandbox"
        )
    else:
        out = summarize(asked, *observe(api, state["company_id"], sprint_id))
        out["sprint_id"] = sprint_id
    out["container_mode_asked"] = bool(args.container_mode)
    report["sandbox"] = out
    if args.container_mode and not out["container_mode"]:
        report.setdefault("notes", []).append(
            f"not a container-mode run: {out.get('note') or out['observed']}"
        )


def exit_status(report: dict[str, Any], container_mode: bool) -> int:
    """5 when a ``--container-mode`` run's daemon did not report a container."""
    seen = report.get("sandbox")
    return (
        EXIT_NOT_CONTAINER
        if container_mode and seen is not None and not seen["container_mode"]
        else 0
    )


HOST_ACKNOWLEDGEMENT = (
    "Self-hosting on the host sandbox: La Fabrique's own checkout, run by the "
    "person who started the harness."
)


def acknowledge_host(api: Any, company_id: int, mode: str) -> bool | None:
    """On the host, record the Owner's acknowledgement its work needs (#766).

    A host run starts no work without it (``services/loop/preconditions.py``);
    the harness says so in the company's own record, as the bench and the e2e
    do. ``True`` once recorded, ``None`` for any other mode."""
    if mode != "host":
        return None
    state = api.post(
        f"/v1/companies/{company_id}/acknowledgements",
        {"kind": "host_sandbox", "days": 1, "reason": HOST_ACKNOWLEDGEMENT},
    )
    return "host_sandbox" not in (state.get("missing") or ["?"])

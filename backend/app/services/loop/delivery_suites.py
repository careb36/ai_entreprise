"""The folders' suites and the every-delivery commands of a delivery (F5).

After the root's test run (``delivery_checks._run_tests``), a delivery runs:

- the test suite of each folder it wrote in (``stack_roots.selected``: the
  files are the daemon's report of what it wrote, never the agent's list);
  a file in no folder runs the root's suite when there is one, and every
  folder's otherwise;
- the commands the company runs on every delivery (``every_delivery``).

Each runs through ``workspace.execute`` in the sandbox the company's policy
names (``DELIVERY_SANDBOX``), as ``cd <folder> && <command>`` since the
daemon runs every command in the project's root, and is kept in
``evidence["command_runs"]`` with its folder, exit status and the sha256 the
backend computed over the output it received. The Verifier's
``required_commands`` check (``services/evidence/command_runs.py``) judges
them.

When the root has no suite of its own, ``test_run`` is the folders' suites
read together (``summary_of: "command_runs"``), so the per-criterion reading,
the gate and a sprint's integration read one test run as before. A
single-root project whose company names no folder and no every-delivery
command records nothing new.
"""

from __future__ import annotations

from typing import Any

from services.evidence import command_runs, criteria_tests, wstg_tests
from services.workspace import stack_roots
from services.workspace.folder_commands import execute, sandbox_box
from services.workspace.project_profile import Context
from services.workspace.workspace_daemon import TEST_OUTPUT_TAIL_CHARS


def _append(evidence: dict[str, Any], record: dict[str, Any]) -> None:
    evidence.setdefault("command_runs", []).append(record)


async def run(ctx: Context, root_command: str | None) -> None:
    """The touched folders' suites, then the every-delivery commands."""
    evidence = ctx.evidence
    folders = await ctx.folders()
    chosen, why = stack_roots.selected(
        folders, stack_roots.changed_paths(evidence), bool(root_command)
    )
    commands = ctx.every_delivery()
    if not chosen and not commands:
        return
    box, unavailable = await sandbox_box(ctx)
    suites: list[tuple[dict[str, Any], str]] = []
    for folder in chosen:
        command = folder.get("test")
        if not command:
            continue
        if evidence.get("acceptance"):
            command = criteria_tests.verbose_command(command)
        record, output = await execute(
            ctx, "suite", folder["folder"], command, box, unavailable
        )
        record["source"] = (folder.get("source") or {}).get("test")
        record["failing_tests"] = criteria_tests.failing_tests(output)[:50]
        _append(evidence, record)
        suites.append((record, output))
    if chosen:
        evidence["folders_run"] = {
            "folders": [f["folder"] for f in chosen],
            "reason": why,
            "without_suite": [f["folder"] for f in chosen if not f.get("test")],
        }
    for entry in commands:
        record, _output = await execute(
            ctx,
            "every_delivery",
            entry.get("folder"),
            entry["command"],
            box,
            unavailable,
        )
        _append(evidence, record)
    if not root_command and suites:
        summarise(evidence, suites)


def summarise(
    evidence: dict[str, Any], suites: list[tuple[dict[str, Any], str]]
) -> None:
    """``test_run`` as the folders' suites read together, for a root without
    a suite of its own: the first failure's exit status, else the first run
    that could not happen (no exit status), else 0."""
    records = [r for r, _o in suites]
    outcomes = [command_runs.outcome_of(r, evidence)[0] for r in records]
    failed = [r for r, o in zip(records, outcomes, strict=True) if o == "refuted"]
    blocked = [r for r, o in zip(records, outcomes, strict=True) if o == "unverifiable"]
    # Exited non-zero, but only for acceptance tests not implemented yet:
    # ``tests_run`` excuses them again from the failing tests read below.
    excused = [r for r in records if r.get("exit_code") not in (0, None)]
    output = "".join(o for _r, o in suites)
    test_run: dict[str, Any] = {
        "command": "; ".join(r.get("run") or r["command"] for r in records),
        "summary_of": "command_runs",
        "folders": [r["folder"] for r in records],
        "output_tail": output[-TEST_OUTPUT_TAIL_CHARS:],
        "output_bytes": len(output),
        "output_truncated": len(output) > TEST_OUTPUT_TAIL_CHARS,
        "timed_out": False,
        "reason": None,
    }
    if failed:
        first = failed[0]
        test_run["timed_out"] = bool(first.get("timed_out"))
        test_run["exit_code"] = first.get("exit_code")
        if first.get("timed_out"):
            test_run["timeout_seconds"] = first.get("timeout_seconds")
        test_run["output_tail"] = first.get("output_tail") or ""
        conclusion = "timed_out" if first.get("timed_out") else "failure"
    elif blocked:
        test_run["exit_code"] = None
        test_run["reason"] = command_runs.outcome_of(blocked[0], evidence)[1]
        conclusion = None
    elif excused:
        test_run["exit_code"] = excused[0]["exit_code"]
        conclusion = "failure"
    else:
        test_run["exit_code"] = 0
        conclusion = "success"
    evidence["test_run"] = test_run
    if conclusion:
        evidence["ci"] = {
            "source": "workspace daemon",
            "ref": test_run["command"],
            "conclusion": conclusion,
        }
    criteria_tests.record_results(evidence, output)
    wstg_tests.record(evidence, output)

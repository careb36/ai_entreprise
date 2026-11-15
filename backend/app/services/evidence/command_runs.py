"""The Verifier's ``required_commands`` check: every command a delivery had to run.

Beside the root's test run (``tests_run``), a delivery runs the suites of the
folders it wrote in, their installs when a manifest changed, and the commands
the company runs on every delivery (``services/workspace/stack_roots.py``,
``services/loop/delivery_suites.py``). Each is kept in
``evidence["command_runs"]`` as the daemon replied: the folder, the command,
the exit status and the sha256 of the output that the backend computed over
the bytes it received. Never the agent's report of its own run: that is a
claim, and it is not read here.

- any required command that failed (a non-zero exit, a timeout) refutes;
- one that could not run (a runtime that is not installed, a refusal by the
  command policy, no exit status) leaves the delivery unverifiable;
- a suite whose only failures are the acceptance tests of criteria not
  implemented yet passes, as ``tests_run`` excuses them.
"""

from __future__ import annotations

import re
from typing import Any

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.evidence import criteria_tests

CHECK_NAME = "required_commands"
# What a shell or an interpreter prints when the program itself is missing.
_UNAVAILABLE_EXIT = (126, 127)
_UNAVAILABLE = (
    re.compile(r"command not found", re.IGNORECASE),
    re.compile(r"is not recognized as an internal or external command", re.IGNORECASE),
    re.compile(r"executable file not found", re.IGNORECASE),
    # ``python -m pytest`` without pytest; an import error inside a test is
    # ``ModuleNotFoundError: No module named 'x'``, with the quotes.
    re.compile(r"^\S*python[\d.]*(?:\.exe)?: No module named [\w.]+\s*$", re.MULTILINE),
)
_KINDS = {"install": "install", "suite": "suite", "every_delivery": "check"}


def unavailable(record: dict[str, Any]) -> bool:
    """Whether the command never ran because what it calls is not there."""
    code = record.get("exit_code")
    if code in _UNAVAILABLE_EXIT:
        return True
    if not isinstance(code, int) or code == 0:
        return False
    text = f"{record.get('output_tail') or ''}\n{record.get('reason') or ''}"
    return any(p.search(text) for p in _UNAVAILABLE)


def outcome_of(
    record: dict[str, Any], evidence: dict[str, Any] | None = None
) -> tuple[str, str]:
    """``(outcome, observation)`` of one recorded run."""
    where = f" in {record['folder']}/" if record.get("folder") else ""
    target = f"`{record.get('command')}`{where}"
    if record.get("refused"):
        reason = record.get("reason") or "refused"
        return VERDICT_UNVERIFIABLE, f"{target} refused by the command policy: {reason}"
    if record.get("timed_out"):
        return VERDICT_REFUTED, f"{target} did not finish in time (killed)"
    code = record.get("exit_code")
    if not isinstance(code, int) or isinstance(code, bool):
        reason = record.get("reason") or "no exit status reported"
        return VERDICT_UNVERIFIABLE, f"{target} not run: {reason}"
    if code == 0:
        return "pass", f"{target} exited 0"
    if unavailable(record):
        first = next(
            (
                line.strip()
                for line in (record.get("output_tail") or "").splitlines()
                if line.strip()
            ),
            "",
        )[:200]
        return (
            VERDICT_UNVERIFIABLE,
            f"{target} could not run (exit {code}): {first or 'not installed'}",
        )
    if record.get("kind") == "suite":
        excused_all, excused = criteria_tests.excused_among(
            record.get("failing_tests") or [], (evidence or {}).get("acceptance") or {}
        )
        if excused_all:
            return "pass", (
                f"{target} exited {code}: only the acceptance tests of criteria "
                f"not implemented yet failed ({', '.join(excused)})"
            )
    tail = (record.get("output_tail") or "").strip()[-300:]
    return VERDICT_REFUTED, f"{target} exited {code}" + (f": {tail}" if tail else "")


def check(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """``required_commands``: absent when the delivery ran none."""
    records = [
        r for r in (evidence or {}).get("command_runs") or [] if isinstance(r, dict)
    ]
    if not records:
        return None
    runs, outcomes, texts = [], set(), []
    for record in records:
        outcome, text = outcome_of(record, evidence)
        outcomes.add(outcome)
        texts.append(f"{_KINDS.get(record.get('kind'), 'command')}: {text}")
        runs.append(
            {
                "kind": record.get("kind"),
                "folder": record.get("folder") or "",
                "command": record.get("command"),
                "exit_code": record.get("exit_code"),
                "output_sha256": record.get("output_sha256"),
                "outcome": outcome,
            }
        )
    if VERDICT_REFUTED in outcomes:
        verdict = VERDICT_REFUTED
    elif VERDICT_UNVERIFIABLE in outcomes:
        verdict = VERDICT_UNVERIFIABLE
    else:
        verdict = "pass"
    return {
        "name": CHECK_NAME,
        "outcome": verdict,
        "observation": "; ".join(texts),
        "runs": runs,
    }


def failed(evidence: dict[str, Any] | None) -> bool:
    """Whether a required command failed (not one that could not run)."""
    return any(
        outcome_of(r, evidence)[0] == VERDICT_REFUTED
        for r in (evidence or {}).get("command_runs") or []
        if isinstance(r, dict)
    )

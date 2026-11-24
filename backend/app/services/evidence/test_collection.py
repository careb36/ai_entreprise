"""A test command that found no tests: the configuration's failure, not the code's.

In the self-hosting owner action of 2026-11-24 (#701, run 2) the company's
one test command was ``python -m pytest … tests`` at the repository's root,
and the plan put its tests in ``backend/tests``. Every delivery got pytest's
``ERROR: file or directory not found: tests`` (exit 4); ``tests_run`` read
that as "tests failed" and the gate sent each task back to fix its code, a
rework no agent could do anything with.

``no_tests`` reads the daemon's reply to a test run (the command, its exit
status and the end of its output: observations, never an agent's report)
and says when the runner collected nothing, from the runners' documented
exits:

- pytest exit 5, no tests were collected (``collected 0 items``);
- pytest exit 4, a usage error: only when the output says a path it was
  given does not exist (``file or directory not found``) or the command line
  was refused (``error: unrecognized arguments``, ``usage:``). Exit 4 for a
  ``conftest.py`` that does not import is the code's, judged as before;
- jest's ``No tests found`` and vitest's ``No test files found`` (both exit
  1), the other runners ``criteria_tests`` reads that say so.

``no_tests_in`` applies it to the evidence's ``test_run``, or, when that run
is the folders' suites read together (``summary_of: "command_runs"``), to
the suite that left it without an exit status. ``check`` is the ``tests_run``
check it gives: ``unverifiable`` with ``cause: "no_tests"``, which the gate
(``delivery_gate``) turns into a hold for a person instead of a rework.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from services.contracts.verdicts import VERDICT_UNVERIFIABLE

CAUSE = "no_tests"
PYTEST_USAGE_ERROR = 4
PYTEST_NO_TESTS = 5
_NOT_FOUND = re.compile(r"file or directory not found:\s*(\S+)")
_USAGE = re.compile(r"(?:error: unrecognized arguments|^usage: )[^\n]*", re.MULTILINE)
_NONE_FOUND = re.compile(r"No tests found|No test files found")
# pytest options whose next token is their value, not a path.
_VALUE_OPTIONS = frozenset(
    {
        "-p",
        "-k",
        "-m",
        "-c",
        "-o",
        "-W",
        "-n",
        "--rootdir",
        "--ignore",
        "--deselect",
        "--junitxml",
        "--junit-xml",
        "--confcutdir",
        "--basetemp",
        "--maxfail",
        "--tb",
        "--log-level",
    }
)
_SEPARATORS = frozenset({"&&", "||", ";", "|"})


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command or "")
    except ValueError:
        return (command or "").split()


def pytest_paths(command: str) -> list[str]:
    """The paths a pytest command line names, in order (none: its folder)."""
    tokens = _tokens(command)
    start = next(
        (
            i
            for i, t in enumerate(tokens)
            if t.rsplit("/", 1)[-1] in ("pytest", "py.test")
        ),
        None,
    )
    if start is None:
        return []
    paths: list[str] = []
    skip = False
    for token in tokens[start + 1 :]:
        if token in _SEPARATORS:
            break
        if skip:
            skip = False
        elif token in _VALUE_OPTIONS:
            skip = True
        elif not token.startswith("-"):
            paths.append(token)
    return paths


def _where(folder: str | None, paths: list[str]) -> str:
    prefix = f"{folder.rstrip('/')}/" if folder else ""
    if not paths:
        return f"{prefix or 'the root '}(the folder it ran in)"
    return ", ".join(f"{prefix}{p}" for p in paths)


def no_tests(run: dict[str, Any]) -> str | None:
    """Why *run* (a test run or a folder's suite as the daemon replied) found
    no tests, or ``None`` when it did or the reply does not say so."""
    code = run.get("exit_code")
    if not isinstance(code, int) or isinstance(code, bool) or code == 0:
        return None
    command = str(run.get("command") or "")
    output = str(run.get("output_tail") or "")
    folder = run.get("folder") or None
    is_pytest = "pytest" in command or "test session starts" in output
    if is_pytest and code == PYTEST_NO_TESTS:
        where = _where(folder, pytest_paths(command))
        return f"the test command found no tests at {where} (pytest collected 0 items)"
    if is_pytest and code == PYTEST_USAGE_ERROR:
        missing = _NOT_FOUND.search(output)
        if missing:
            where = _where(folder, [missing.group(1)])
            return f"the test command found no tests at {where} (file or directory not found)"
        usage = _USAGE.search(output)
        if usage:
            return f"the test command was refused by pytest (usage error): {usage.group(0).strip()[:200]}"
        return None
    if _NONE_FOUND.search(output):
        return f"the test command found no tests at {_where(folder, [])}"
    return None


def no_tests_in(evidence: dict[str, Any]) -> tuple[Any, str] | None:
    """``(exit status, reason)`` when the delivery's test run found no tests."""
    run = evidence.get("test_run") or {}
    if run.get("summary_of") != "command_runs":
        reason = no_tests(run)
        return (run.get("exit_code"), reason) if reason else None
    if run.get("exit_code") is not None:
        return None  # a suite failed, or every suite passed
    for record in evidence.get("command_runs") or []:
        if isinstance(record, dict) and record.get("kind") == "suite":
            reason = no_tests(record)
            if reason:
                return record.get("exit_code"), reason
    return None


def check(target: str, found: tuple[Any, str], truncated: str = "") -> dict[str, Any]:
    """The ``tests_run`` check for a run that found no tests."""
    code, reason = found
    return {
        "name": "tests_run",
        "outcome": VERDICT_UNVERIFIABLE,
        "cause": CAUSE,
        "observation": (
            f"{reason}; {target} exited {code}. Where the company's test command "
            "looks and where the tests are disagree: that is configuration, not "
            "code, and no rework changes it. A person fixes the test command "
            f"(the stack profile) or moves the tests{truncated}"
        ),
    }

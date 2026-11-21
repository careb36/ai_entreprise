"""What a delivery must show, decided before reading what it happened to show.

On 2026-11-20 the factory's own company held 66 verifications and none of
them contained a test run (issue #766, ``docs/design/target/01-verification.md``).
With ``DELIVERY_TESTS_ENABLED`` off the delivery wrote no ``test_run``, the
Verifier added ``tests_run`` only when ``test_run`` was present, and
``aggregate_verdict`` looked only at the checks present: a missing check read
as a passed one, and a delivery whose files existed and whose imports
resolved was ``verified``. POSITIONING's rule 3 ("``unverifiable`` is a real
verdict") was broken by omission.

``missing`` closes that: from the paths the delivery changes, it decides which
checks the delivery requires, and returns each required check that did not
run as ``unverifiable`` with the reason. The verdict is then computed over
the full list (the aggregation rule itself is unchanged).

Policy ``v1``: a delivery that changes code (any path that is not
documentation) requires an observation of tests: the platform's own run
(``tests_run``), the suites of the folders it wrote in when the root has
none (``suites_ran``), or an independent CI conclusion (``evidence["ci"]``). An
agent's report of its own test run is a claim and never satisfies it.

Pure: no database, no I/O. ``policy`` returns what was required, so the
verification row says which rule produced its verdict.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from services.contracts.verdicts import VERDICT_UNVERIFIABLE

POLICY_VERSION = "v1"
TESTS_RUN = "tests_run"
DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})
DOC_NAMES = frozenset({"LICENSE", "NOTICE", "AUTHORS", "CODEOWNERS"})
SHOWN_PATHS = 5


def is_documentation(path: str) -> bool:
    """A path whose change needs no test run: prose, not code."""
    name = PurePosixPath(path).name
    return name in DOC_NAMES or PurePosixPath(path).suffix.lower() in DOC_SUFFIXES


def code_paths(paths: list[str]) -> list[str]:
    """The paths that are code, in order, without repeats."""
    seen: dict[str, None] = {}
    for path in paths:
        if isinstance(path, str) and path and not is_documentation(path):
            seen.setdefault(path, None)
    return list(seen)


def _ci_observed(evidence: dict[str, Any]) -> bool:
    ci = evidence.get("ci")
    return isinstance(ci, dict) and bool(ci.get("conclusion"))


def suites_ran(evidence: dict[str, Any] | None) -> bool:
    """Whether the daemon ran a folder's suite for this delivery: in a
    repository whose root has no suite of its own, the folders' suites are
    its tests (``command_runs``, kind ``suite``; judged by
    ``required_commands``). A suite that could not run has no exit status
    and does not count."""
    for record in (evidence or {}).get("command_runs") or []:
        if (
            isinstance(record, dict)
            and record.get("kind") == "suite"
            and (isinstance(record.get("exit_code"), int) or record.get("timed_out"))
        ):
            return True
    return False


def required(paths: list[str]) -> list[str]:
    """The checks a delivery that changes *paths* requires."""
    return [TESTS_RUN] if code_paths(paths) else []


def missing(
    paths: list[str], evidence: dict[str, Any] | None, checks: list[dict]
) -> list[dict]:
    """Each required check that did not run, as ``unverifiable`` with the reason."""
    evidence = evidence or {}
    present = {check.get("name") for check in checks}
    added: list[dict] = []
    if (
        TESTS_RUN in required(paths)
        and TESTS_RUN not in present
        and not _ci_observed(evidence)
        and not suites_ran(evidence)
    ):
        code = code_paths(paths)
        shown = ", ".join(code[:SHOWN_PATHS]) + (
            f" and {len(code) - SHOWN_PATHS} more" if len(code) > SHOWN_PATHS else ""
        )
        added.append(
            {
                "name": TESTS_RUN,
                "outcome": VERDICT_UNVERIFIABLE,
                "observation": (
                    "not run: no test observation for a delivery that "
                    f"changes code ({shown})"
                ),
                "required": True,
            }
        )
    return added


def policy(paths: list[str]) -> dict[str, Any]:
    """What was required of this delivery, for the verification row."""
    return {"version": POLICY_VERSION, "required": required(paths)}

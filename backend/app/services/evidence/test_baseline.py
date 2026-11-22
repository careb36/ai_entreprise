"""A test that already failed in the folder is not this delivery's failure.

Without the Git workflow every task delivers into one shared folder, so a
delivery's test run also runs what other tasks left there. In the bench of
2026-11-22 (spec 06) three QA tasks were refuted, and blocked, for a seed test
another task's in-flight file had broken; a developer task was refuted for a
CLI test an earlier delivery had broken and left behind.

``evidence["test_baseline"]`` is the folder's latest test run before this
delivery, as the daemon observed it for another task (the loop records it,
``services/loop/folder_baseline.py``): which tests failed then. ``explain``
says whether every test that fails now is either an acceptance test of a
criterion not implemented yet (``criteria_tests.excused_among``) or a test
that already failed in that run, in a file this delivery did not write. A
test file the delivery wrote is always its own, and so is an acceptance test
of a criterion it implements (it failed before by design, the code did not
exist yet); a run whose list of failures was cut, or a baseline without names,
excuses nothing.
"""

from __future__ import annotations

from typing import Any

from services.evidence import criteria_tests


def _names(values: Any) -> list[str]:
    return [n for n in values or [] if isinstance(n, str) and n]


def _complete(run: dict[str, Any]) -> list[str] | None:
    """The run's failing tests when the list is the whole of them."""
    names = _names(run.get("failing_tests"))
    count = run.get("failing_count")
    if not names or isinstance(count, bool) or count != len(names):
        return None
    return names


def explain(evidence: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """Whether every failure is explained, the excused criteria, and the tests
    that already failed before; at least one must be the latter."""
    failing = _complete(evidence.get("test_run") or {})
    baseline = evidence.get("test_baseline") or {}
    before = set(_names(baseline.get("failing_tests")))
    if failing is None or not before:
        return False, [], []
    written = {
        w.get("path")
        for w in evidence.get("workspace_writes") or []
        if isinstance(w, dict)
    }
    acceptance = evidence.get("acceptance") or {}
    excused: list[str] = []
    earlier: list[str] = []
    for name in failing:
        ok, criteria = criteria_tests.excused_among([name], acceptance)
        if ok:
            excused += [c for c in criteria if c not in excused]
        elif any(
            criteria_tests.names_criterion(name, c) for c in acceptance.get("all") or []
        ):
            # A criterion's own test that no excuse covers: this task's to pass.
            return False, [], []
        elif name in before and name.split("::", 1)[0] not in written:
            earlier.append(name)
        else:
            return False, [], []
    return bool(earlier), excused, earlier


def observation(
    evidence: dict[str, Any], excused: list[str], earlier: list[str]
) -> str:
    """What the passing ``tests_run`` says: which run the failures were seen in."""
    run_id = (evidence.get("test_baseline") or {}).get("run_id")
    shown = ", ".join(earlier[:5]) + (
        f" and {len(earlier) - 5} more" if len(earlier) > 5 else ""
    )
    text = (
        f"only tests that already failed in the folder before this delivery "
        f"failed (run #{run_id}, another task's): {shown}"
    )
    if excused:
        text += f"; and acceptance tests of criteria not implemented yet ({', '.join(excused)})"
    return text

"""Assertions on a verification whose only gap is the missing test run.

Since issue #766 a delivery that changes code is ``unverifiable`` until the
platform observes its tests (``services/evidence/required_checks.py``). Tests
about something else (grounding, the Git workflow, queued deliveries, roles)
run with tests off; they assert that every check they produced passed and
that the one gap is the test run, instead of a ``verified`` that would hide
it.
"""

from __future__ import annotations


def _checks(verification):
    if isinstance(verification, dict):
        return verification["verdict"], verification["checks"]
    return verification.verdict, verification.checks


def assert_verified_but_for_tests(verification) -> None:
    verdict, checks = _checks(verification)
    gaps = [(c["name"], c["outcome"]) for c in checks if c["outcome"] != "pass"]
    assert verdict == "unverifiable", (verdict, gaps)
    assert gaps == [("tests_run", "unverifiable")], gaps

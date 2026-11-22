"""QA's own tests that fail only because the code they test is not written yet.

A QA task that writes acceptance tests before the implementation expects them
to fail; the Verifier excuses a failing test that names an excused criterion
(``criteria_tests.excused_among``: ``test_req_1_1_…``). A QA agent often adds
tests beside those that name no criterion (an edge case, an empty input). They
fail for the same reason, and the whole delivery used to be refuted for it: in
the bench of 2026-11-22 (spec 05, issue #774) QA's task was refuted four times
for ``test_empty_text_returns_empty_dict`` failing with ``ModuleNotFoundError:
No module named 'confcheck'``, and the six tasks after it never ran.

``explain`` excuses such a test only in QA's acceptance-test task, only while
every criterion the task covers is still unimplemented, only in a test file
this delivery wrote, and only when the runner's own summary line (the daemon's
output, ``test_run.failure_reasons``) says the import of the code failed. Any
other failure, a cut list of failures, or a test without a reason excuses
nothing. The implementation task that comes next is judged on the same tests
with no such excuse.
"""

from __future__ import annotations

import re
from typing import Any

from services.evidence import criteria_tests

# The import of code that does not exist yet; never an assertion or an error
# inside code that does.
_NOT_WRITTEN = re.compile(
    r"^(?:ModuleNotFoundError: No module named |ImportError: cannot import name )"
)


def explain(evidence: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """Whether every failure is excused, the excused criteria, and the tests
    excused because their code is not written yet; at least one of those."""
    acceptance = evidence.get("acceptance") or {}
    test_run = evidence.get("test_run") or {}
    if acceptance.get("kind") != criteria_tests.ACCEPTANCE_KIND:
        return False, [], []
    excused_ids = set(acceptance.get("excused") or [])
    if not set(acceptance.get("covers") or []) <= excused_ids:
        return False, [], []
    failing = [n for n in test_run.get("failing_tests") or [] if isinstance(n, str)]
    count = test_run.get("failing_count")
    if not failing or isinstance(count, bool) or count != len(failing):
        return False, [], []
    reasons = test_run.get("failure_reasons") or {}
    written = {
        w.get("path")
        for w in evidence.get("workspace_writes") or []
        if isinstance(w, dict) and w.get("status") == "written"
    }
    excused: list[str] = []
    unwritten: list[str] = []
    for name in failing:
        ok, criteria = criteria_tests.excused_among([name], acceptance)
        if ok:
            excused += [c for c in criteria if c not in excused]
            continue
        reason = reasons.get(name) if isinstance(reasons, dict) else None
        if (
            name.split("::", 1)[0] in written
            and isinstance(reason, str)
            and _NOT_WRITTEN.match(reason)
        ):
            unwritten.append(name)
            continue
        return False, [], []
    return bool(unwritten), excused, unwritten


def observation(excused: list[str], unwritten: list[str]) -> str:
    shown = ", ".join(unwritten[:5]) + (
        f" and {len(unwritten) - 5} more" if len(unwritten) > 5 else ""
    )
    text = (
        "only tests of this task's own files failed, because the code they "
        f"import is not written yet: {shown}"
    )
    if excused:
        text += f"; and acceptance tests of criteria not implemented yet ({', '.join(excused)})"
    return text

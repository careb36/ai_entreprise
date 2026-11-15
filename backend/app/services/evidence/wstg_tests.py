"""The Verifier's ``wstg_tests`` check: tests named after an OWASP WSTG test, as the daemon ran them.

A project's security test plan (``services/planning/wstg_plan.py``, issue
#539) asks QA for one test per applicable ``auto`` / ``qa_test`` row, named
``test_wstg_<cat>_<nn>_…`` (``ATHZ-02`` → ``test_wstg_athz_02_…``). After a
delivery the daemon runs the project's tests (``DELIVERY_TESTS_ENABLED``);
``record`` reads the runner's per-test lines (``criteria_tests.test_lines``)
for those names and keeps, per WSTG test, ``pass`` or ``fail`` on
``evidence["test_run"]["wstg"]``. That is an observation of the daemon's
output, never the agent's report of its own run (rules 1 and 2 of
``docs/POSITIONING.md``).

``check`` turns it into items in the security probe's shape
(``{"id", "name", "status", "observation"}``, ``services/evidence/security_probe.py``):
the versioned id from the catalogue, ``pass`` or ``refuted``. The check is
informational: its outcome is ``pass`` whatever the items say, because a
security test that fails reports a weakness of the application, not a fault
of the delivery under review, and the project's tests already judge that run
(``tests_run``). The documentation pack's WSTG checklist reads the items.
"""

from __future__ import annotations

import re
from typing import Any

from services.evidence.criteria_tests import test_lines
from services.platform import wstg_catalogue

CHECK_NAME = "wstg_tests"
PASS, FAIL = "pass", "fail"
_TOKEN = re.compile(r"(?<![a-z0-9])wstg_([a-z]{4})_(\d\d)(?![0-9])")
_NORMAL = re.compile(r"[^a-z0-9]+")


def results_from_output(output: str) -> dict[str, str]:
    """Per short WSTG id named by a test, ``pass`` or ``fail`` (any failure fails it)."""
    results: dict[str, str] = {}
    for name, passed in test_lines(output):
        normal = _NORMAL.sub("_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower())
        for cat, number in _TOKEN.findall(normal):
            short = f"{cat.upper()}-{number}"
            if wstg_catalogue.by_short(short) is None:
                continue
            if not passed or results.get(short) == FAIL:
                results[short] = FAIL
            else:
                results[short] = PASS
    return dict(sorted(results.items()))


def record(evidence: dict[str, Any], output: str) -> None:
    """Add ``evidence["test_run"]["wstg"]`` when the run named any WSTG test."""
    test_run = evidence.get("test_run")
    if not isinstance(test_run, dict):
        return
    found = results_from_output(output)
    if found:
        test_run["wstg"] = found


def check(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """``wstg_tests``: absent unless the observed run named a WSTG test."""
    test_run = (evidence or {}).get("test_run") or {}
    found = test_run.get("wstg")
    if not isinstance(found, dict) or not found:
        return None
    items = []
    for short, result in sorted(found.items()):
        test = wstg_catalogue.by_short(short)
        if test is None or result not in (PASS, FAIL):
            continue
        items.append(
            {
                "id": test["id"],
                "name": test["name"],
                "status": "pass" if result == PASS else "refuted",
                "observation": f"test_{short.lower().replace('-', '_')} … "
                + ("passed" if result == PASS else "failed")
                + f" in `{str(test_run.get('command') or '?')[:120]}`",
            }
        )
    if not items:
        return None
    failed = [i["id"] for i in items if i["status"] != "pass"]
    return {
        "name": CHECK_NAME,
        "outcome": "pass",
        "observed_by": "daemon",
        "tool": str(test_run.get("command") or "")[:120],
        "wstg_commit": wstg_catalogue.commit(),
        "observation": f"{len(items)} WSTG tests ran: "
        + (f"failed {', '.join(failed)}" if failed else "all passed"),
        "items": items,
    }

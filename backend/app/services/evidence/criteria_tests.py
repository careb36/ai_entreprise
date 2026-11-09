"""The acceptance tests per criterion, as the daemon's test run saw them.

The runner's per-test lines (pytest ``-v``, jest, vitest, go, cargo) are
read back into criterion ids: a test whose name carries ``req_1_2`` speaks
for ``REQ-1.2``. What comes out is an observation, from the daemon's output,
never from the agent. The Verifier's checks live here too:
``criteria_tests`` (what the run said of the task's criteria) and
``acceptance_tests_untouched`` (an implementation task left the test files
alone). ``services.acceptance`` plans the test-writing tasks and reads the
database; this module reads only text and evidence dicts.
"""

from __future__ import annotations

import re
from typing import Any

ACCEPTANCE_KIND = "acceptance_tests"
CHECK_CRITERIA = "criteria_tests"
CHECK_UNTOUCHED = "acceptance_tests_untouched"
RESULT_PASS, RESULT_FAIL, RESULT_MISSING = "pass", "fail", "missing"

# Per-test lines of the runners the daemon may run: the test's name and
# whether it passed. Playwright's list reporter (``✓  1 [project] ›
# e2e/req_1_2.spec.ts:3:5 › REQ-1.2 title (1.2s)``, ``✘``; ``ok`` and ``x``
# where the terminal has no UTF-8), pytest ``-v`` (``path::name PASSED``),
# its short summary (``FAILED path::name - …``), jest and vitest (``✓ name``
# / ``✕ name``, ``×``), go (``--- PASS: Name``), cargo (``test name ...
# ok``), mocha (``✓``/``N)``).
_LINE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^\s*(✓|✔|✘|ok|x)\s+\d+\s+(.*\S:\d+:\d+\s+›\s+.+?)"
            r"(?:\s+\(\d+(?:\.\d+)?\s*m?s\))?\s*$"
        ),
        "playwright",
    ),
    (re.compile(r"^\s*(\S+::\S+)\s+(PASSED|FAILED|ERROR|XPASS|XFAIL)\b"), "pytest"),
    (re.compile(r"^\s*(PASSED|FAILED|ERROR)\s+(\S+::\S+)"), "pytest-summary"),
    (re.compile(r"^\s*[✓✔]\s+(.+?)(?:\s+\(\d+\s*m?s\))?\s*$"), "tick"),
    (re.compile(r"^\s*[✕✗×]\s+(.+?)(?:\s+\(\d+\s*m?s\))?\s*$"), "cross"),
    (re.compile(r"^\s*---\s+(PASS|FAIL):\s+(\S+)"), "go"),
    (re.compile(r"^\s*test\s+(\S+)\s+\.\.\.\s+(ok|FAILED)\b"), "cargo"),
)
_PASS_WORDS = {"PASSED", "XPASS", "PASS", "ok"}


# What a test file's name looks like, in the runners the daemon may run:
# ``test_*.py``, ``*_test.py``/``*_test.go``, ``*.test.ts``, ``*.spec.ts``,
# ``FooTest.java``, ``foo_spec.rb``. A manifest or a configuration file QA
# delivered beside its tests (``package.json``, ``playwright.config.ts``) is
# not one, and is never guarded as an acceptance test.
_TEST_FILE = re.compile(
    r"^(?:test_.+|.+_test|.+\.(?:test|spec))\.[A-Za-z0-9]+$"
    r"|^.+Tests?\.(?:java|kt|cs)$|^.+_spec\.rb$"
)


def is_test_file(path: Any) -> bool:
    """Whether *path* names a test file (by its name, never its folder)."""
    if not isinstance(path, str):
        return False
    return bool(_TEST_FILE.match(path.replace("\\", "/").rsplit("/", 1)[-1]))


def token(criterion_id: str) -> str:
    """The test-name token of a criterion id: ``REQ-1.2`` → ``req_1_2``."""
    return re.sub(r"[^a-z0-9]+", "_", str(criterion_id).lower()).strip("_")


def _normalized(name: str) -> str:
    # camelCase split first (``TestReq_1_1`` → ``test_req_1_1``), then lowered.
    return re.sub(
        r"[^a-z0-9]+", "_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    )


def names_criterion(test_name: str, criterion_id: str) -> bool:
    """Whether a test's name carries the criterion's token as a whole word."""
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(token(criterion_id))}(?![0-9])",
            _normalized(test_name),
        )
        is not None
    )


def _playwright_name(text: str) -> str:
    """A Playwright test's title (``[project] › file:line:col › title``); its
    file when the title carries no number, so ``e2e/req_1_2.spec.ts`` still
    names the criterion. Never both: a file holding another criterion's test
    must not speak for it."""
    parts = [p.strip() for p in text.split(" › ")]
    at = next(
        (i for i, p in enumerate(parts) if re.search(r":\d+:\d+$", p)), len(parts) - 1
    )
    title = " › ".join(parts[at + 1 :])
    return title if re.search(r"\d", title) else parts[at].rsplit(":", 2)[0]


def test_lines(output: str) -> list[tuple[str, bool]]:
    """The per-test lines of a runner's output: ``(test name, passed)``."""
    found: list[tuple[str, bool]] = []
    for raw in (output or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        for pattern, kind in _LINE_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            if kind == "playwright":
                found.append(
                    (
                        _playwright_name(match.group(2)),
                        match.group(1) in ("✓", "✔", "ok"),
                    )
                )
            elif kind == "pytest":
                found.append((match.group(1), match.group(2) in _PASS_WORDS))
            elif kind == "pytest-summary":
                found.append((match.group(2), match.group(1) in _PASS_WORDS))
            elif kind == "tick":
                found.append((match.group(1), True))
            elif kind == "cross":
                found.append((match.group(1), False))
            elif kind == "go":
                found.append((match.group(2), match.group(1) in _PASS_WORDS))
            elif kind == "cargo":
                found.append((match.group(1), match.group(2) in _PASS_WORDS))
            break
    return found


def results_from_output(output: str, criterion_ids: list[str]) -> dict[str, str]:
    """Per criterion, ``pass``, ``fail`` or ``missing``, from the runner's output.

    A criterion with several tests passes only when every one of them did;
    one named by no test line is ``missing``.
    """
    lines = test_lines(output)
    results: dict[str, str] = {}
    for cid in criterion_ids:
        outcomes = [passed for name, passed in lines if names_criterion(name, cid)]
        if not outcomes:
            results[cid] = RESULT_MISSING
        else:
            results[cid] = RESULT_PASS if all(outcomes) else RESULT_FAIL
    return results


def failing_tests(output: str) -> list[str]:
    """The names of the tests that failed, from the runner's per-test lines."""
    return list(
        dict.fromkeys(name for name, passed in test_lines(output) if not passed)
    )


def verbose_command(command: str) -> str:
    """The test command with per-test output where the runner is known.

    pytest prints one line per test with ``-v``; ``-q`` would hide them.
    Other runners are left as configured (``DELIVERY_TEST_COMMAND`` can name
    a verbose one: ``jest --verbose``, ``vitest --reporter=verbose``).
    """
    if "pytest" in command:
        stripped = re.sub(
            r"\s+", " ", re.sub(r"(?<=\s)-q(?=\s|$)", "", command)
        ).strip()
        return (
            stripped if re.search(r"(?<=\s)-v+(?=\s|$)", stripped) else f"{stripped} -v"
        )
    if "go test" in command and "-v" not in command.split():
        # -v must precede the package pattern.
        return (
            command.replace("go test ", "go test -v ", 1)
            if "go test " in command
            else "go test -v"
        )
    return command


def record_results(evidence: dict[str, Any], output: str) -> None:
    """Add to ``evidence["test_run"]`` what the runner's output says per
    criterion (``criteria``) and which tests failed (``failing_tests``)."""
    acceptance = evidence.get("acceptance")
    test_run = evidence.get("test_run")
    if not acceptance or not isinstance(test_run, dict):
        return
    test_run["criteria"] = results_from_output(output, acceptance.get("all") or [])
    test_run["failing_tests"] = failing_tests(output)[:50]


def excused_failures(evidence: dict[str, Any]) -> tuple[bool, list[str]]:
    """Whether every failing test is an acceptance test of an excused criterion,
    and those criteria. A run whose failures could not be read excuses none."""
    return excused_among(
        (evidence.get("test_run") or {}).get("failing_tests") or [],
        evidence.get("acceptance") or {},
    )


def excused_among(
    failing: list[str], acceptance: dict[str, Any]
) -> tuple[bool, list[str]]:
    """``excused_failures`` over any list of failing test names (a unit run's
    or a browser run's)."""
    if not failing:
        return False, []
    excused: list[str] = []
    for name in failing:
        cid = next(
            (c for c in acceptance.get("excused") or [] if names_criterion(name, c)),
            None,
        )
        if cid is None:
            return False, []
        if cid not in excused:
            excused.append(cid)
    return True, excused


def _combined(unit: str | None, browser: str | None) -> str:
    """One criterion's result over its unit and browser tests: a failure in
    either fails it; otherwise a pass in either passes it."""
    if RESULT_FAIL in (unit, browser):
        return RESULT_FAIL
    if RESULT_PASS in (unit, browser):
        return RESULT_PASS
    return RESULT_MISSING


def check_criteria(evidence: dict[str, Any]) -> dict[str, Any] | None:
    """The ``criteria_tests`` check: per covered criterion with a test task,
    what the observed run said. ``None`` when the task has none."""
    acceptance = evidence.get("acceptance") or {}
    tested = acceptance.get("tested") or []
    if not tested:
        return None
    results = (evidence.get("test_run") or {}).get("criteria")
    if not isinstance(results, dict):
        reason = (evidence.get("test_run") or {}).get(
            "reason"
        ) or "the tests were not run"
        return {
            "name": CHECK_CRITERIA,
            "outcome": "unverifiable",
            "observation": f"{', '.join(tested)}: {reason}",
            "results": {c: RESULT_MISSING for c in tested},
        }
    # A browser smoke test of the criterion (``services/browser_smoke.py``),
    # read from the e2e run's output: a failure there fails the criterion.
    browser = ((evidence.get("app_run") or {}).get("e2e") or {}).get("criteria") or {}
    own = {c: _combined(results.get(c), browser.get(c)) for c in tested}
    words = ", ".join(f"{c} {r}" for c, r in own.items())
    if acceptance.get("kind") == ACCEPTANCE_KIND:
        missing = [c for c, r in own.items() if r == RESULT_MISSING]
        if missing:
            return {
                "name": CHECK_CRITERIA,
                "outcome": "refuted",
                "observation": (
                    f"no test ran for {', '.join(missing)} (name one test_{token(missing[0])}_…"
                    " and import the code under test inside it)"
                ),
                "results": own,
            }
        return {
            "name": CHECK_CRITERIA,
            "outcome": "pass",
            "observation": f"a test ran for every criterion: {words}",
            "results": own,
        }
    if any(r == RESULT_FAIL for r in own.values()):
        return {
            "name": CHECK_CRITERIA,
            "outcome": "refuted",
            "observation": words,
            "results": own,
        }
    if any(r == RESULT_MISSING for r in own.values()):
        return {
            "name": CHECK_CRITERIA,
            "outcome": "unverifiable",
            "observation": words + " (no test named for it ran)",
            "results": own,
        }
    return {
        "name": CHECK_CRITERIA,
        "outcome": "pass",
        "observation": words,
        "results": own,
    }


def check_untouched(
    claimed: list[str], evidence: dict[str, Any]
) -> dict[str, Any] | None:
    """The ``acceptance_tests_untouched`` check of an implementation task: it
    must not write an acceptance test file. ``None`` for the test task."""
    acceptance = evidence.get("acceptance") or {}
    if acceptance.get("kind") == ACCEPTANCE_KIND or not acceptance.get("test_files"):
        return None
    written = [
        w.get("path")
        for w in evidence.get("workspace_writes") or []
        if isinstance(w, dict) and w.get("status") == "written"
    ]
    tampered = [p for p in acceptance.get("tampered") or [] if isinstance(p, str)]
    touched = [
        p
        for p in acceptance["test_files"]
        + [t for t in tampered if t not in acceptance["test_files"]]
        if p in set(written) | set(claimed) | set(tampered)
    ]
    if touched:
        restored = [p for p in acceptance.get("restored") or [] if p in touched]
        return {
            "name": CHECK_UNTOUCHED,
            "outcome": "refuted",
            "observation": (
                f"edits the acceptance test {', '.join(touched)}: a change to a "
                "criterion's test is the specification's to decide"
                + (
                    f"; restored from QA's delivery: {', '.join(restored)}"
                    if restored
                    else ""
                )
            ),
        }
    return {
        "name": CHECK_UNTOUCHED,
        "outcome": "pass",
        "observation": "no acceptance test file written",
    }

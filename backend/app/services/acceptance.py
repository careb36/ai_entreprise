"""An acceptance test per criterion: a criterion is done when its test passes.

A sprint planned from a specification gets, before its implementation
tasks, one **test-writing task per requirement** (``kind ==
ACCEPTANCE_KIND``, assigned to QA): one test per acceptance criterion,
named after the criterion id (``REQ-1.2`` → ``test_req_1_2_…``), written
through the daemon like any file. Each implementation task depends on the
test tasks of the requirements it covers, so the Developer always has the
tests to make pass, and its prompt names the test files.

After a delivery the daemon runs the project's tests
(``DELIVERY_TESTS_ENABLED``); ``results_from_output`` reads the per-test
lines of the runner's output and maps the test names back to criterion ids
(``pass``, ``fail`` or ``missing``). That map is an observation: it comes
from the daemon's output, never from the agent. The Verifier records it as
the ``criteria_tests`` check; ``tests_run`` excuses the failing acceptance
tests of criteria whose implementation is not done yet (an acceptance test
fails before its implementation, by design), and never any other failure.
A deliverable of an implementation task that edits an acceptance test is
refuted (``acceptance_tests_untouched``): a change to a criterion's test is
the specification's to decide, never silently accepted.

``verified_criteria`` reads, per criterion, the latest observed result of an
implementation task's run: a criterion is ``verified`` only when its test
passed in an observed run.
"""

from __future__ import annotations

import re
from typing import Any

from logging_config import logger
from models import AgentRun, BacklogItem, Sprint, Verification
from models.specification import Specification
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

ACCEPTANCE_KIND = "acceptance_tests"
QA_ROLE = "QA"
TITLE_PREFIX = "Acceptance tests for "
CHECK_CRITERIA = "criteria_tests"
CHECK_UNTOUCHED = "acceptance_tests_untouched"
RESULT_PASS, RESULT_FAIL, RESULT_MISSING = "pass", "fail", "missing"
_TASK_WORKER = "le-fabrique-task-worker"

# Per-test lines of the runners the daemon may run: the test's name and
# whether it passed. pytest ``-v`` (``path::name PASSED``), its short summary
# (``FAILED path::name - …``), jest and vitest (``✓ name`` / ``✕ name``, ``×``),
# go (``--- PASS: Name``), cargo (``test name ... ok``), mocha (``✓``/``N)``).
_LINE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*(\S+::\S+)\s+(PASSED|FAILED|ERROR|XPASS|XFAIL)\b"), "pytest"),
    (re.compile(r"^\s*(PASSED|FAILED|ERROR)\s+(\S+::\S+)"), "pytest-summary"),
    (re.compile(r"^\s*[✓✔]\s+(.+?)(?:\s+\(\d+\s*m?s\))?\s*$"), "tick"),
    (re.compile(r"^\s*[✕✗×]\s+(.+?)(?:\s+\(\d+\s*m?s\))?\s*$"), "cross"),
    (re.compile(r"^\s*---\s+(PASS|FAIL):\s+(\S+)"), "go"),
    (re.compile(r"^\s*test\s+(\S+)\s+\.\.\.\s+(ok|FAILED)\b"), "cargo"),
)
_PASS_WORDS = {"PASSED", "XPASS", "PASS", "ok"}


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


def test_lines(output: str) -> list[tuple[str, bool]]:
    """The per-test lines of a runner's output: ``(test name, passed)``."""
    found: list[tuple[str, bool]] = []
    for raw in (output or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        for pattern, kind in _LINE_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            if kind == "pytest":
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
    return command


# ---------------------------------------------------------------------------
# The test-writing tasks in a plan
# ---------------------------------------------------------------------------


def is_acceptance_task(item: Any) -> bool:
    kind = item.get("kind") if isinstance(item, dict) else getattr(item, "kind", None)
    return kind == ACCEPTANCE_KIND


def test_task(requirement: dict[str, Any]) -> dict[str, Any]:
    """The test-writing task of *requirement*, one test per criterion."""
    criteria = [c for c in requirement.get("criteria") or [] if c.get("id")]
    names = ", ".join(
        f"{c['id']} → test_{token(c['id'])}_<what it checks>" for c in criteria
    )
    return {
        "title": f"{TITLE_PREFIX}{requirement['id']}: {requirement.get('title', '')}"[
            :80
        ],
        "description": (
            "Write one acceptance test per criterion of this requirement, in the "
            "project's test framework, before its implementation exists. Name each "
            f"test after its criterion id ({names}). Each test asserts the criterion's "
            "`then` from its `given` and `when`, as a black box through the module's "
            "interface. Import the code under test inside the test function, so a "
            "missing module fails that test rather than the whole file. The tests "
            "fail until the implementation task makes them pass; do not implement "
            "the feature here."
        )[:500],
        "priority": "high",
        "assignee": QA_ROLE,
        "covers": [c["id"] for c in criteria],
        "depends_on": [],
        "kind": ACCEPTANCE_KIND,
    }


def with_test_tasks(
    tasks: list[dict[str, Any]], spec: dict[str, Any], item_limit: int
) -> list[dict[str, Any]]:
    """*tasks* (the PO's split, ``depends_on`` by position) preceded by the test
    tasks of the requirements they cover, each implementation task depending on
    its test tasks. Within *item_limit*: an implementation task that would not
    fit with its tests is left out, from the end."""
    if any(is_acceptance_task(t) for t in tasks):
        return tasks
    requirements_by_id = {
        r["id"]: r for r in spec.get("requirements") or [] if r.get("id")
    }
    requirement_of = {
        c["id"]: r
        for r in requirements_by_id.values()
        for c in r.get("criteria") or []
        if c.get("id")
    }
    kept: list[
        tuple[int, dict[str, Any], list[int]]
    ] = []  # (old position, task, test positions)
    test_tasks: list[dict[str, Any]] = []
    test_position: dict[str, int] = {}
    for old, task in enumerate(tasks):
        requirements = list(
            dict.fromkeys(
                requirement_of[c]["id"]
                for c in task.get("covers") or []
                if c in requirement_of
            )
        )
        new = [r for r in requirements if r not in test_position]
        if len(kept) + len(test_tasks) + len(new) + 1 > item_limit:
            break
        for rid in new:
            test_position[rid] = len(test_tasks)
            test_tasks.append(test_task(requirements_by_id[rid]))
        kept.append((old, task, [test_position[r] for r in requirements]))
    # The PO's dependencies (positions among its tasks) re-pointed onto the
    # kept tasks, shifted past the test tasks; one onto a task left out is dropped.
    offset = len(test_tasks)
    old_to_new = {old: offset + new for new, (old, _t, _p) in enumerate(kept)}
    result = list(test_tasks)
    for _old, task, tests_of in kept:
        deps = [
            old_to_new[d]
            for d in task.get("depends_on") or []
            if isinstance(d, int) and d in old_to_new
        ]
        result.append(
            {**task, "kind": None, "depends_on": list(dict.fromkeys(tests_of + deps))}
        )
    return result


def section(session: Session, sprint: Sprint, item: Any) -> str:
    """The prompt section naming the acceptance tests an implementation task
    must make pass: the files its test tasks wrote and the test names."""
    if is_acceptance_task(item):
        return ""
    covers = [
        str(c)
        for c in (
            item.get("covers")
            if isinstance(item, dict)
            else getattr(item, "covers", None)
        )
        or []
    ]
    if not covers:
        return ""
    files, named = test_files_of(session, sprint.id, covers)
    if not files and not named:
        return ""
    lines = [
        "\n\n## Acceptance tests to make pass",
        (
            "QA wrote one test per criterion this task covers, through the daemon, "
            "before this task. Make them pass without changing them: a test you "
            "think wrong is a question for the specification, not an edit."
        ),
        "",
    ]
    lines += [f"- {path}" for path in files]
    lines += [f"- {cid}: a test named test_{token(cid)}_…" for cid in named]
    return "\n".join(lines)


def test_files_of(
    session: Session, sprint_id: int, covers: list[str]
) -> tuple[list[str], list[str]]:
    """The files the sprint's test tasks of *covers* wrote (their latest run's
    declared files), and the criteria among *covers* that have a test task."""
    tasks = session.exec(
        select(BacklogItem).where(
            BacklogItem.sprint_id == sprint_id, BacklogItem.kind == ACCEPTANCE_KIND
        )
    ).all()
    files: list[str] = []
    named: list[str] = []
    for task in tasks:
        its = [str(c) for c in task.covers or []]
        if not set(its) & set(covers):
            continue
        named += [c for c in covers if c in its and c not in named]
        run = session.exec(
            select(AgentRun)
            .where(
                AgentRun.external_ref == f"task-{task.id}",
                AgentRun.producer == _TASK_WORKER,
            )
            .order_by(AgentRun.version.desc())
        ).first()
        for path in (run.claims or {}).get("files") or [] if run else []:
            if isinstance(path, str) and path not in files:
                files.append(path)
    return files, named


# ---------------------------------------------------------------------------
# What a delivery is judged against
# ---------------------------------------------------------------------------


def evidence_for(engine: Engine, item_id: int) -> dict[str, Any] | None:
    """What the Verifier needs about the task's criteria, read before the test run.

    ``None`` when the sprint was not planned from a specification or has no
    test task; otherwise ``covers``, ``kind``, ``tested`` (the covered
    criteria that have a test task), ``all`` (every criterion of the
    specification), ``excused`` (criteria whose acceptance tests may fail:
    their implementation is not done yet, and never this task's own unless it
    is the test task) and ``test_files`` (the files the test tasks wrote).
    """
    try:
        with Session(engine) as session:
            item = session.get(BacklogItem, item_id)
            sprint = session.get(Sprint, item.sprint_id) if item else None
            if item is None or sprint is None or sprint.specification_id is None:
                return None
            spec = session.get(Specification, sprint.specification_id)
            if spec is None:
                return None
            every = [
                c["id"]
                for r in spec.requirements or []
                for c in r.get("criteria") or []
            ]
            covers = [str(c) for c in item.covers or []]
            files, tested = test_files_of(session, sprint.id, every)
            if not tested:
                return None
            done = {
                str(c)
                for row in session.exec(
                    select(BacklogItem).where(
                        BacklogItem.sprint_id == sprint.id, BacklogItem.status == "done"
                    )
                ).all()
                if not is_acceptance_task(row)
                for c in row.covers or []
            }
            own = set() if is_acceptance_task(item) else set(covers)
            return {
                "covers": covers,
                "kind": item.kind,
                "tested": [c for c in covers if c in tested],
                "all": every,
                "excused": [c for c in every if c not in done and c not in own],
                "test_files": files,
            }
    except Exception as exc:  # noqa: BLE001 - Without it the delivery is judged as before.
        logger.debug("Acceptance evidence failed for item=%s: %s", item_id, exc)
        return None


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
    acceptance = evidence.get("acceptance") or {}
    failing = (evidence.get("test_run") or {}).get("failing_tests") or []
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
    own = {c: results.get(c, RESULT_MISSING) for c in tested}
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
    touched = [p for p in acceptance["test_files"] if p in set(written) | set(claimed)]
    if touched:
        return {
            "name": CHECK_UNTOUCHED,
            "outcome": "refuted",
            "observation": (
                f"edits the acceptance test {', '.join(touched)}: a change to a "
                "criterion's test is the specification's to decide"
            ),
        }
    return {
        "name": CHECK_UNTOUCHED,
        "outcome": "pass",
        "observation": "no acceptance test file written",
    }


# ---------------------------------------------------------------------------
# What the specification shows
# ---------------------------------------------------------------------------


def verified_criteria(
    session: Session, spec: Specification
) -> dict[str, dict[str, Any]]:
    """Per criterion id, the latest observed result of an implementation task's
    run (``result``, ``task_id``, ``verified``), from the Verifier's
    ``criteria_tests`` checks; criteria never tested are absent."""
    items = session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(Sprint.specification_id == spec.id)
        .order_by(BacklogItem.id)
    ).all()
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        if is_acceptance_task(item) or not item.covers:
            continue
        run = session.exec(
            select(AgentRun)
            .where(
                AgentRun.external_ref == f"task-{item.id}",
                AgentRun.producer == _TASK_WORKER,
            )
            .order_by(AgentRun.version.desc())
        ).first()
        if run is None:
            continue
        verification = session.exec(
            select(Verification)
            .where(Verification.agent_run_id == run.id)
            .order_by(Verification.id.desc())
        ).first()
        check = next(
            (
                c
                for c in (verification.checks if verification else []) or []
                if c.get("name") == CHECK_CRITERIA
            ),
            None,
        )
        if not check:
            continue
        for cid, result in (check.get("results") or {}).items():
            latest[cid] = {
                "result": result,
                "task_id": item.id,
                "verified": result == RESULT_PASS,
            }
    return latest

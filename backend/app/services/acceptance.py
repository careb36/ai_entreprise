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
the specification's to decide, never silently accepted. Before the tests
run, ``guard_test_files`` asks the daemon for the sha256 of the acceptance
test files as they are on disk (``workspace.hash``) and compares them with
what QA's delivery wrote; a file changed behind the loop's back is restored
from QA's deliverable, so no criterion is ever judged against a weakened
test, and the tampering is refuted as an edit.

``verified_criteria`` reads, per criterion, the latest observed result of an
implementation task's run: a criterion is ``verified`` only when its test
passed in an observed run.
"""

from __future__ import annotations

from typing import Any

from logging_config import logger
from models import AgentRun, BacklogItem, Sprint, Verification
from models.specification import Specification
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from services.criteria_tests import (
    ACCEPTANCE_KIND,
    CHECK_CRITERIA,
    RESULT_PASS,
    token,
)
from services.orchestration_metrics import VERDICT_VERIFIED

QA_ROLE = "QA"
TITLE_PREFIX = "Acceptance tests for "
_TASK_WORKER = "le-fabrique-task-worker"


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


def _test_tasks(
    session: Session, sprint_id: int
) -> list[tuple[BacklogItem, AgentRun | None]]:
    """The sprint's test-writing tasks, each with its latest run."""
    tasks = session.exec(
        select(BacklogItem).where(
            BacklogItem.sprint_id == sprint_id, BacklogItem.kind == ACCEPTANCE_KIND
        )
    ).all()
    return [
        (
            task,
            session.exec(
                select(AgentRun)
                .where(
                    AgentRun.external_ref == f"task-{task.id}",
                    AgentRun.producer == _TASK_WORKER,
                )
                .order_by(AgentRun.version.desc())
            ).first(),
        )
        for task in tasks
    ]


def test_files_of(
    session: Session, sprint_id: int, covers: list[str]
) -> tuple[list[str], list[str]]:
    """The files the sprint's test tasks of *covers* wrote (their latest run's
    declared files), and the criteria among *covers* that have a test task."""
    files: list[str] = []
    named: list[str] = []
    for task, run in _test_tasks(session, sprint_id):
        its = [str(c) for c in task.covers or []]
        if not set(its) & set(covers):
            continue
        named += [c for c in covers if c in its and c not in named]
        for path in (run.claims or {}).get("files") or [] if run else []:
            if isinstance(path, str) and path not in files:
                files.append(path)
    return files, named


def test_hashes_of(session: Session, sprint_id: int) -> dict[str, str]:
    """Per acceptance test file, the sha256 the daemon read back when QA's
    delivery wrote it (its latest run's verification evidence)."""
    hashes: dict[str, str] = {}
    for _task, run in _test_tasks(session, sprint_id):
        if run is None:
            continue
        verification = session.exec(
            select(Verification)
            .where(Verification.agent_run_id == run.id)
            .order_by(Verification.id.desc())
        ).first()
        evidence = (verification.evidence if verification else None) or {}
        for write in evidence.get("workspace_writes") or []:
            if (
                isinstance(write, dict)
                and write.get("status") == "written"
                and isinstance(write.get("path"), str)
                and isinstance(write.get("sha256"), str)
            ):
                hashes[write["path"]] = write["sha256"]
    return hashes


def test_sources_of(engine: Engine, sprint_id: int, paths: list[str]) -> dict[str, str]:
    """The content of the acceptance test files *paths*, from QA's deliverables:
    a whole file in its answer, else the text the daemon read back of a file
    QA changed with the edit tool (``tool_edit_files`` on its run's evidence,
    an observation; ``services/workspace_edits.py``).

    A file QA delivered as SEARCH/REPLACE edits has no source here: it is
    reported tampered and not restored, so ``check_untouched`` refutes."""
    from services import context_read
    from services.task_worker import parse_file_manifest

    sources: dict[str, str] = {}
    with Session(engine) as session:
        for _task, run in _test_tasks(session, sprint_id):
            if run is None:
                continue
            verification = session.exec(
                select(Verification)
                .where(Verification.agent_run_id == run.id)
                .order_by(Verification.id.desc())
            ).first()
            edited = context_read.edited_texts(
                verification.evidence if verification else None
            )
            whole = [
                t
                for t in parse_file_manifest(run.artifact_text or "")
                # Only a whole file can be restored: an edit is not the file.
                if "edits" not in t
            ]
            for target in edited + whole:  # The answer's own file wins.
                if target["path"] in paths:
                    sources[target["path"]] = target["content"]
    return sources


async def guard_test_files(
    nats_client: Any, engine: Engine, evidence: dict[str, Any], timeout: float
) -> None:
    """Before an implementation task's tests run: the acceptance test files on
    disk must be the ones QA delivered. The daemon reports their sha256
    (``workspace.hash``); a file that differs or is missing is restored from
    QA's deliverable and recorded in ``acceptance["tampered"]`` and
    ``acceptance["restored"]``, so ``check_untouched`` refutes the delivery
    and the criteria are judged against QA's tests. A daemon without the
    operation leaves things as they were."""
    from services.workspace_daemon import daemon_request

    acceptance = evidence.get("acceptance") or {}
    hashes = acceptance.get("test_hashes") or {}
    if not hashes or acceptance.get("kind") == ACCEPTANCE_KIND:
        return
    root = evidence.get("workspace_root")
    try:
        reply = await daemon_request(
            nats_client,
            "workspace.hash",
            {"workspace_root": root, "paths": list(hashes)},
            timeout,
        )
    except Exception as exc:  # noqa: BLE001 - An older daemon has no workspace.hash; the delivery is judged as before.
        logger.warning("Acceptance test files not checked on disk: %s", exc)
        return
    on_disk = {
        f.get("path"): f.get("sha256")
        for f in reply.get("files") or []
        if isinstance(f, dict)
    }
    tampered = [path for path, sha in hashes.items() if on_disk.get(path) != sha]
    if not tampered:
        return
    acceptance["tampered"] = tampered
    sources = test_sources_of(engine, acceptance.get("sprint_id"), tampered)
    files = [
        {"path": path, "content": sources[path], "overwrite_existing": True}
        for path in tampered
        if path in sources
    ]
    restored: list[str] = []
    if files:
        try:
            written = await daemon_request(
                nats_client,
                "workspace.generate",
                {
                    "workspace_root": root,
                    "files": files,
                    "interaction_id": "acceptance-restore",
                },
                timeout,
            )
            restored = [
                f["path"]
                for f in written.get("files") or []
                if isinstance(f, dict) and f.get("status") == "written"
            ]
        except Exception as exc:  # noqa: BLE001 - Recorded as tampered and not restored.
            logger.warning("Acceptance test files not restored: %s", exc)
    acceptance["restored"] = restored


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
                "sprint_id": sprint.id,
                "covers": covers,
                "kind": item.kind,
                "tested": [c for c in covers if c in tested],
                "all": every,
                "excused": [c for c in every if c not in done and c not in own],
                "test_files": files,
                "test_hashes": test_hashes_of(session, sprint.id),
            }
    except Exception as exc:  # noqa: BLE001 - Without it the delivery is judged as before.
        logger.debug("Acceptance evidence failed for item=%s: %s", item_id, exc)
        return None


# ---------------------------------------------------------------------------
# What the specification shows
# ---------------------------------------------------------------------------


def verified_criteria(
    session: Session, spec: Specification
) -> dict[str, dict[str, Any]]:
    """Per criterion id, the latest observed result of an implementation task's
    run (``result``, ``task_id``, ``verified``), from the Verifier's
    ``criteria_tests`` checks; criteria never tested are absent.

    Across the tasks covering a criterion, the most recent verification (not
    the task's position) gives ``result``; ``verified`` is true only when the
    test passed in a run whose whole verdict is verified, so a refuted
    delivery never verifies a criterion.
    """
    items = session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(Sprint.specification_id == spec.id)
    ).all()
    latest: dict[str, dict[str, Any]] = {}
    seen_at: dict[str, int] = {}
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
            if seen_at.get(cid, -1) > verification.id:
                continue
            seen_at[cid] = verification.id
            latest[cid] = {
                "result": result,
                "task_id": item.id,
                "verified": result == RESULT_PASS
                and verification.verdict == VERDICT_VERIFIED,
            }
    return latest

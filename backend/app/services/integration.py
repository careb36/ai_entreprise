"""Integration of a sprint's task branches into one branch (issue #314).

With the Git workflow on, each task is a branch (``fabrique/task-<id>``) and
nobody joins them. A sprint is not delivered until its work is integrated
and the whole suite passes on the integrated result.

``plan`` says which branches, in which order (dependencies first).
``integrate`` carries it out when a sprint closes, or when a Manager asks:
the workspace daemon merges the done tasks' branches into
``fabrique/sprint-<id>`` with real git (``vscode-daemon/sprint_integration.py``,
``workspace.git.integrate``), and, with ``DELIVERY_TESTS_ENABLED``, runs the
project's test command on the integrated worktree through the same path and
sandbox as a delivery's tests (``delivery_checks.run_delivery_tests``). The
backend never shells out. What the daemon answered is kept as a
``SprintIntegration``: the merges, the conflict (files and hunks) at the
first merge git could not make, and the test run. A conflict or a failing
suite waits for a person (``GET /v1/inbox``, ``integrations``). The branch is
published as one request for the sprint by ``services/integration_publish.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import weakref
from collections.abc import AsyncIterator
from typing import Any

from config import settings
from models import (
    AgentRun,
    BacklogItem,
    Company,
    ProjectDiscovery,
    Sprint,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from models.integration import WAITS_FOR_A_PERSON, SprintIntegration
from sqlmodel import Session, create_engine, select

from services import delivery_checks, integration_events, locks, workspace_daemon
from services.agent_service import AgentService
from services.spec_planning import acceptance_summaries

BRANCH_PREFIX = "fabrique/task-"
INTEGRATION_BRANCH_PREFIX = "fabrique/sprint-"


def task_branch(item: BacklogItem) -> str:
    return f"{BRANCH_PREFIX}{item.id}"


def integration_branch(sprint: Sprint) -> str:
    return f"{INTEGRATION_BRANCH_PREFIX}{sprint.id}"


def _topological(items: list[BacklogItem]) -> list[BacklogItem]:
    by_id = {i.id: i for i in items}
    done = set()
    result: list[BacklogItem] = []
    remaining = list(items)
    # Repeatedly take items whose dependencies are already placed.
    progress = True
    while remaining and progress:
        progress = False
        still: list[BacklogItem] = []
        for item in remaining:
            deps = []
            for d in item.depends_on or []:
                try:
                    deps.append(int(d))
                except (TypeError, ValueError):
                    continue
            if all(d in done or d not in by_id for d in deps):
                result.append(item)
                done.add(item.id)
                progress = True
            else:
                still.append(item)
        remaining = still
    # Anything left (cycle) goes last, in id order.
    result.extend(sorted(remaining, key=lambda i: i.id or 0))
    return result


def plan(session: Session, sprint_id: int) -> dict[str, Any]:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise ValueError("sprint not found")
    items = session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
    ).all()
    ordered = _topological(list(items))
    branches = [
        {
            "task_id": i.id,
            "title": i.title,
            "status": i.status,
            "branch": task_branch(i),
        }
        for i in ordered
    ]
    by_id = {i.id: i for i in items}
    unmet = []
    for i in items:
        for d in i.depends_on or []:
            try:
                did = int(d)
            except (TypeError, ValueError):
                continue
            if did not in by_id:
                unmet.append({"task_id": i.id, "missing_dependency": did})
    return {
        "sprint_id": sprint.id,
        "sprint_name": sprint.sprint_name,
        "integration_branch": integration_branch(sprint),
        "task_branches": branches,
        "merge_order": [i.id for i in ordered],
        "unmet_dependencies": unmet,
    }


# --- the integration itself ------------------------------------------------

# A merge per task branch and, on a large repository, a worktree to create.
INTEGRATE_TIMEOUT_SECONDS = 150.0
TRIGGERS = ("sprint_closed", "manual")
# One integration of a sprint at a time: merge, test and record together.
# In this process an asyncio lock; across replicas a Postgres advisory lock.
_LOCKS: dict[int, asyncio.Lock] = {}
# Integrations running at once in this process, each holding a lock connection.
MAX_CONCURRENT = max(1, int(os.getenv("INTEGRATION_MAX_CONCURRENT", "2")))
_SLOTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
# Postgres engines for lock connections only, by database URL.
_LOCK_ENGINES: dict[str, Any] = {}


class IntegrationRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _lock_engine(engine: Any) -> Any:
    """The engine lock connections come from: on Postgres a small pool of its
    own, so integrations waiting on the daemon never hold the application's."""
    if not locks.is_postgres(engine):
        return engine
    key = engine.url.render_as_string(hide_password=False)
    if key not in _LOCK_ENGINES:
        _LOCK_ENGINES[key] = create_engine(
            engine.url,
            pool_size=MAX_CONCURRENT,
            max_overflow=0,
            pool_pre_ping=True,
        )
    return _LOCK_ENGINES[key]


@contextlib.asynccontextmanager
async def _database_lock(engine: Any, sprint_id: int) -> AsyncIterator[bool]:
    """Whether this process owns the sprint's integration across replicas.

    ``locks.held``: a session advisory lock on Postgres, held for the attempt
    and released if the process dies. Taken and released in a thread, so a
    connection checkout never blocks the event loop.
    """
    held = locks.held(_lock_engine(engine), locks.SPRINT_INTEGRATION, sprint_id)
    entering = asyncio.ensure_future(asyncio.to_thread(held.__enter__))
    try:
        owned = await asyncio.shield(entering)
    except asyncio.CancelledError:
        # Cancelled while the lock was being taken: release it once it is.
        with contextlib.suppress(Exception):
            await entering
            await asyncio.to_thread(held.__exit__, None, None, None)
        raise
    try:
        yield owned
    finally:
        await asyncio.to_thread(held.__exit__, None, None, None)


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _SLOTS:
        _SLOTS[loop] = asyncio.Semaphore(MAX_CONCURRENT)
    return _SLOTS[loop]


@contextlib.asynccontextmanager
async def sprint_lock(engine: Any, sprint_id: int) -> AsyncIterator[bool]:
    """Hold the sprint's integration; yields False when another replica holds it.

    At most ``MAX_CONCURRENT`` integrations run at a time in this process.
    """
    async with (
        _LOCKS.setdefault(sprint_id, asyncio.Lock()),
        _slots(),
        _database_lock(engine, sprint_id) as owned,
    ):
        yield owned


def _project_path(session: Session, company_id: int) -> str | None:
    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == company_id)
        .order_by(ProjectDiscovery.created_at.desc(), ProjectDiscovery.id.desc())
    ).first()
    if discovery is not None and discovery.project_path:
        return discovery.project_path
    return os.getenv("AGENT_WORKSPACE_ROOT") or None


def _task_evidence(
    session: Session, company_id: int, task_id: int
) -> tuple[int | None, int | None]:
    """The task worker's latest run of the task and its latest verification, now."""
    run = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{task_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.id.desc())
    ).first()
    if run is None:
        return None, None
    verification = session.exec(
        select(Verification)
        .where(
            Verification.agent_run_id == run.id,
            Verification.company_id == company_id,
        )
        .order_by(Verification.observed_at.desc(), Verification.id.desc())
    ).first()
    return run.id, verification.id if verification else None


def integration_spec(
    session: Session, sprint_id: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ``workspace.git.integrate`` request for the sprint, and what to record it against."""
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise IntegrationRefused(404, "Sprint not found")
    company = session.get(Company, sprint.company_id)
    if company is None or not company.git_workflow:
        raise IntegrationRefused(
            409, "The company's Git workflow is off: its tasks have no branches"
        )
    root = _project_path(session, company.id)
    if not root:
        raise IntegrationRefused(409, "The company has no project folder")
    tasks = plan(session, sprint_id)["task_branches"]
    done = [t for t in tasks if t["status"] == "done"]
    if not done:
        raise IntegrationRefused(409, "No task of this sprint is done")
    # As it stood at integration; a later run does not rewrite it.
    acceptance = acceptance_summaries(
        session, [session.get(BacklogItem, t["task_id"]) for t in done]
    )
    spec = {
        "workspace_root": root,
        "branch": integration_branch(sprint),
        "base": company.git_base_branch or "",
        # Dependencies first: a task's branch already holds the ones it built on.
        "merge": [t["branch"] for t in done],
        # This attempt's own snapshot worktree on the daemon; a retry gets another.
        "attempt": secrets.token_hex(8),
    }
    context = {
        "company_id": company.id,
        "sprint_id": sprint.id,
        "tasks": [
            {
                "task_id": t["task_id"],
                "title": t["title"],
                "branch": t["branch"],
                **dict(
                    zip(
                        ("run_id", "verification_id"),
                        _task_evidence(session, company.id, t["task_id"]),
                        strict=True,
                    )
                ),
                "acceptance": acceptance.get(t["task_id"], ""),
            }
            for t in done
        ],
        "left_out": [
            {"task_id": t["task_id"], "title": t["title"], "status": t["status"]}
            for t in tasks
            if t["status"] != "done"
        ],
    }
    return spec, context


async def _run_suite(
    nats_client: Any, engine: Any, context: dict[str, Any], reply: dict[str, Any]
) -> dict[str, Any]:
    """The project's test command at the integrated commit, as a delivery's tests run."""
    if not settings.DELIVERY_TESTS_ENABLED:
        return {"command": None, "reason": "DELIVERY_TESTS_ENABLED is off"}
    # The detached worktree at the commit: a later integration cannot move it.
    worktree = reply.get("test_worktree")
    if not worktree:
        return {"command": None, "reason": "the daemon named no worktree at the commit"}
    # An empty acceptance asks for per-test lines, so the failing tests are named.
    evidence: dict[str, Any] = {"workspace_root": worktree, "acceptance": {"all": []}}
    await delivery_checks.run_delivery_tests(
        nats_client,
        {"id": context["sprint_id"], "company_id": context["company_id"]},
        evidence,
        engine,
        # The tests alone: the integration's status reads only the test run.
        application=False,
    )
    test_run = dict(evidence.get("test_run") or {})
    test_run.pop("criteria", None)
    if test_run.get("command") and test_run.get("exit_code") is not None:
        test_run["commit"] = reply.get("commit")
    test_run["workspace_root"] = worktree
    return test_run


def status_of(
    conflict: Any, test_run: dict[str, Any] | None, complete: bool = True
) -> str:
    if conflict:
        return "conflict"
    if not complete:
        return "incomplete"
    test_run = test_run or {}
    exit_code = test_run.get("exit_code")
    if test_run.get("timed_out") or (isinstance(exit_code, int) and exit_code != 0):
        return "tests_failed"
    return "tests_passed" if exit_code == 0 else "untested"


def _task_states(
    context: dict[str, Any], reply: dict[str, Any], conflict: dict | None
) -> list[dict[str, Any]]:
    merged = {
        m.get("branch"): m.get("commit")
        for m in reply.get("merged") or []
        if isinstance(m, dict)
    }
    state_of = {b: "merged" for b in merged}
    for key, state in (
        ("already_merged", "already_merged"),
        ("missing", "missing"),
        ("refused", "refused"),
        ("not_attempted", "not_attempted"),
    ):
        state_of.update({b: state for b in reply.get(key) or [] if isinstance(b, str)})
    if conflict:
        state_of[conflict.get("branch")] = "conflict"
    sources = reply.get("sources") if isinstance(reply.get("sources"), dict) else {}
    return [
        {
            **task,
            # Not named by the daemon: the request ended before it.
            "state": state_of.get(task["branch"], "not_attempted"),
            "source_commit": sources.get(task["branch"]),
            "merge_commit": merged.get(task["branch"]),
        }
        for task in context["tasks"]
    ]


def record(
    session: Session,
    spec: dict[str, Any],
    context: dict[str, Any],
    reply: dict[str, Any],
    test_run: dict[str, Any] | None,
    *,
    trigger: str,
    requested_by: str,
    job: SprintIntegration | None = None,
) -> SprintIntegration:
    """Keep the daemon's answer, audited; an answer that is not about this branch is refused.

    *job*, the row a closed sprint's integration was kept as while it ran, is
    filled in place, so the attempt it stood for ends there.
    """
    if reply.get("branch") != spec["branch"] or not reply.get("commit"):
        raise IntegrationRefused(502, "The workspace daemon sent an unreadable answer")
    task_of = {t["branch"]: t["task_id"] for t in context["tasks"]}
    conflict = reply.get("conflict")
    if isinstance(conflict, dict):
        conflict = {**conflict, "task_id": task_of.get(conflict.get("branch"))}
    else:
        conflict = None
    merged = [
        {
            "task_id": task_of.get(m.get("branch")),
            "branch": m.get("branch"),
            "commit": m.get("commit"),
        }
        for m in reply.get("merged") or []
        if isinstance(m, dict)
    ]
    details = {
        key: [
            {"task_id": task_of.get(b), "branch": b}
            for b in reply.get(key) or []
            if isinstance(b, str)
        ]
        for key in ("already_merged", "missing", "refused", "not_attempted")
    }
    # An older daemon does not say: whatever is missing or refused decides.
    complete = not details["missing"] and not details["refused"]
    if job is not None:
        details["attempts"] = (job.details or {}).get("attempts")
    fields = {
        "company_id": context["company_id"],
        "sprint_id": context["sprint_id"],
        "trigger": trigger,
        "requested_by": requested_by,
        "status": status_of(conflict, test_run, complete),
        "branch": spec["branch"],
        "base": str(reply.get("base") or spec["base"] or ""),
        "repository": reply.get("repository"),
        "worktree": reply.get("worktree"),
        "start_commit": reply.get("start"),
        "commit": str(reply["commit"]),
        "merged": merged,
        "conflict": conflict,
        "test_run": test_run,
        "tested_commit": (test_run or {}).get("commit"),
        "tasks": _task_states(context, reply, conflict),
        # Where the base stood against its remote (git_base): an observation.
        "details": details
        | {"left_out": context["left_out"]}
        | {k: reply[k] for k in ("base_status", "base_merge") if reply.get(k)},
    }
    row = session.get(SprintIntegration, job.id) if job is not None else None
    if row is None:
        row = SprintIntegration(**fields)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    session.add(row)
    session.flush()  # each task's part becomes its event (issue #324)
    integration_events.record_for_tasks(session, row)
    _audit(session, row, "sprint_integrated")
    return row


def _audit(session: Session, row: SprintIntegration, action: str) -> None:
    session.add(row)
    session.flush()
    requested_by = row.requested_by
    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"sprint:{row.sprint_id}:integration",
        agent_id=requested_by
        if requested_by.startswith("human:")
        else f"system:{requested_by}",
        action=action,
        details={
            "company_id": str(row.company_id),
            "sprint_id": row.sprint_id,
            "integration_id": row.id,
            "trigger": row.trigger,
        },
        result={
            "status": row.status,
            "branch": row.branch,
            "commit": row.commit,
            "tested_commit": row.tested_commit,
            "merged": [m["branch"] for m in row.merged or []],
            "conflict_files": (row.conflict or {}).get("files"),
            "tests_exit_code": (row.test_run or {}).get("exit_code"),
            "reason": (row.details or {}).get("reason"),
        },
        decision_signature=None,
        cost_tokens=0,
    )
    row.audit_log_id = audit_log.id
    session.add(row)
    session.commit()
    session.refresh(row)


async def integrate(
    nats_client: Any,
    engine: Any,
    sprint_id: int,
    *,
    trigger: str,
    requested_by: str,
) -> SprintIntegration:
    """Merge the sprint's done task branches, run the suite on the result, record both.

    Raises ``IntegrationRefused`` when there is nothing to integrate or
    another replica is integrating the sprint, and
    ``workspace_daemon.DaemonUnavailable`` / ``DaemonError`` when the daemon
    could not do it; nothing is recorded then. One integration of a sprint
    runs at a time, so what is recorded is what that attempt merged and tested.
    """
    async with sprint_lock(engine, sprint_id) as owned:
        if not owned:
            raise IntegrationRefused(
                409, "An integration of this sprint is already running"
            )
        return await attempt(
            nats_client, engine, sprint_id, trigger=trigger, requested_by=requested_by
        )


async def attempt(
    nats_client: Any,
    engine: Any,
    sprint_id: int,
    *,
    trigger: str,
    requested_by: str,
    job: SprintIntegration | None = None,
) -> SprintIntegration:
    """One integration, for a caller that holds ``sprint_lock``."""
    with Session(engine) as session:
        spec, context = integration_spec(session, sprint_id)
    reply = await workspace_daemon.daemon_request(
        nats_client, "workspace.git.integrate", spec, INTEGRATE_TIMEOUT_SECONDS
    )
    test_run = None
    if reply.get("ok") and reply.get("commit"):
        test_run = await _run_suite(nats_client, engine, context, reply)
    with Session(engine) as session:
        return record(
            session,
            spec,
            context,
            reply,
            test_run,
            trigger=trigger,
            requested_by=requested_by,
            job=job,
        )


# --- what people read --------------------------------------------------------


def latest(session: Session, sprint_id: int) -> SprintIntegration | None:
    return session.exec(
        select(SprintIntegration)
        .where(SprintIntegration.sprint_id == sprint_id)
        .order_by(SprintIntegration.created_at.desc(), SprintIntegration.id.desc())
    ).first()


def waiting_for_a_person(session: Session, company_id: int) -> list[dict[str, Any]]:
    """Each sprint whose latest integration stopped, was incomplete or could not start."""
    rows = session.exec(
        select(SprintIntegration, Sprint)
        .join(Sprint, Sprint.id == SprintIntegration.sprint_id)
        .where(SprintIntegration.company_id == company_id, Sprint.archived_at.is_(None))
        .order_by(SprintIntegration.created_at.desc(), SprintIntegration.id.desc())
    ).all()
    seen: set[int] = set()
    waiting = []
    for row, sprint in rows:
        if row.sprint_id in seen:
            continue
        seen.add(row.sprint_id)
        if row.status not in WAITS_FOR_A_PERSON:
            continue
        details = row.details or {}
        waiting.append(
            {
                "id": row.id,
                "sprint_id": sprint.id,
                "sprint_name": sprint.sprint_name,
                "status": row.status,
                "branch": row.branch,
                "reason": details.get("reason"),
                "conflict_task_id": (row.conflict or {}).get("task_id"),
                "conflict_files": (row.conflict or {}).get("files") or [],
                "failing_tests": (row.test_run or {}).get("failing_tests") or [],
                "missing_task_ids": [
                    e.get("task_id")
                    for e in (details.get("missing") or [])
                    + (details.get("refused") or [])
                ],
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return waiting

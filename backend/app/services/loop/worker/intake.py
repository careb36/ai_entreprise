"""The consumer, the claim, the queue markers and dispatch (ADR 0014, intake).

``process_backlog_tasks`` is the worker that ``lifecycle`` starts: it binds
the Developer's tools to this loop, consumes ``task.assign`` as the durable
consumer ``task-worker`` (and ``workspace.delivered`` as ``delivery-results``
when JetStream is on), then runs the review cycle every 30 seconds
(``review._review_cycle``). Each assignment is handled under the task's lock
(``replicas.task_lock``, ``_on_task_assign_held``) by ``_on_task_assign``:

- ``_claim``: the task's context (``task_run._load_task_context``), its queue
  marker cleared, a frozen task kept blocked, a task whose dependencies are
  not done left for later (``dependencies.ready``), and the conditional claim
  from ``todo`` (``replicas.claim_todo``);
- ``task_run._run_claimed_task``: the run itself, up to the handoff to QA;
- ``_record_failure``: a provider out of credit pauses the sprint and puts
  the task back to ``todo``; any other failure blocks it with the error.

The handler records its own failures, so the consumer acknowledges a message
once the handler returns. ``_dispatch_todo_items`` (called by the review cycle)
queues the ``todo`` tasks that can start, once per cooldown
(``_queued_recently``, ``_mark_queued``, ``_clear_queue_marker``).

Moved out of ``task_worker``, which still re-exports these names.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from database import engine
from logging_config import setup_logger
from models import BacklogItem, Sprint
from nats_client import NATSClient
from sqlmodel import Session, select

from services.evidence import delivery_gate
from services.loop import preconditions, replicas, run_window_gate, sprint_control
from services.loop.worker import board_writes, deliver, execute, review, task_run
from services.platform.glassroom import publish_glassroom
from services.tasks import dependencies, task_events, task_projection
from services.tools import developer_tools

logger = setup_logger("fabrique-task-worker")


def normalize_utc(value: datetime | None) -> datetime | None:
    """A datetime loaded from the database as timezone-aware UTC; ``None`` stays ``None``."""
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


DISPATCH_REQUEUE_COOLDOWN_SECONDS = int(
    os.getenv("DISPATCH_REQUEUE_COOLDOWN_SECONDS", "90")
)
LAST_AUTONOMOUS_QUEUE_AT: dict[int, datetime] = {}

DELIVERY_QUEUED = delivery_gate.DELIVERY_QUEUED
TASK_ASSIGN_CONSUMER = "task-worker"
DELIVERY_RESULTS_CONSUMER = "delivery-results"
# A task can take several LLM calls, console assistants and a test run; the
# consumer keeps the message claimed while the handler runs, so this only
# bounds how long a crashed worker holds an assignment.
TASK_ASSIGN_ACK_WAIT_SECONDS = 600.0


def _queued_recently(item_id: int, now: datetime) -> bool:
    """Avoid publishing duplicate queue events for the same todo item every review tick."""
    last_dispatched_at = LAST_AUTONOMOUS_QUEUE_AT.get(item_id)
    if not last_dispatched_at:
        return False
    return (
        now - last_dispatched_at
    ).total_seconds() < DISPATCH_REQUEUE_COOLDOWN_SECONDS


def _mark_queued(item_id: int, now: datetime) -> None:
    LAST_AUTONOMOUS_QUEUE_AT[item_id] = now


def _clear_queue_marker(item_id: int) -> None:
    LAST_AUTONOMOUS_QUEUE_AT.pop(item_id, None)


async def process_backlog_tasks(nats_client: NATSClient) -> None:
    """
    Background coroutine that subscribes to ``task.assign`` and processes
    each backlog item by invoking the appropriate AI agent.

    The LLM call is offloaded to a thread via ``asyncio.to_thread()`` to keep
    the event loop free for other NATS callbacks and SSE streaming.
    """
    # Durable: assignments published while this worker was down are processed
    # when it starts.  The handler records its own failures on the board, so a
    # message is acknowledged once the handler returns.
    developer_tools.bind(
        nats_client, asyncio.get_running_loop(), deliver._resolve_workspace_root
    )
    await _consume_assignments(nats_client)
    await _consume_delivery_results(nats_client)

    await review._review_cycle(nats_client)

    # Periodic review loop — processes items stuck in in_review or blocked.
    await asyncio.sleep(10)  # Let initial tasks start first
    while True:
        try:
            await review._review_cycle(nats_client)
        except Exception as e:  # noqa: BLE001 - Keep the review polling loop alive across cycle failures.
            logger.error("Review cycle error: %s", e)
        await asyncio.sleep(30)  # Run every 30s


async def _consume_assignments(nats_client: NATSClient) -> None:
    """``task.assign`` as the durable consumer ``task-worker``."""

    async def _on_task_assign_held_here(msg, data=None):
        await _on_task_assign_held(nats_client, msg, data)

    await nats_client.consume(
        "task.assign",
        TASK_ASSIGN_CONSUMER,
        _on_task_assign_held_here,
        ack_wait_seconds=TASK_ASSIGN_ACK_WAIT_SECONDS,
    )
    logger.info("Task Worker consuming task.assign")


async def _consume_delivery_results(nats_client: NATSClient) -> None:
    """``workspace.delivered`` as ``delivery-results``, only with JetStream."""

    async def _on_delivered(msg, data):
        await deliver._on_delivery_result(nats_client, data)

    # Without JetStream nothing is ever queued, so there are no results either.
    if nats_client.jetstream_enabled:
        await nats_client.consume(
            "workspace.delivered", DELIVERY_RESULTS_CONSUMER, _on_delivered
        )


async def _on_task_assign_held(
    nats_client: NATSClient, msg: Any, data: dict[str, Any] | None = None
) -> None:
    """Held while this replica works on the task: a restarting one leaves it alone."""
    with replicas.task_lock(engine, (data or {}).get("backlog_item_id")) as mine:
        if mine:
            await _on_task_assign(nats_client, msg, data)


async def _on_task_assign(
    nats_client: NATSClient, msg: Any, data_from_nats: dict[str, Any] | None = None
) -> None:
    """One ``task.assign``: claim the task and run it; record a failure on the board."""
    data: dict[str, Any] = {}
    try:
        data = data_from_nats or json.loads(msg.data.decode())
        ctx = await _claim(data)
        if not ctx:
            return
        await task_run._run_claimed_task(nats_client, data.get("backlog_item_id"), ctx)
    except Exception as e:  # noqa: BLE001 - _record_failure logs it with its traceback and records it on the board.
        await _record_failure(nats_client, data, e)


async def _claim(data: dict[str, Any]) -> dict[str, Any] | None:
    """The task's context once this replica claimed it, else nothing."""
    backlog_item_id = data.get("backlog_item_id")
    sprint_id = data.get("sprint_id")

    if not backlog_item_id:
        logger.warning("task.assign message missing backlog_item_id: %s", data)
        return None

    logger.info("Received task.assign: item=%s sprint=%s", backlog_item_id, sprint_id)

    ctx = await asyncio.to_thread(task_run._load_task_context, backlog_item_id)
    if not ctx:
        return None

    item = ctx["item"]
    _clear_queue_marker(item["id"])

    if task_projection.record_from_dict(item).frozen():
        await asyncio.to_thread(task_run._sync_manual_freeze_state, item["id"])
        logger.info("Skipping manually frozen assignment for item=%s", item["id"])
        return None

    # A task that waits for others is queued once they are done.
    if not await asyncio.to_thread(dependencies.ready, engine, item["id"]):
        return None
    # Outside the company's run window the next model turn waits (issue #736).
    if item.get("status") == "todo" and await asyncio.to_thread(
        run_window_gate.hold_task, engine, item["id"]
    ):
        return None
    # A company that runs with tests off or agent code on the host needs an
    # Owner's acknowledgement; without one the sprint pauses (issue #766).
    if item.get("status") == "todo" and await asyncio.to_thread(
        preconditions.hold_task, engine, item["id"]
    ):
        return None
    # Guard against duplicate task.assign events for the same item: one
    # replica claims it, with a conditional update from todo (issue #328),
    # recorded as its ``claimed`` event (issue #324).
    if item.get("status") != "todo" or not await asyncio.to_thread(
        replicas.claim_todo, engine, item["id"]
    ):
        logger.info(
            "Skipping duplicate assignment for item=%s with status=%s",
            item["id"],
            item.get("status"),
        )
        return None
    return ctx


# A provider that did not answer in time, a proxy that dropped the call (a
# restart), a rate limit or a 5xx: the next attempt may well answer. Each was
# blocking the task for a person (sprint 3, tasks 12 and 15, issue #766).
TRANSIENT_LLM_MARKERS = (
    "upstream_timeout",
    "proxy_connection_lost",
    "llm-proxy unreachable",
    "empty_content",
    " 429 ",
    " 500 ",
    " 502 ",
    " 503 ",
    " 504 ",
)
TRANSIENT_RETRY_PREFIX = "Transient model failure, queued again"
MAX_TRANSIENT_RETRIES = 3


def is_transient_llm_failure(message: str) -> bool:
    return any(marker in f" {message} " for marker in TRANSIENT_LLM_MARKERS)


def requeue_after_transient_failure(item_id: int, message: str) -> bool:
    """A task whose model call failed transiently goes back to ``todo``, at
    most ``MAX_TRANSIENT_RETRIES`` times; ``False`` leaves it to be blocked."""
    if not is_transient_llm_failure(message):
        return False
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if item is None:
            return False
        done = sum(
            1
            for c in item.comments or []
            if str(c.get("text") or "").startswith(TRANSIENT_RETRY_PREFIX)
        )
        if done >= MAX_TRANSIENT_RETRIES:
            return False
        task_events.record(
            session,
            item,
            "retried",
            task_events.SYSTEM,
            status="todo",
            text=(
                f"{TRANSIENT_RETRY_PREFIX} ({done + 1}/{MAX_TRANSIENT_RETRIES}): "
                f"{message[:400]}"
            ),
        )
        session.commit()
    logger.warning("Transient model failure on item=%s; queued again", item_id)
    return True


async def _record_failure(
    nats_client: NATSClient, data: dict[str, Any], e: Exception
) -> None:
    """No credit pauses the sprint; a transient model failure queues the task
    again (a few times); any other failure blocks the task with the error."""
    logger.exception(
        "Error processing task.assign (item=%s): %s",
        data.get("backlog_item_id", "?"),
        e,
    )

    credit = getattr(e, "credit_provider", None)
    if credit and data.get("backlog_item_id") and data.get("sprint_id"):
        # No credit: the sprint pauses and the task waits as todo, so
        # it runs again when a person resumes the sprint.
        await asyncio.to_thread(
            sprint_control.pause_task_for_credit,
            engine,
            data["backlog_item_id"],
            data["sprint_id"],
            credit,
        )
        await sprint_control.publish_credit_pause(
            nats_client, data["sprint_id"], credit
        )
        return

    if (
        isinstance(e, execute.LLMCallError)
        and data.get("backlog_item_id")
        and await asyncio.to_thread(
            requeue_after_transient_failure, data["backlog_item_id"], str(e)
        )
    ):
        return

    # Mark as blocked on failure.
    if data.get("backlog_item_id"):
        try:
            await asyncio.to_thread(
                board_writes._update_item_status,
                data["backlog_item_id"],
                "blocked",
                comment_text=(
                    str(e)[:500]
                    if isinstance(e, execute.LLMCallError)
                    else f"Agent error: {str(e)[:500]}"
                ),
                comment_author="System",
                kind="blocked",
            )
        except Exception:  # noqa: BLE001, S110 - Recovery bookkeeping must not mask the task failure.
            pass

    if data.get("sprint_id"):
        await publish_glassroom(
            nats_client,
            data["sprint_id"],
            {
                "type": "error",
                "semantic_type": "task.error",
                "source": "task_worker",
                "task_id": data.get("backlog_item_id"),
                "task_status": "blocked",
                "message": f"❌ Task failed: {str(e)[:200]}",
                "message_key": "glassroom.events.failed",
                "message_params": {"error": str(e)[:200]},
            },
        )


async def _dispatch_todo_items(nats_client: NATSClient) -> None:
    """Find todo items and publish task.assign events for them."""
    # Tasks waiting on one a person must act on are blocked with the reason;
    # those whose wait ended come back to todo, and are queued below.
    await asyncio.to_thread(dependencies.settle_active, engine)
    with Session(engine) as session:
        todo_items = session.exec(
            select(BacklogItem).where(BacklogItem.status == "todo")
        ).all()
        histories = task_projection.histories_for(session, list(todo_items))
        now = run_window_gate.utc_now()
        for item in todo_items:
            item_sprint = session.get(Sprint, item.sprint_id)
            if item_sprint is not None and sprint_control.is_halted(item_sprint.status):
                continue
            if not dependencies.is_ready(session, item):
                continue
            if item_sprint is not None and run_window_gate.gate_task(
                session, item, item_sprint, now
            ):
                continue
            dispatch_time = datetime.now(UTC)
            record = task_projection.record_of(session, item, histories)
            if record.frozen():
                task_events.record(session, item, "blocked", "system", status="blocked")
                logger.info(
                    "Keeping manually frozen todo item=%s blocked during dispatch",
                    item.id,
                )
                continue

            existing_deliverable = record.deliverable()
            if existing_deliverable:
                task_events.record(
                    session, item, "recovered", "system", status="in_review"
                )
                _clear_queue_marker(item.id)
                await publish_glassroom(
                    nats_client,
                    item.sprint_id,
                    {
                        "type": "status",
                        "semantic_type": "task.recovered",
                        "source": "task_worker",
                        "agent": "System",
                        "role": "System",
                        "task_id": item.id,
                        "task_status": "in_review",
                        "phase": "todo_recovery",
                        "task": item.title,
                        "message": f"♻️ Recovered todo task with existing deliverable: {item.title} moved to QA review.",
                        "message_key": "glassroom.events.recovered_to_review",
                        "message_params": {"task": item.title},
                        "preview": existing_deliverable[:200],
                    },
                )
                logger.info(
                    "Recovered todo item=%s to in_review because a deliverable already exists",
                    item.id,
                )
                continue

            if _queued_recently(item.id, dispatch_time):
                logger.debug(
                    "Skipping duplicate autonomous queue publish for item=%s", item.id
                )
                continue

            # Durable: one stored assignment per todo state of the item, even
            # though this runs every review cycle while the worker is busy.
            updated = normalize_utc(item.updated_at)
            await nats_client.publish(
                "task.assign",
                {
                    "agent": item.assignee or "Dev",
                    "sprint_id": item.sprint_id,
                    "interaction_id": str(uuid4()),
                    "backlog_item_id": item.id,
                },
                msg_id=f"assign-{item.id}-{updated.isoformat() if updated else '-'}",
            )
            _mark_queued(item.id, dispatch_time)
            await publish_glassroom(
                nats_client,
                item.sprint_id,
                {
                    "type": "status",
                    "semantic_type": "task.queued",
                    "source": "task_worker",
                    "agent": "System",
                    "role": "System",
                    "task_id": item.id,
                    "task_status": "todo",
                    "phase": "queued",
                    "task": item.title,
                    "message": f"📬 Task queued for autonomous execution: {item.title} → {item.assignee or 'Dev'}",
                    "message_key": "glassroom.events.queued",
                    "message_params": {
                        "task": item.title,
                        "agent": item.assignee or "Dev",
                    },
                },
            )
        session.commit()

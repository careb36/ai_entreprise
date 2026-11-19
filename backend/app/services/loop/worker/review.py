"""The review cycle (ADR 0014, the task worker's review stage).

One replica at a time (``locks.REVIEW_CYCLE``) runs ``_review_cycle``: a dead
replica's tasks go back to ``todo`` first (``replicas.recover_stale``), then
``_review_cycle_body`` advances the board in one session, in these steps:

- ``_review_delivered``: each ``in_review`` task of a running sprint goes
  through ``_review_task``: its reviewer (``_reviewer_of``), a frozen task kept
  blocked, the review retries (``_block_exhausted_reviews``) and a missing
  deliverable (``_block_missing_deliverable``), the delivery gate, evidence
  before opinion (``_send_back_from_delivery_gate``), then QA's review
  (``qa_review._qa_review``);
- ``_retry_blocked``: a blocked task with a deliverable and no block reason
  goes back to QA (``_recover_to_review``), one the worker retries is queued
  again (``_requeue_blocked``), and the sprints with nothing left to move are
  named;
- ``_closing_candidates``: those that settled, which then close
  (``close._finalize_sprint_if_ready``); last, the ``todo`` tasks are
  dispatched (the intake's ``_dispatch_todo_items``, imported when used,
  since ``intake`` imports this module).

Moved out of ``task_worker``, which still re-exports these names.
"""

import asyncio
from typing import Any

from database import engine
from logging_config import setup_logger
from models import Agent, BacklogItem, Sprint
from nats_client import NATSClient
from sqlmodel import Session, select

from services.evidence import delivery_gate
from services.loop import replicas, run_window_gate, sprint_control
from services.loop.worker import close, messages, qa_review, record
from services.models import model_routing
from services.platform import locks
from services.platform.glassroom import publish_glassroom
from services.tasks import dependencies, task_events, task_projection
from services.tasks.board import DELIVERY_GATE_AUTHOR, SCRUM_MASTER_ROLE

logger = setup_logger("fabrique-task-worker")

MAX_QA_REVIEW_FAILURES = 3


def _intake() -> Any:
    """The intake stage, imported when used (it imports this module)."""
    from services.loop.worker import intake

    return intake


async def _review_cycle(nats_client: NATSClient) -> None:
    """One replica at a time runs the review cycle; the others skip the round (issue #328)."""
    with locks.held(engine, locks.REVIEW_CYCLE) as mine:
        if mine:  # a dead replica's tasks first, so the dispatch below queues them
            await replicas.recover_stale(engine, nats_client)
            await _review_cycle_body(nats_client)


async def _review_cycle_body(nats_client: NATSClient) -> None:
    """
    Periodic cycle that advances the sprint board:
    - ``in_review`` items get reviewed by QA and moved to ``done``.
    - ``blocked`` items get retried once (moved back to ``todo`` for re-dispatch).
    """
    # Items blocked by QA in this same cycle must not be auto-retried below.
    quality_gate_blocked_ids: set[int] = set()

    with Session(engine) as session:
        await _review_delivered(session, nats_client, quality_gate_blocked_ids)
        settled_sprint_ids = await _retry_blocked(
            session, nats_client, quality_gate_blocked_ids
        )
        closing_candidates = _closing_candidates(session, settled_sprint_ids)

    # A sprint whose last open task just got blocked for a person closes now,
    # with its retrospective; approvals close theirs on the spot.
    for sprint_id in closing_candidates:
        await close._finalize_sprint_if_ready(nats_client, sprint_id)

    # Re-dispatch any items that just went back to todo
    await _intake()._dispatch_todo_items(nats_client)


async def _review_delivered(
    session: Session, nats_client: NATSClient, quality_gate_blocked_ids: set[int]
) -> None:
    """Each ``in_review`` task of a running sprint is reviewed (``_review_task``)."""
    # --- Review: in_review → done ---
    in_review = session.exec(
        select(BacklogItem).where(BacklogItem.status == "in_review")
    ).all()
    # Every decision below reads the task's events (one query for all).
    histories = task_projection.histories_for(session, list(in_review))

    for item in in_review:
        sprint = session.get(Sprint, item.sprint_id)
        if not sprint or sprint_control.is_halted(sprint.status):
            continue
        # QA's review is a model turn: outside the run window it waits (#736).
        if run_window_gate.gate_task(session, item, sprint):
            session.commit()
            continue
        await _review_task(
            session, nats_client, item, sprint, histories, quality_gate_blocked_ids
        )


async def _review_task(
    session: Session,
    nats_client: NATSClient,
    item: BacklogItem,
    sprint: Sprint,
    histories: Any,
    quality_gate_blocked_ids: set[int],
) -> None:
    """One delivered task: the checks before QA, the delivery gate, then QA's review."""
    reviewer_dict = _reviewer_of(session, sprint)

    task_record = task_projection.record_of(session, item, histories)
    if task_record.frozen():
        task_events.record(session, item, "blocked", "system", status="blocked")
        session.commit()
        logger.info("Skipping QA review for manually frozen item=%s", item.id)
        return

    deliverable = task_record.deliverable()
    # Prevent infinite review loops when QA repeatedly fails.
    qa_review_failures = task_record.review_failures()
    if qa_review_failures >= MAX_QA_REVIEW_FAILURES:
        await _block_exhausted_reviews(
            session, nats_client, item, sprint, reviewer_dict
        )
        return

    if not deliverable:
        await _block_missing_deliverable(
            session, nats_client, item, sprint, reviewer_dict
        )
        return

    # Evidence before opinion: a delivery the daemon refuted or never
    # confirmed goes back for rework without asking the QA model.
    gate = await asyncio.to_thread(record._delivery_gate, sprint.company_id, item.id)
    if gate is not None and gate[0] == delivery_gate.DELIVERY_QUEUED:
        # Files are written when the daemon connects; review then.
        return
    if gate is not None:
        await _send_back_from_delivery_gate(
            session,
            nats_client,
            item,
            sprint,
            task_record,
            gate,
            quality_gate_blocked_ids,
        )
        return

    await qa_review._qa_review(
        session,
        nats_client,
        item,
        sprint,
        reviewer_dict,
        task_record,
        deliverable,
        qa_review_failures,
        quality_gate_blocked_ids,
    )


def _reviewer_of(session: Session, sprint: Sprint) -> dict[str, Any]:
    """The company's QA agent as the reviewer, with the model it runs."""
    # Find QA agent for this company (fallback to any reviewer)
    reviewer = session.exec(
        select(Agent).where(
            Agent.company_id == sprint.company_id,
            Agent.role == "QA",
        )
    ).first()
    qa_model = getattr(reviewer, "model", None) or model_routing.company_model(
        session, sprint.company_id, "QA"
    )
    return {
        "role": reviewer.role if reviewer else "QA",
        "name": reviewer.name if reviewer else "QA",
        "model": model_routing.resolve_agent_model({"model": qa_model, "role": "QA"}),
        "seniority": reviewer.seniority if reviewer else "Senior",
        "system_prompt": reviewer.system_prompt if reviewer else None,
    }


async def _block_exhausted_reviews(
    session: Session,
    nats_client: NATSClient,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
) -> None:
    """A task QA failed to review ``MAX_QA_REVIEW_FAILURES`` times is blocked."""
    task_events.record(
        session,
        item,
        "blocked",
        "system",
        status="blocked",
        payload={"reason": "review retries exhausted"},
    )
    session.commit()

    await publish_glassroom(
        nats_client,
        sprint.id,
        {
            "type": "error",
            "semantic_type": "task.blocked",
            "source": "task_worker",
            "agent": reviewer_dict["name"],
            "role": reviewer_dict["role"],
            "task_id": item.id,
            "task_status": "blocked",
            "phase": "qa_review",
            "task": f"Reviewing: {item.title}",
            "message": f"❌ {reviewer_dict['name']} review failed {MAX_QA_REVIEW_FAILURES} times for: {item.title}. Marked as blocked.",
        },
    )

    logger.warning("Review retries exhausted for item=%s; marked blocked", item.id)


async def _block_missing_deliverable(
    session: Session,
    nats_client: NATSClient,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
) -> None:
    """A task in review with no deliverable on its board is blocked."""
    task_events.record(
        session,
        item,
        "blocked",
        "system",
        status="blocked",
        text="QA review aborted: no deliverable found in task comments.",
    )
    session.commit()

    await publish_glassroom(
        nats_client,
        sprint.id,
        {
            "type": "error",
            "semantic_type": "task.blocked",
            "source": "task_worker",
            "agent": reviewer_dict["name"],
            "role": reviewer_dict["role"],
            "task_id": item.id,
            "task_status": "blocked",
            "phase": "qa_review",
            "task": f"Reviewing: {item.title}",
            "message": f"❌ {reviewer_dict['name']} cannot review {item.title}: missing deliverable. Marked as blocked.",
        },
    )


async def _send_back_from_delivery_gate(
    session: Session,
    nats_client: NATSClient,
    item: BacklogItem,
    sprint: Sprint,
    task_record: Any,
    gate: tuple[str, str],
    quality_gate_blocked_ids: set[int],
) -> None:
    """Rework for a delivery the daemon refuted or never confirmed; block after the limit."""
    problem, observation = gate
    previous_gate_reworks = task_record.rework_requests_by(DELIVERY_GATE_AUTHOR)
    rework = previous_gate_reworks < qa_review.AUTOPILOT_MAX_REWORK_CYCLES
    instruction = (
        "Fix the code so the project's checks pass and deliver every "
        "changed file in full as a path= block."
        if problem in delivery_gate.CODE_PROBLEMS
        else "Re-run the task and give every file in full as a path= block."
    )
    gate_event = task_events.record(
        session,
        item,
        "reviewed" if rework else "blocked",
        "gate",
        author=DELIVERY_GATE_AUTHOR,
        status="todo" if rework else "blocked",
        text=(
            f"NEEDS WORK: {problem} — {observation}. {instruction}"
            if rework
            else f"{problem.capitalize()} after {previous_gate_reworks} "
            f"rework(s) — blocked for manual triage: {observation}"
        ),
    )
    gate_text = gate_event.payload["text"]
    gate_message = messages._message(
        sprint.company_id,
        sprint.id,
        item.id,
        DELIVERY_GATE_AUTHOR,
        DELIVERY_GATE_AUTHOR,
        (item.assignee or "Developer") if rework else SCRUM_MASTER_ROLE,
        "request" if rework else "escalation",
        gate_text,
    )
    session.add(gate_message)
    session.commit()
    if not rework:
        quality_gate_blocked_ids.add(item.id)
        await asyncio.to_thread(
            messages._triage_blocked_task,
            sprint.company_id,
            sprint.id,
            item.id,
            gate_text,
            gate_message.id,
        )
    await publish_glassroom(
        nats_client,
        sprint.id,
        {
            "type": "status" if rework else "error",
            "semantic_type": "task.rework_scheduled" if rework else "task.blocked",
            "source": "task_worker",
            "agent": DELIVERY_GATE_AUTHOR,
            "role": "System",
            "task_id": item.id,
            "task_status": item.status,
            "phase": "delivery_gate",
            "task": f"Reviewed: {item.title}",
            "message": f"🧱 {problem.capitalize()} for {item.title}: {observation}",
        },
    )


async def _retry_blocked(
    session: Session, nats_client: NATSClient, quality_gate_blocked_ids: set[int]
) -> set[int]:
    """Blocked tasks the worker may move again; the sprints with nothing left to move."""
    # --- Retry: blocked → todo (one retry only) ---
    blocked = session.exec(
        select(BacklogItem).where(BacklogItem.status == "blocked")
    ).all()
    # Sprints holding an item that stays blocked until a person acts may
    # have nothing left to move; they are checked for closing below.
    settled_sprint_ids: set[int] = set()
    histories = task_projection.histories_for(session, list(blocked))

    for item in blocked:
        item_sprint = session.get(Sprint, item.sprint_id)
        if item_sprint is not None and sprint_control.is_halted(item_sprint.status):
            continue
        if item.id in quality_gate_blocked_ids:
            settled_sprint_ids.add(item.sprint_id)
            continue

        next_step = task_projection.record_of(session, item, histories).next_step()
        if next_step is None:
            settled_sprint_ids.add(item.sprint_id)
            continue
        if next_step == "frozen":
            continue

        if next_step == "review":
            await _recover_to_review(session, nats_client, item)
            continue

        await _requeue_blocked(session, nats_client, item)

    session.commit()
    return settled_sprint_ids


async def _recover_to_review(
    session: Session, nats_client: NATSClient, item: BacklogItem
) -> None:
    """A blocked task with a deliverable and no explicit block reason goes back to QA."""
    task_events.record(
        session,
        item,
        "recovered",
        "system",
        status="in_review",
        text="Autonomous recovery detected a blocked task with an existing deliverable and no explicit block reason. Returning it to QA review.",
    )

    sprint = session.get(Sprint, item.sprint_id)
    if sprint:
        await publish_glassroom(
            nats_client,
            sprint.id,
            {
                "type": "status",
                "semantic_type": "task.recovered",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": item.id,
                "task_status": "in_review",
                "phase": "blocked_recovery",
                "task": item.title,
                "message": f"♻️ Recovered blocked task with existing deliverable: {item.title} moved back to QA review.",
            },
        )

    logger.info(
        "Recovered blocked item=%s back to in_review because a deliverable already exists",
        item.id,
    )


async def _requeue_blocked(
    session: Session, nats_client: NATSClient, item: BacklogItem
) -> None:
    """A blocked task the worker retries goes back to ``todo``."""
    task_events.record(
        session,
        item,
        "retried",
        "system",
        status="todo",
        text="Automatic retry — re-queuing blocked task.",
    )

    sprint = session.get(Sprint, item.sprint_id)
    if sprint:
        await publish_glassroom(
            nats_client,
            sprint.id,
            {
                "type": "status",
                "semantic_type": "task.retry_queued",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": item.id,
                "task_status": "todo",
                "phase": "retry",
                "task": item.title,
                "message": f"🔄 Retrying blocked task: {item.title}",
            },
        )

    logger.info("Retrying blocked item=%s", item.id)


def _closing_candidates(session: Session, settled_sprint_ids: set[int]) -> list[int]:
    """The active sprints among those named that have settled, worth closing."""
    # Only a sprint with nothing left to move is worth the retrospective.
    closing_candidates = [
        sprint_id
        for sprint_id in (
            session.exec(
                select(Sprint.id).where(
                    Sprint.id.in_(settled_sprint_ids), Sprint.status == "active"
                )
            ).all()
            if settled_sprint_ids
            else []
        )
        if dependencies.sprint_settled(session, sprint_id)
    ]
    session.commit()
    return closing_candidates

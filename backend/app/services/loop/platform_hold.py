"""A delivery the platform could not judge is held for a person, not reworked.

In the self-hosting owner action of 2026-11-24 (#701, run 2: company 3,
sprint 9) the company's test command looked in ``tests`` at the root and the
plan's tests were in ``backend/tests``. Every delivery's test run collected
nothing (pytest exit 4), the gate called it "tests failed" and sent each task
back to fix its code; the agents spent rework after rework, and 2.1 million
tokens, on a configuration no agent could change.

The gate now names that case ``delivery_gate.TESTS_NOT_FOUND`` (the Verifier's
``tests_run`` check, ``unverifiable`` with ``cause: no_tests``, read from the
daemon's exit status and output; ``evidence/test_collection.py``), one of
``delivery_gate.PLATFORM_PROBLEMS``. For such a problem ``hold``:

- blocks the task with the gate's observation and "blocked for manual
  triage", so it is held for a person: no ``NEEDS WORK:``, no rework counted,
  nothing the worker retries or reviews again, and no ScrumMaster triage (a
  model call cannot change the company's test command);
- pauses the sprint (``sprint_control.pause``) with ``REASON_PREFIX`` and the
  observation when every task of the sprint that delivered is now held for
  this same problem: the test command is the company's, so the next delivery
  would meet it too. A sprint where some delivery's tests did run goes on.
  The pause is audited once (``sprint_tests_not_found``), and ``publish`` says
  it on the sprint's Glassroom.

A person fixes the test command (the stack profile) or moves the tests, sends
the held tasks back to work and resumes the sprint. Mirrors ``daemon_gate``
(the owner's rule of 2026-11-24: a platform failure pauses instead of
spending rounds).
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Sprint, TaskEvent
from sqlmodel import Session, select

from services.evidence import delivery_gate
from services.loop import sprint_control
from services.platform.agent_service import AgentService
from services.platform.glassroom import publish_glassroom
from services.tasks import task_events
from services.tasks.board import DELIVERY_GATE_AUTHOR

ACTION = "sprint_tests_not_found"
PAYLOAD_KEY = "platform_failure"
REASON_PREFIX = "No delivery of this sprint could run its tests"


def reason(observation: str) -> str:
    """The sprint's pause reason, with what was observed and what to do."""
    return (
        f"{REASON_PREFIX}: {observation[:600]}. Fix the company's test command "
        "(Settings, stack profile) or where the tests are, send the held tasks "
        "back to work, then resume the sprint."
    )


def task_text(problem: str, observation: str) -> str:
    """The held task's comment: a block for a person, never a rework request."""
    return (
        f"{problem.capitalize()} — blocked for manual triage, no rework spent: "
        f"{observation}"
    )


def _held_for(events: list[TaskEvent], problem: str) -> bool:
    last = events[-1] if events else None
    return (
        last is not None
        and last.event_type == "blocked"
        and (last.payload or {}).get(PAYLOAD_KEY) == problem
    )


def every_delivery_held(session: Session, sprint_id: int, problem: str) -> bool:
    """Whether every task of the sprint that delivered is held for *problem*."""
    events = session.exec(
        select(TaskEvent).where(TaskEvent.sprint_id == sprint_id).order_by(TaskEvent.id)  # type: ignore[arg-type]
    ).all()
    by_task: dict[int, list[TaskEvent]] = {}
    for event in events:
        if event.task_id is not None:
            by_task.setdefault(event.task_id, []).append(event)
    delivered = [
        task_id
        for task_id, rows in by_task.items()
        if any(e.event_type == "delivered" for e in rows)
    ]
    return bool(delivered) and all(_held_for(by_task[t], problem) for t in delivered)


def hold(
    session: Session, item: BacklogItem, sprint: Sprint, problem: str, observation: str
) -> bool:
    """Hold *item* for a person; ``True`` when the sprint paused now."""
    task_events.record(
        session,
        item,
        "blocked",
        task_events.GATE,
        author=DELIVERY_GATE_AUTHOR,
        status="blocked",
        text=task_text(problem, observation),
        payload={PAYLOAD_KEY: problem},
    )
    session.flush()
    paused = False
    if every_delivery_held(session, sprint.id, problem):
        why = reason(observation)
        paused = sprint_control.pause(session, sprint, why)
        if paused:
            AgentService().audit_interaction(
                session=session,
                company_id=sprint.company_id,
                interaction_id=f"sprint:{sprint.id}:tests-not-found",
                agent_id="system:delivery-gate",
                action=ACTION,
                details={"company_id": str(sprint.company_id), "sprint_id": sprint.id},
                result={"reason": why, "item_id": item.id, "problem": problem},
                decision_signature=None,
                cost_tokens=0,
                commit=False,
            )
    session.commit()
    return paused


async def publish(
    nats_client: Any, sprint: Sprint, item: BacklogItem, problem: str, paused: bool
) -> None:
    """The hold, and the pause when there was one, on the sprint's Glassroom."""
    await publish_glassroom(
        nats_client,
        sprint.id,
        {
            "type": "error",
            "semantic_type": "task.blocked",
            "source": "task_worker",
            "agent": DELIVERY_GATE_AUTHOR,
            "role": "System",
            "task_id": item.id,
            "task_status": "blocked",
            "phase": "delivery_gate",
            "task": f"Reviewed: {item.title}",
            "message": f"🧱 {problem.capitalize()} for {item.title}: held for a person",
        },
    )
    if paused:
        await publish_glassroom(
            nats_client,
            sprint.id,
            {
                "type": "error",
                "semantic_type": "sprint.paused_tests_not_found",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_status": sprint_control.PAUSED,
                "message": f"⏸ {sprint.pause_reason}",
            },
        )


def is_platform_problem(problem: str) -> bool:
    return problem in delivery_gate.PLATFORM_PROBLEMS

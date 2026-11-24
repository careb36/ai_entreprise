"""A closed sprint's health, for its retrospective and the next sprint's plan.

``build`` counts a sprint's tasks (done, blocked, waiting for a person) and the
rework and QA failures on their boards; ``summary`` writes those numbers as the
text the ScrumMaster's retrospective message, the memory engram and the
procedure proposals carry. Best-effort: a failure returns no metrics.

The sprint board (issue #439) adds its numbers and the notes worth keeping:
the active decisions, interface and schema changes and gotchas nobody marked
wrong (``promotable_notes``). The summary lists them as claims, so the memory
engram keeps them and the ScrumMaster sees them when it proposes procedures.

The tool demand (``contracts.tool_demand``, issue #766) counts what the
sprint's agents asked of their tools and did not get: unknown tool names, final
answers that were a tool call written as text, refused calls and runs that
spent their rounds, all from the platform's own records (``demand_of``), and
the summary shows them beside the other numbers, as observations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from models import AgentRun, AuditLog, BacklogItem, Sprint, Verification
from models.agent_run import TASK_WORKER_PRODUCER
from sqlmodel import Session, select

from services.contracts import board_notes, tool_demand
from services.tasks import sprint_board, task_events, task_projection

logger = logging.getLogger("fabrique-task-worker")

# The notes a retrospective offers to keep: what later sprints must not
# contradict. Questions are left out; they are not knowledge.
PROMOTABLE_KINDS = ("decision", "interface_change", "schema_change", "gotcha")
MAX_PROMOTABLE = 5


def board_summary(session: Session, sprint_id: int, items: list[Any]) -> dict[str, Any]:
    """The sprint board's counts, the contradiction measure and the notes to
    keep; nothing when the board cannot be read (the rest still counts)."""
    try:
        notes = sprint_board.notes_of(session, sprint_id)
    except Exception as exc:  # noqa: BLE001 - The board is an addition, best-effort.
        logger.warning("Sprint board unreadable for sprint=%s: %s", sprint_id, exc)
        session.rollback()
        return {}
    promotable = [
        {
            "id": n.id,
            "kind": n.kind,
            "text": n.text,
            "author_role": n.author_role,
            "author_task_id": n.author_task_id,
        }
        for n in notes
        if n.status == board_notes.ACTIVE and n.kind in PROMOTABLE_KINDS
    ][:MAX_PROMOTABLE]
    return {
        "board_notes": sprint_board.counts(notes),
        "contradiction_rework": sprint_board.contradiction_rework(
            [(i.id, i.title, i.comments) for i in items], notes
        ),
        "promotable_notes": promotable,
    }


# The task events whose system text may say a final answer was a tool call
# written as text (``loop/worker/intake``: the retry, then the block).
DEMAND_EVENT_KINDS = ("retried", "blocked")


def demand_of(session: Session, company_id: int, items: list[Any]) -> dict[str, Any]:
    """The sprint's tool demand (``contracts.tool_demand``), from the records
    the platform kept: each task run's latest verification (its tool calls and
    loop record), the audit turns' tool calls (the QA review's reads, the
    Architect's brief) and the system's retry and block texts."""
    ids = [i.id for i in items if i.id is not None]
    refs = [f"task-{i}" for i in ids]
    run_ids = session.exec(
        select(AgentRun.id).where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref.in_(refs),  # type: ignore[attr-defined]
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
    ).all()
    latest: dict[int, Any] = {}  # The latest verification of each run.
    for row in session.exec(
        select(Verification)
        .where(Verification.agent_run_id.in_(list(run_ids)))  # type: ignore[attr-defined]
        .order_by(Verification.id)  # type: ignore[arg-type]
    ).all():
        latest[row.agent_run_id] = row.evidence or {}
    observations = [c for e in latest.values() for c in e.get("tool_calls") or []]
    for details in session.exec(
        select(AuditLog.details).where(
            AuditLog.company_id == company_id,
            AuditLog.interaction_id.in_(refs),  # type: ignore[union-attr]
        )
    ).all():
        observations += (details or {}).get("tool_calls") or []
    notes = [
        (e.payload or {}).get("text")
        for events in task_events.for_tasks(session, ids).values()
        for e in events
        if e.actor == task_events.SYSTEM and e.event_type in DEMAND_EVENT_KINDS
    ]
    return tool_demand.count(observations, list(latest.values()), notes)


def demand_summary(session: Session, sprint: Any, items: list[Any]) -> dict[str, Any]:
    """``tool_demand`` for the metrics; nothing when it cannot be read."""
    if sprint is None:
        return {}
    try:
        return {"tool_demand": demand_of(session, sprint.company_id, items)}
    except Exception as exc:  # noqa: BLE001 - An addition, best-effort like the board.
        logger.warning("Tool demand unreadable for sprint=%s: %s", sprint.id, exc)
        session.rollback()
        return {}


def build(engine: Any, sprint_id: int) -> dict[str, Any]:
    """Compute sprint health metrics for retrospective engram and next-sprint planning."""
    try:
        with Session(engine) as session:
            items = session.exec(
                select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
            ).all()
            if not items:
                return {}

            sprint = session.get(Sprint, sprint_id)
            # The tasks' events in one query; each answer reads them when they
            # cover the thread (services/tasks/task_projection.py).
            records = task_projection.records_for(session, list(items))
            total = len(items)
            done = sum(1 for i in items if i.status == "done")
            # An approved high-risk delivery waiting for a person is not a
            # failure: counted apart from the blocked tasks.
            awaiting_person = sum(
                1
                for i in items
                if i.status == "blocked" and records[i.id].awaits_risk_acceptance()
            )
            blocked = sum(1 for i in items if i.status == "blocked") - awaiting_person
            rework_count = 0
            qa_fail_count = 0
            blocked_reasons: list[str] = []

            for item in items:
                comments = item.comments or []
                if isinstance(comments, str):
                    try:
                        comments = json.loads(comments)
                    except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                        comments = []
                for comment in comments:
                    text = str(comment.get("text", ""))
                    if "NEEDS WORK:" in text.upper():
                        rework_count += 1
                    if (
                        "qa quality gate failed" in text.lower()
                        or "qa review failed" in text.lower()
                    ):
                        qa_fail_count += 1
                record = records[item.id]
                if item.status == "blocked" and not record.awaits_risk_acceptance():
                    reason = record.block_reason() or record.deliverable()
                    if reason:
                        blocked_reasons.append(f"{item.title[:60]}: {reason[:120]}")

            return {
                "company_id": sprint.company_id if sprint else None,
                "sprint_id": sprint_id,
                "total_tasks": total,
                "done_tasks": done,
                "blocked_tasks": blocked,
                "awaiting_person_tasks": awaiting_person,
                "completion_rate": round(done / total * 100) if total else 0,
                "rework_cycles": rework_count,
                "qa_failures": qa_fail_count,
                "blocked_reasons": blocked_reasons[:5],
                **board_summary(session, sprint_id, list(items)),
                **demand_summary(session, sprint, list(items)),
            }
    except Exception as exc:  # noqa: BLE001 - Retrospective context is best-effort.
        logger.warning(
            "Sprint retrospective build failed for sprint=%s: %s", sprint_id, exc
        )
        return {}


def summary(metrics: dict[str, Any]) -> str:
    lines = [
        f"Sprint {metrics.get('sprint_id')} retrospective:",
        f"- Completed {metrics.get('done_tasks', 0)}/{metrics.get('total_tasks', 0)} tasks ({metrics.get('completion_rate', 0)}%)",
        f"- Blocked: {metrics.get('blocked_tasks', 0)} tasks",
        f"- QA rework cycles: {metrics.get('rework_cycles', 0)}",
        f"- QA failures: {metrics.get('qa_failures', 0)}",
    ]
    for reason in metrics.get("blocked_reasons") or []:
        lines.append(f"  • {reason}")
    if metrics.get("tool_demand") is not None:  # Observations, beside the counts.
        lines.append(tool_demand.summary_line(metrics["tool_demand"]))
    notes = (metrics.get("board_notes") or {}).get("total", 0)
    if notes:
        lines.append(
            f"- Board notes: {notes} ({metrics['board_notes'].get('marked_wrong', 0)} "
            "marked wrong); rework for a contradiction with another task: "
            f"{metrics.get('contradiction_rework', 0)}"
        )
    promotable = metrics.get("promotable_notes") or []
    if promotable:
        lines.append(
            "- Board notes worth keeping (agents' claims, not verified; "
            "candidates for memory or a procedure):"
        )
        for note in promotable:
            lines.append(
                f"  • #{note['id']} {note['kind']} ({note['author_role']}): "
                f"{str(note['text'])[:200]}"
            )
    return "\n".join(lines)

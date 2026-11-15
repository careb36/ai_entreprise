"""A task's observed effort, read from its own records at sprint close (issue #534).

The difficulty estimate (``services/models/difficulty.py``, an ``estimated``
event) is a prediction made before a task runs. This module is the other
side: what the task actually took, read only from what the backend itself
recorded, never from an agent's account of its work (POSITIONING: a claim and
an observation never share a field; an agent's report of its own effort is a
claim).

The observation of one task:

- ``tokens``: the audit log's KPI turns for the task (``interaction_id``
  ``task-<id>``) whose usage the LLM proxy reported (``tokens: reported``).
  When any turn's count was estimated or unavailable, or the task had no
  turn, the total is not known: ``tokens`` is ``None`` with
  ``tokens_unknown_reason`` (``no_turns``, ``unreported_turns``) and the
  reported part kept apart in ``tokens_reported``; never 0;
- ``wall_seconds``: from the task's first ``claimed`` event to its last event
  that left it ``done``; ``None`` when it never got there;
- ``deliveries`` and ``rework_cycles``: the ``delivered`` events, and each
  delivery after the first (every one a return to work after a delivery);
- ``rework_requests``: the ``reviewed`` events with a ``needs_work``
  verdict (QA's, a gate's or a person's); ``send_backs``: a person's
  ``sent_back`` events; ``review_failures``: the ``review_failed`` events;
- ``files_written``: the distinct paths the workspace daemon reported it
  wrote (``workspace_writes`` with ``status: written`` on each run's latest
  verification carrying one); ``None`` when no run of the task has such a
  report.

Beside it, the run's attribution, also an observation: the role and producer
of the task's last ``AgentRun`` (else the task's assignee) and the model the
proxy reported serving it.

Why a task event rather than a table: the observation belongs to the task's
record, and the task's events are an append-only chain, hashed and verified
(``services/tasks/task_events.py``), so an observation is never overwritten.
``record_sprint`` writes one ``effort_observed`` event per task at close; a
second close (after a person reopened the sprint) writes a new event only
when the observation changed, and readers take the latest. No migration is
needed: ``task_events.event_type`` is free text checked in Python (``KINDS``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.tasks import task_events

VERSION = 1
KIND = "effort_observed"
TOKENS_REPORTED = "reported"
NO_TURNS = "no_turns"
UNREPORTED_TURNS = "unreported_turns"
_TASK_REF = "task-"


def _get(event: Any, name: str) -> Any:
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


def _kind(event: Any) -> str:
    return str(_get(event, "event_type") or _get(event, "kind") or "")


def _payload(event: Any) -> dict[str, Any]:
    payload = _get(event, "payload")
    return payload if isinstance(payload, dict) else {}


def _when(event: Any) -> datetime | None:
    value = _get(event, "created_at")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return None


def tokens_of(turns: list[tuple[str | None, int | None]]) -> dict[str, Any]:
    """``(usage status, cost)`` per audit turn → the task's tokens, or None
    with the reason when any turn's count was not reported."""
    reported = [
        max(0, int(cost or 0)) for status, cost in turns if status == TOKENS_REPORTED
    ]
    unreported = len(turns) - len(reported)
    reason = None
    if not turns:
        reason = NO_TURNS
    elif unreported:
        reason = UNREPORTED_TURNS
    return {
        "tokens": None if reason else sum(reported),
        "tokens_reported": sum(reported),
        "turns_reported": len(reported),
        "turns_unreported": unreported,
        "tokens_unknown_reason": reason,
    }


def wall_seconds(events: list[Any]) -> int | None:
    """Seconds from the first ``claimed`` event to the last event that left
    the task ``done`` after it; None without both."""
    claimed = next(
        (_when(e) for e in events if _kind(e) == "claimed" and _when(e)), None
    )
    if claimed is None:
        return None
    done = [
        _when(e)
        for e in events
        if _get(e, "task_status") == "done" and _when(e) and _when(e) >= claimed
    ]
    if not done:
        return None
    return int((max(done) - claimed).total_seconds())


def _is_person(event: Any) -> bool:
    return str(_get(event, "actor") or "") == task_events.PERSON


def observe(
    *,
    status: str | None,
    events: list[Any],
    turns: list[tuple[str | None, int | None]],
    files: list[str] | None,
) -> dict[str, Any]:
    """One task's observed effort from its records: its status at close, its
    events (rows or dicts, in order), its audit turns and the paths the daemon
    reported writing (None: no daemon report at all). Pure."""
    deliveries = sum(1 for e in events if _kind(e) == "delivered")
    return {
        "status": status,
        "done": status == "done",
        **tokens_of(turns),
        "wall_seconds": wall_seconds(events),
        "deliveries": deliveries,
        "rework_cycles": max(0, deliveries - 1),
        "rework_requests": sum(
            1
            for e in events
            if _kind(e) == "reviewed" and _payload(e).get("verdict") == "needs_work"
        ),
        "send_backs": sum(
            1 for e in events if _kind(e) == "sent_back" and _is_person(e)
        ),
        "review_failures": sum(1 for e in events if _kind(e) == "review_failed"),
        "files_written": None if files is None else len(set(files)),
    }


def _task_id(ref: str | None) -> int | None:
    prefix, _, number = (ref or "").partition("-")
    return int(number) if prefix == "task" and number.isdigit() else None


def collect(
    session: Any, company_id: int, items: list[Any]
) -> dict[int, dict[str, Any]]:
    """Per task of *items* (``BacklogItem`` rows of *company_id*): ``{observed,
    attribution, sources}``, read from the database. Another company's runs,
    turns and events never count."""
    from models import AuditLog, TaskEvent
    from models.agent_run import AgentRun, Verification
    from sqlmodel import select

    ids = [item.id for item in items if item.id is not None]
    if not ids:
        return {}
    events: dict[int, list[Any]] = {i: [] for i in ids}
    for event in session.exec(
        select(TaskEvent)
        .where(TaskEvent.company_id == company_id, TaskEvent.task_id.in_(ids))
        .order_by(TaskEvent.id)
    ).all():
        if event.event_type != KIND:
            events[event.task_id].append(event)
    runs: dict[int, list[Any]] = {i: [] for i in ids}
    for run in session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref.in_([f"{_TASK_REF}{i}" for i in ids]),
        )
        .order_by(AgentRun.id)
    ).all():
        runs[_task_id(run.external_ref)].append(run)
    run_ids = [run.id for group in runs.values() for run in group]
    reports: dict[int, Any] = {}
    if run_ids:
        for row in session.exec(
            select(Verification)
            .where(
                Verification.company_id == company_id,
                Verification.agent_run_id.in_(run_ids),
            )
            .order_by(Verification.id.desc())
        ).all():
            if isinstance((row.evidence or {}).get("workspace_writes"), list):
                reports.setdefault(row.agent_run_id, row)
    turns: dict[int, list[tuple[str | None, int | None]]] = {i: [] for i in ids}
    for row in session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id,
            AuditLog.interaction_id.in_([f"{_TASK_REF}{i}" for i in ids]),
        )
    ).all():
        details = row.details if isinstance(row.details, dict) else {}
        task_id = _task_id(row.interaction_id)
        if task_id in turns and details.get("tokens") is not None:
            turns[task_id].append((details.get("tokens"), row.cost_tokens))
    out: dict[int, dict[str, Any]] = {}
    for item in items:
        task_runs = runs.get(item.id, [])
        seen = [reports[r.id] for r in task_runs if r.id in reports]
        files = (
            None
            if not seen
            else [
                w["path"]
                for report in seen
                for w in report.evidence["workspace_writes"]
                if isinstance(w, dict)
                and w.get("status") == "written"
                and isinstance(w.get("path"), str)
            ]
        )
        last = task_runs[-1] if task_runs else None
        task_events_ = events.get(item.id, [])
        out[item.id] = {
            "observed": observe(
                status=item.status,
                events=task_events_,
                turns=turns.get(item.id, []),
                files=files,
            ),
            "attribution": {
                "role": (last.agent_role if last else None) or item.assignee or None,
                "producer": last.producer if last else None,
                "model": (last.model if last else None) or None,
            },
            "sources": {
                "runs": [r.id for r in task_runs],
                "verifications": sorted(r.id for r in seen),
                "turns": len(turns.get(item.id, [])),
                "events_through": task_events_[-1].id if task_events_ else None,
            },
        }
    return out


def latest_stored(
    session: Any, company_id: int, ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Per task, the payload of its latest ``effort_observed`` event."""
    from models import TaskEvent
    from sqlmodel import select

    out: dict[int, dict[str, Any]] = {}
    if not ids:
        return out
    for event in session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.company_id == company_id,
            TaskEvent.task_id.in_(ids),
            TaskEvent.event_type == KIND,
        )
        .order_by(TaskEvent.id)
    ).all():
        out[event.task_id] = _payload(event)
    return out


def record_sprint(session: Any, sprint_id: int) -> int:
    """Write each task's ``effort_observed`` event for the closed *sprint_id*;
    a task whose latest stored observation is unchanged gets none. Returns how
    many were written; the caller commits."""
    from models import BacklogItem, Sprint
    from sqlmodel import select

    sprint = session.get(Sprint, sprint_id)
    if sprint is None or sprint.status != "completed":
        return 0
    items = list(
        session.exec(
            select(BacklogItem)
            .where(BacklogItem.sprint_id == sprint_id)
            .order_by(BacklogItem.id)
        ).all()
    )
    found = collect(session, sprint.company_id, items)
    stored = latest_stored(session, sprint.company_id, list(found))
    written = 0
    for item in items:
        fact = found.get(item.id)
        if fact is None:
            continue
        previous = stored.get(item.id) or {}
        if (
            previous.get("observed") == fact["observed"]
            and previous.get("attribution") == fact["attribution"]
        ):
            continue
        task_events.record(
            session,
            item,
            KIND,
            task_events.SYSTEM,
            payload={
                "version": VERSION,
                "sprint_id": sprint_id,
                "observed_by": "backend",
                **fact,
            },
            company_id=sprint.company_id,
        )
        written += 1
    return written

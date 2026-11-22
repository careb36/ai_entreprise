"""A company's routines: a task that runs on a schedule (issue #774).

A Manager writes a routine (a title, a description, the hired role that does
it, and a daily or weekly ``routine_schedule``). When ``next_run_at`` comes,
``fire_due`` opens a small sprint, ``Routine: <title> — <date>``, with the
routine's one task, and queues it like a sprint a person started: the run
window, the sprint budget, the Verifier and QA apply as to any other work,
because the routine goes through the same worker and the same gates.

- **Previous run still open.** When the sprint the routine opened last time is
  still open (``sprint_control.OPEN_STATUSES``: running, paused, waiting), the
  occurrence is skipped and audited as ``routine_skipped``; two runs of one
  routine never overlap (Paperclip's ``skip_if_active``).
- **Backend down.** The occurrences missed while it was down collapse into one
  run, then the schedule resumes (``routine_schedule.next_after``).
- **Several replicas.** Each claims an occurrence with a conditional update of
  ``next_run_at``; only the replica whose update matched runs it.

- **Revisions.** Each change that alters a routine is a numbered revision
  (``routine_revisions``); a run records the revision it ran, and a Manager
  can restore an earlier one.
- **Variables.** A routine declares inputs (``routine_variables``) and writes
  ``{{name}}`` in its description. A scheduled run takes the defaults; "run
  now" may give values. A run whose required value is missing is skipped,
  audited with the reason. The values a run used are in its audit row.

Creating, changing, deleting, running and skipping a routine are each an
audit row (``routine_*``) in the company's chain. The design follows
Paperclip's routines (MIT: ``server/src/services/routines.ts``); no code taken.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models import BacklogItem, BusinessUnit, OutboxEvent, Routine, Sprint
from sqlalchemy import update
from sqlmodel import Session, select

from services.contracts import routine_schedule, routine_variables
from services.loop import routine_revisions, run_window_gate, sprint_control
from services.planning import sprint_team
from services.platform.agent_service import AgentService

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
TITLE_MAX = 200
DESCRIPTION_MAX = 10_000
FIELDS = routine_revisions.SNAPSHOT_FIELDS


class RoutineRefused(ValueError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def view(routine: Routine) -> dict[str, Any]:
    return {
        "id": routine.id,
        "company_id": routine.company_id,
        "title": routine.title,
        "description": routine.description,
        "assignee": routine.assignee,
        "schedule": routine.schedule,
        "enabled": routine.enabled,
        "variables": routine.variables,
        "revision": routine.revision,
        "next_run_at": routine.next_run_at.isoformat() if routine.next_run_at else None,
        "last_run_at": routine.last_run_at.isoformat() if routine.last_run_at else None,
        "last_sprint_id": routine.last_sprint_id,
    }


def _audit(
    session: Session,
    routine: Routine,
    action: str,
    result: dict[str, Any],
    actor: dict[str, Any] | None = None,
) -> None:
    actor = actor or {}
    AgentService().audit_interaction(
        session=session,
        company_id=routine.company_id,
        interaction_id=f"routine:{routine.id}",
        agent_id=str(actor.get("by") or "system:routines"),
        action=action,
        details={
            "company_id": str(routine.company_id),
            "routine_id": routine.id,
            "user_id": actor.get("user_id"),
            "token_id": actor.get("token_id"),
        },
        result=result,
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )


def _clean(
    session: Session, company_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "title" in payload:
        title = str(payload["title"] or "").strip()
        if not title or len(title) > TITLE_MAX:
            raise RoutineRefused(422, f"title must be 1 to {TITLE_MAX} characters")
        out["title"] = title
    if "description" in payload:
        description = str(payload["description"] or "")
        if len(description) > DESCRIPTION_MAX:
            raise RoutineRefused(
                422, f"description is over {DESCRIPTION_MAX} characters"
            )
        out["description"] = description
    if "assignee" in payload:
        hired = sprint_team.hired_roles(session, company_id)
        try:
            team = sprint_team.check_team([str(payload["assignee"] or "")], hired)
        except sprint_team.TeamRefused as exc:
            raise RoutineRefused(422, exc.detail) from exc
        out["assignee"] = team[0]
    if "schedule" in payload:
        try:
            out["schedule"] = routine_schedule.validate(payload["schedule"])
        except routine_schedule.InvalidSchedule as exc:
            raise RoutineRefused(422, str(exc)) from exc
    if "enabled" in payload:
        out["enabled"] = bool(payload["enabled"])
    if "variables" in payload:
        try:
            out["variables"] = routine_variables.validate(payload["variables"])
        except routine_variables.InvalidVariables as exc:
            raise RoutineRefused(422, str(exc)) from exc
    return out


def _check_placeholders(description: str, variables: list[dict[str, Any]]) -> None:
    try:
        routine_variables.check_placeholders(description, variables)
    except routine_variables.InvalidVariables as exc:
        raise RoutineRefused(422, str(exc)) from exc


def _reschedule(routine: Routine, now: datetime | None = None) -> None:
    routine.next_run_at = (
        _naive(routine_schedule.next_after(routine.schedule, now or _now()))
        if routine.enabled
        else None
    )


def create(
    session: Session,
    company_id: int,
    payload: dict[str, Any],
    actor: dict[str, Any] | None,
    now: datetime | None = None,
) -> Routine:
    missing = [k for k in ("title", "assignee", "schedule") if not payload.get(k)]
    if missing:
        raise RoutineRefused(422, f"missing: {', '.join(missing)}")
    values = _clean(session, company_id, payload)
    _check_placeholders(values.get("description", ""), values.get("variables", []))
    routine = Routine(
        company_id=company_id,
        created_by_user_id=(actor or {}).get("user_id"),
        **values,
    )
    _reschedule(routine, now)
    session.add(routine)
    session.flush()
    routine_revisions.write(session, routine, "Created", actor)
    _audit(session, routine, "routine_created", {"routine": view(routine)}, actor)
    session.commit()
    session.refresh(routine)
    return routine


def update_routine(
    session: Session,
    routine: Routine,
    payload: dict[str, Any],
    actor: dict[str, Any] | None,
    now: datetime | None = None,
    *,
    summary: str | None = None,
    restored_from: int | None = None,
) -> Routine:
    """Change *routine*; a change that alters it is the next revision."""
    values = _clean(session, routine.company_id, payload)
    before = {k: getattr(routine, k) for k in FIELDS}
    changed = sorted(k for k, v in values.items() if before[k] != v)
    if not changed:
        return routine
    _check_placeholders(
        values.get("description", routine.description),
        values.get("variables", routine.variables),
    )
    for key in changed:
        setattr(routine, key, values[key])
    _reschedule(routine, now)
    routine.revision += 1
    session.add(routine)
    routine_revisions.write(
        session,
        routine,
        summary or f"Changed {', '.join(changed)}",
        actor,
        restored_from,
    )
    _audit(
        session,
        routine,
        "routine_changed",
        {
            "changed": changed,
            "from": {k: before[k] for k in changed},
            "to": {k: values[k] for k in changed},
            "revision": routine.revision,
            "restored_from": restored_from,
        },
        actor,
    )
    session.commit()
    session.refresh(routine)
    return routine


def delete(session: Session, routine: Routine, actor: dict[str, Any] | None) -> None:
    _audit(session, routine, "routine_deleted", {"routine": view(routine)}, actor)
    session.delete(routine)
    session.commit()


def _open_run(session: Session, routine: Routine) -> Sprint | None:
    if routine.last_sprint_id is None:
        return None
    sprint = session.get(Sprint, routine.last_sprint_id)
    if sprint is None or sprint.status not in sprint_control.OPEN_STATUSES:
        return None
    return sprint


def restore(
    session: Session, routine: Routine, number: int, actor: dict[str, Any] | None
) -> Routine:
    """Put back revision *number* of *routine*, as its next revision."""
    revision = routine_revisions.find(session, routine, number)
    if revision is None:
        raise RoutineRefused(404, f"Routine {routine.id} has no revision {number}")
    payload = {k: revision.snapshot.get(k) for k in FIELDS if k in revision.snapshot}
    current = routine.revision
    updated = update_routine(
        session,
        routine,
        payload,
        actor,
        summary=f"Restored revision {number}",
        restored_from=number,
    )
    if updated.revision == current:
        raise RoutineRefused(409, f"The routine already holds revision {number}")
    return updated


def _open_sprint(
    session: Session, routine: Routine, now: datetime, description: str
) -> Sprint:
    business_unit = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == routine.company_id)
    ).first()
    sprint = Sprint(
        company_id=routine.company_id,
        business_unit_id=business_unit.id if business_unit else None,
        sprint_name=f"Routine: {routine.title} — {now.date().isoformat()}"[:255],
        scope_size="S",
        goal=routine.title,
        objective=description or routine.title,
        team=None,
        team_source=sprint_team.ALL,
    )
    session.add(sprint)
    session.flush()
    held = run_window_gate.start_new(session, sprint, False, None)
    item = BacklogItem(
        sprint_id=sprint.id,
        title=routine.title,
        description=description,
        priority="medium",
        assignee=routine.assignee,
    )
    session.add(item)
    session.flush()
    if held:
        run_window_gate.defer_task(session, item, sprint, held)
    else:
        session.add(
            OutboxEvent(
                aggregate_type="backlog_item",
                aggregate_id=str(item.id),
                event_type="nats_publish",
                payload={
                    "subject": "task.assign",
                    "message": {
                        "backlog_item_id": item.id,
                        "interaction_id": str(uuid4()),
                        "agent": item.assignee,
                        "sprint_id": sprint.id,
                    },
                },
            )
        )
    return sprint


def run(
    session: Session,
    routine: Routine,
    actor: dict[str, Any] | None = None,
    now: datetime | None = None,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One occurrence of *routine*: its sprint, or why it was skipped. Commits.

    *values* are the variables given for this run (``None``: the defaults);
    values a person gave that the routine refuses raise ``RoutineRefused``,
    while a scheduled run that cannot fill a required variable is skipped."""
    now = now or _now()
    try:
        resolved = routine_variables.resolve(routine.variables, values)
    except routine_variables.InvalidVariables as exc:
        if values is not None:
            raise RoutineRefused(422, str(exc)) from exc
        reason = f"Cannot run revision {routine.revision}: {exc}"
        _audit(session, routine, "routine_skipped", {"reason": reason}, actor)
        session.commit()
        return {"skipped": reason, "revision": routine.revision}
    open_run = _open_run(session, routine)
    if open_run is not None:
        reason = (
            f"The previous run, sprint #{open_run.id}, is still {open_run.status}; "
            "this occurrence is skipped."
        )
        _audit(
            session,
            routine,
            "routine_skipped",
            {"open_sprint_id": open_run.id, "reason": reason},
            actor,
        )
        session.commit()
        return {"skipped": reason, "open_sprint_id": open_run.id}
    description = routine_variables.render(routine.description, resolved)
    sprint = _open_sprint(session, routine, now, description)
    routine.last_run_at = now
    routine.last_sprint_id = sprint.id
    session.add(routine)
    _audit(
        session,
        routine,
        "routine_ran",
        {"sprint_id": sprint.id, "revision": routine.revision, "values": resolved},
        actor,
    )
    session.commit()
    return {"sprint_id": sprint.id, "revision": routine.revision, "values": resolved}


def _claim(session: Session, routine: Routine, now: datetime) -> bool:
    """Move *routine*'s ``next_run_at`` past *now*; ``True`` when this replica did."""
    following = _naive(routine_schedule.next_after(routine.schedule, now))
    claimed = session.exec(
        update(Routine)
        .where(Routine.id == routine.id, Routine.next_run_at == routine.next_run_at)
        .values(next_run_at=following)
    )
    session.commit()
    return claimed.rowcount == 1


def fire_due(engine: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Run every enabled routine whose time came; what each did."""
    now = now or _now()
    out = []
    with Session(engine) as session:
        due = session.exec(
            select(Routine).where(
                Routine.enabled.is_(True),  # type: ignore[attr-defined]
                Routine.next_run_at.is_not(None),  # type: ignore[union-attr]
                Routine.next_run_at <= now,  # type: ignore[operator]
            )
        ).all()
        for routine in due:
            if not _claim(session, routine, now):
                continue
            session.refresh(routine)
            out.append({"routine_id": routine.id, **run(session, routine, now=now)})
    return out


async def worker(engine: Any) -> None:
    """Fire due routines every ``TICK_SECONDS``, for the life of the backend."""
    while True:
        try:
            for fired in await asyncio.to_thread(fire_due, engine):
                logger.info("Routine fired: %s", fired)
        except Exception:
            logger.exception("Routine scheduler tick failed")
        await asyncio.sleep(TICK_SECONDS)

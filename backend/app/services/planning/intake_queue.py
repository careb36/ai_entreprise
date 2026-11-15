"""The intake queue: requests in, a person's decision out, with provenance (issue #532).

``receive`` records a request with the source the backend observed (the
route it arrived by, never a field the sender filled). A Manager then moves
it (``accept``, ``decline``, ``mark_duplicate``, ``snooze``, ``reopen``); the
system moves a snoozed item back to ``pending`` once its date passes
(``wake_due``, run by the intake janitor loop). Every move is one audit row
naming its actor, under ``intake:<company>:<item>``, so the item's history is
read back from the company's hash chain (``intake_records.history``).

Accepting creates what the request became and keeps the link both ways:
a task in a sprint the person names (its thread opens with a note naming the
request), or a revision of an approved specification (the request is added
to the draft as an open question, so it cannot be approved until a person
answers it). The Support agent's suggestion (``suggested_*``,
``services/management/intake_triage.py``) is never read here: a decision is
the person's alone (``docs/POSITIONING.md``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from logging_config import logger
from models import BacklogItem, IntakeItem, OutboxEvent, Specification, Sprint
from sqlmodel import Session, select

from services.contracts import intake_rules as intake
from services.contracts import roles as catalogue
from services.planning import sprint_team
from services.planning.intake_records import interaction_of
from services.platform import locks
from services.platform.agent_service import AgentService
from services.tasks import task_events

SYSTEM_ACTOR = "system:intake-janitor"
WAKE_INTERVAL_SECONDS = 300
QUESTION_PREFIX = "Intake request #"
PRIORITIES = ("low", "medium", "high", "critical")
# A sprint a new task may join: not closed, not cancelled.
OPEN_SPRINT_STATUSES = ("active", "planned", "paused", "needs_split")


class IntakeRefused(Exception):
    """A move the queue refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _audit(
    session: Session,
    item: IntakeItem,
    actor: str,
    action: str,
    previous: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    """One audit row for a move of *item*; the caller commits."""
    AgentService().audit_interaction(
        session=session,
        company_id=item.company_id,
        interaction_id=interaction_of(item),
        agent_id=actor,
        action=action,
        details={
            "company_id": item.company_id,
            "intake_item_id": item.id,
            "from": previous,
            "to": item.status,
            **(extra or {}),
        },
        result={"status": item.status},
        decision_signature=None,
        cost_tokens=0,
    )


def _category(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    name = value.strip().lower()
    if name not in intake.CATEGORIES:
        raise IntakeRefused(
            422, f"Unknown category {value!r}; use {', '.join(intake.CATEGORIES)}"
        )
    return name


def receive(
    session: Session,
    company_id: int,
    source: str,
    title: str,
    body: str = "",
    *,
    submitted_by: str | None = None,
    source_ref: str | None = None,
) -> IntakeItem:
    """Record a request, ``pending``, and audit its arrival; the caller commits."""
    if source not in intake.SOURCES:
        raise ValueError(f"unknown intake source {source!r}")
    title = (title or "").strip()[: intake.TITLE_LIMIT]
    if not title:
        raise IntakeRefused(422, "A request needs a title")
    item = IntakeItem(
        company_id=company_id,
        source=source,
        source_ref=(source_ref or None) and source_ref[: intake.SOURCE_REF_LIMIT],
        submitted_by=submitted_by,
        title=title,
        body=(body or "")[: intake.BODY_LIMIT],
    )
    session.add(item)
    session.flush()
    _audit(
        session,
        item,
        submitted_by or source,
        "intake_received",
        None,
        {"source": source, "source_ref": item.source_ref},
    )
    return item


def load_for_decision(session: Session, item_id: int) -> IntakeItem:
    """The item, its row locked until the commit: two Managers cannot decide
    it at once (the second reads the first's status and is refused)."""
    item = session.exec(
        select(IntakeItem).where(IntakeItem.id == item_id).with_for_update()
    ).first()
    if item is None:
        raise IntakeRefused(404, "Intake item not found")
    return item


def _move(
    session: Session,
    item: IntakeItem,
    target: str,
    actor: str,
    *,
    category: str | None,
    reason: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    if not intake.can_move(item.status, target):
        raise IntakeRefused(
            409, f"The request is {item.status}; it cannot become {target}"
        )
    decided_category = _category(category)  # refused before anything changes
    previous = item.status
    now = _now()
    item.status = target
    item.updated_at = now
    if target != intake.SNOOZED:
        item.snoozed_until = None
    if target != intake.PENDING:
        item.decided_status = target
        item.decided_category = decided_category or item.decided_category
        item.decision_reason = (reason or "").strip()[: intake.REASON_LIMIT] or None
        item.decided_by = actor
        item.decided_at = now
    session.add(item)
    _audit(
        session,
        item,
        f"human:{actor}",
        f"intake_{target}",
        previous,
        {
            "category": item.decided_category,
            "reason": item.decision_reason,
            **(extra or {}),
        },
    )


def decline(
    session: Session, item: IntakeItem, actor: str, reason: str, category: str | None
) -> None:
    if not (reason or "").strip():
        raise IntakeRefused(422, "Declining needs a reason")
    _move(session, item, intake.DECLINED, actor, category=category, reason=reason)


def snooze(
    session: Session,
    item: IntakeItem,
    actor: str,
    until: datetime,
    category: str | None,
    reason: str | None,
) -> None:
    until = until.astimezone(UTC).replace(tzinfo=None) if until.tzinfo else until
    if until <= _now():
        raise IntakeRefused(422, "Snooze until a date in the future")
    _move(
        session,
        item,
        intake.SNOOZED,
        actor,
        category=category,
        reason=reason,
        extra={"snoozed_until": until.isoformat()},
    )
    item.snoozed_until = until


def reopen(session: Session, item: IntakeItem, actor: str) -> None:
    _move(session, item, intake.PENDING, actor, category=None, reason=None)


def _company_task(session: Session, company_id: int, task_id: int) -> BacklogItem:
    task = session.get(BacklogItem, task_id)
    sprint = session.get(Sprint, task.sprint_id) if task else None
    if task is None or sprint is None or sprint.company_id != company_id:
        raise IntakeRefused(404, f"Task {task_id} not found in this company")
    return task


def mark_duplicate(
    session: Session,
    item: IntakeItem,
    actor: str,
    *,
    of_item_id: int | None,
    of_task_id: int | None,
    category: str | None,
    reason: str | None,
) -> None:
    if (of_item_id is None) == (of_task_id is None):
        raise IntakeRefused(422, "Name one intake item or one task it duplicates")
    if of_item_id is not None:
        other = session.get(IntakeItem, of_item_id)
        if other is None or other.company_id != item.company_id:
            raise IntakeRefused(404, f"Intake item {of_item_id} not found")
        if other.id == item.id:
            raise IntakeRefused(422, "A request cannot duplicate itself")
        if other.duplicate_of_item_id == item.id:
            raise IntakeRefused(422, f"Intake item {other.id} duplicates this one")
    else:
        _company_task(session, item.company_id, of_task_id)
    _move(
        session,
        item,
        intake.DUPLICATE,
        actor,
        category=category,
        reason=reason,
        extra={
            "duplicate_of": intake.link("intake", of_item_id)
            or intake.link("task", of_task_id)
        },
    )
    item.duplicate_of_item_id = of_item_id
    item.duplicate_of_task_id = of_task_id


def _new_task(
    session: Session,
    item: IntakeItem,
    actor: str,
    sprint_id: int | None,
    assignee: str | None,
    priority: str | None,
) -> BacklogItem:
    if sprint_id is None:
        raise IntakeRefused(422, "Accepting as a task needs the sprint it joins")
    sprint = session.get(Sprint, sprint_id)
    if sprint is None or sprint.company_id != item.company_id:
        raise IntakeRefused(404, f"Sprint {sprint_id} not found in this company")
    if sprint.archived_at is not None or sprint.status not in OPEN_SPRINT_STATUSES:
        raise IntakeRefused(
            409, f"Sprint {sprint_id} is {sprint.status}; pick an open sprint"
        )
    role = catalogue.canonical(assignee or "PO")
    if role not in catalogue.ROLES:
        raise IntakeRefused(422, f"Unknown role {role!r}")
    if not sprint_team.on_team(sprint, role):
        raise IntakeRefused(409, f"{role} is not on sprint {sprint_id}'s team")
    if priority is not None and priority not in PRIORITIES:
        raise IntakeRefused(422, f"Unknown priority; use {', '.join(PRIORITIES)}")
    task = BacklogItem(
        sprint_id=sprint.id,
        title=item.title,
        description=item.body,
        priority=priority or "medium",
        assignee=role,
        status="todo",
    )
    session.add(task)
    session.flush()
    task_events.record(
        session,
        task,
        "note",
        task_events.SYSTEM,
        text=(
            f"Created from intake request #{item.id} ({item.source}), "
            f"accepted by {actor}."
        ),
        payload={"intake_item_id": item.id, "source": item.source},
        company_id=item.company_id,
    )
    if sprint.status == "active":
        # Queued now; in a planned or paused sprint it starts with the sprint.
        session.add(
            OutboxEvent(
                aggregate_type="backlog_item",
                aggregate_id=str(task.id),
                event_type="nats_publish",
                payload={
                    "subject": "task.assign",
                    "message": {
                        "backlog_item_id": task.id,
                        "interaction_id": str(uuid4()),
                        "agent": role,
                        "sprint_id": sprint.id,
                    },
                },
            )
        )
    return task


def _revision(
    session: Session, item: IntakeItem, actor: str, specification_id: int | None
) -> Specification:
    """The draft the request lands in: the specification named if it is a
    draft, else the open revision of the approved one, else a new revision."""
    if specification_id is None:
        raise IntakeRefused(
            422, "Accepting as a specification revision needs the specification"
        )
    spec = session.get(Specification, specification_id)
    if spec is None or spec.company_id != item.company_id:
        raise IntakeRefused(404, f"Specification {specification_id} not found")
    if spec.status == "superseded":
        raise IntakeRefused(
            409, "That specification was superseded; name the current one"
        )
    draft = spec
    if spec.status == "approved":
        draft = session.exec(
            select(Specification).where(
                Specification.supersedes_id == spec.id,
                Specification.status == "draft",
            )
        ).first()
        if draft is None:
            draft = Specification(
                company_id=spec.company_id,
                title=spec.title,
                summary=spec.summary,
                version=spec.version + 1,
                supersedes_id=spec.id,
                requirements=list(spec.requirements or []),
                quality=list(spec.quality or []),
                constraints=list(spec.constraints or []),
                questions=[],
                drafted_by=actor,
            )
    question = f"{QUESTION_PREFIX}{item.id}: {item.title}"
    if item.body.strip():
        question += f" — {item.body.strip()}"
    # An unanswered question blocks approval (spec_check): a person decides
    # how the revision takes the request in.
    draft.questions = [
        *(draft.questions or []),
        {"question": question[:1000], "answer": None},
    ]
    draft.updated_at = datetime.now(UTC)
    session.add(draft)
    session.flush()
    return draft


def accept(
    session: Session,
    item: IntakeItem,
    actor: str,
    *,
    target: str,
    category: str | None = None,
    reason: str | None = None,
    sprint_id: int | None = None,
    assignee: str | None = None,
    priority: str | None = None,
    specification_id: int | None = None,
) -> None:
    """Accept *item* as a task or a specification revision; links both ways."""
    if not intake.can_move(item.status, intake.ACCEPTED):
        raise IntakeRefused(409, f"The request is {item.status}; it cannot be accepted")
    if target == "task":
        task = _new_task(session, item, actor, sprint_id, assignee, priority)
        item.outcome_task_id = task.id
        outcome = intake.link("task", task.id)
    elif target == "specification":
        spec = _revision(session, item, actor, specification_id)
        item.outcome_specification_id = spec.id
        outcome = intake.link("specification", spec.id)
    else:
        raise IntakeRefused(422, "Accept as a 'task' or a 'specification' revision")
    _move(
        session,
        item,
        intake.ACCEPTED,
        actor,
        category=category,
        reason=reason,
        extra={"outcome": outcome},
    )


def record_brief(
    session: Session, company_id: int, brief: str, spec: Specification, actor: str
) -> IntakeItem:
    """The client's brief that the PO drafted *spec* from: received and
    accepted at once by the Manager who sent it, linked to the draft."""
    first_line = next((ln for ln in brief.splitlines() if ln.strip()), brief)
    item = receive(
        session,
        company_id,
        "brief",
        first_line.strip()[: intake.TITLE_LIMIT] or spec.title,
        brief,
        submitted_by=actor,
        source_ref=f"specification:{spec.id}",
    )
    item.outcome_specification_id = spec.id
    _move(
        session,
        item,
        intake.ACCEPTED,
        actor,
        category=None,
        reason="Drafted by the PO from the brief",
        extra={"outcome": intake.link("specification", spec.id)},
    )
    return item


def wake_due(session: Session, now: datetime | None = None) -> int:
    """Snoozed items whose date has passed, back to ``pending``; audited as
    the system's move. Commits; returns how many woke."""
    now = now or _now()
    due = session.exec(
        select(IntakeItem)
        .where(
            IntakeItem.status == intake.SNOOZED,
            IntakeItem.snoozed_until.is_not(None),
            IntakeItem.snoozed_until <= now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for item in due:
        until = item.snoozed_until
        item.status = intake.PENDING
        item.snoozed_until = None
        item.updated_at = now
        session.add(item)
        _audit(
            session,
            item,
            SYSTEM_ACTOR,
            "intake_woken",
            intake.SNOOZED,
            {"snoozed_until": until.isoformat() if until else None},
        )
    session.commit()
    return len(due)


def wake_cycle(engine) -> int:
    with Session(engine) as session:
        return wake_due(session)


async def run_intake_janitor(engine) -> None:
    """Background loop: wake the snoozed requests whose date passed, every
    ``WAKE_INTERVAL_SECONDS``, one replica at a time. It runs in the backend
    rather than the janitor container because each move is an audit row of
    the company's hash chain, which only the backend seals."""
    logger.info("Intake janitor started (every %ss)", WAKE_INTERVAL_SECONDS)
    while True:
        # Waits first, so startup stays quiet; an overdue item still wakes
        # within one interval.
        await asyncio.sleep(WAKE_INTERVAL_SECONDS)
        try:
            woke = await asyncio.to_thread(
                locks.run_exclusive, engine, locks.INTAKE_WAKE, wake_cycle, engine
            )
            if woke:
                logger.info("Intake janitor woke %s snoozed request(s)", woke)
        except Exception as exc:  # noqa: BLE001 - Keep the loop alive.
            logger.warning("Intake janitor cycle failed: %s", exc)

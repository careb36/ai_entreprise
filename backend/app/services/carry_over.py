"""
Carrying a blocked task into the next sprint.

A task the agents gave up on waits for a person. One way a person (or, at
autonomy level 3, the company itself) answers is to give it another attempt
in the next sprint: a fresh task with the same brief plus what blocked the
previous one, so the agent does not repeat it blind. The original stays on
its board, marked as carried over, and leaves the person's inbox.

A task is carried over at most once on the company's own initiative; one
that blocks again stays for a person. The ScrumMaster's triage of the block
(``services.scrum_master``) is honoured: a task it left for a person is not
carried on the company's own, and its brief for the next attempt travels
with the new task into the agent's prompt.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models import AgentMessage, BacklogItem, OutboxEvent, Sprint
from sqlmodel import Session, select

from services import (
    dependencies,
    scrum_master,
    sprint_team,
    task_events,
    task_projection,
)
from services.board import (
    CARRIED_FROM_PREFIX,
    CARRIED_TO_PREFIX,
    SCRUM_MASTER_ROLE,
    comment_list,
)

REASON_LIMIT = 500
# The Manager agent's mark on a blocked task its policy lets it carry over
# (``carry_over`` rule, issue #394), and a person's note taking it back.
CARRY_MARK_PREFIX = "Marked for carry-over by the Manager agent"
CARRY_UNMARK_PREFIX = "Carry-over mark taken back"


class CarryOverError(ValueError):
    """The task cannot be carried over; ``status`` says why (404 or 409)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _record(
    item: BacklogItem, record: task_projection.TaskRecord | None
) -> task_projection.TaskRecord:
    return record if record is not None else task_projection.TaskRecord(item.comments)


def waits_for_a_person(
    item: BacklogItem, record: task_projection.TaskRecord | None = None
) -> bool:
    """Blocked, and nothing the worker does on its own will move it.

    Read from *record* (``task_projection.record_of``: the events when they
    cover the thread), else from the comments.
    """
    return _record(item, record).waits_for_a_person(item.status)


def carried_over(
    item: BacklogItem, record: task_projection.TaskRecord | None = None
) -> bool:
    """Already given to another sprint: it no longer waits for a person."""
    return _record(item, record).carried("to")


def carry_mark(
    item: BacklogItem, record: task_projection.TaskRecord | None = None
) -> bool | None:
    """Whether the Manager agent marked the task for the next sprint: ``True``
    marked, ``False`` a person took the mark back, ``None`` never marked.

    Read from *record* (the events when they cover the thread), else from the
    comments.
    """
    return _record(item, record).carry_mark(CARRY_MARK_PREFIX, CARRY_UNMARK_PREFIX)


def is_carry_over(
    item: BacklogItem, record: task_projection.TaskRecord | None = None
) -> bool:
    """This task is itself a second attempt at an earlier one."""
    return _record(item, record).carried("from")


def left_for_a_person(session: Session, item: BacklogItem) -> bool:
    """The ScrumMaster's triage said a person must decide, not another attempt."""
    records = {item.id: task_projection.record_of(session, item)}
    return _left_for_a_person(
        scrum_master.triages_for(session, [item], records).get(item.id)
    )


def _left_for_a_person(triage: dict[str, Any] | None) -> bool:
    return bool(triage) and triage["decision"] == scrum_master.DECISION_PERSON


def off_team_reason(item: BacklogItem, sprint: Sprint) -> str:
    """Why *item* cannot go to *sprint*: its assignee is not on the team."""
    return (
        f"Task {item.id} is assigned to {item.assignee or '(none)'}, who is not on "
        f"sprint {sprint.id}'s team ({', '.join(sprint_team.team_of(sprint) or [])}); "
        "pick a sprint whose team has that role"
    )


def carry_over(
    session: Session,
    company_id: int,
    item_ids: list[int],
    new_sprint: Sprint,
    by: str,
    by_role: str,
) -> list[BacklogItem]:
    """Clone each blocked task of *company_id* into *new_sprint*, queued for an agent.

    Raises ``CarryOverError`` (404 unknown or another company's task, 409 not
    waiting for a person or already carried over) before writing anything.
    The caller commits.
    """
    rows: list[tuple[BacklogItem, Sprint]] = []
    for item_id in dict.fromkeys(item_ids):
        row = session.exec(
            select(BacklogItem, Sprint)
            .join(Sprint, Sprint.id == BacklogItem.sprint_id)
            .where(BacklogItem.id == item_id, Sprint.company_id == company_id)
        ).first()
        if row is None:
            raise CarryOverError(404, f"Task {item_id} not found in this company")
        rows.append((row[0], row[1]))
    # Every task's events in one query; the checks read them (issue #324).
    records = task_projection.records_for(session, [item for item, _ in rows])
    originals: list[tuple[BacklogItem, Sprint, task_projection.TaskRecord]] = []
    for item, sprint in rows:
        item_id = item.id
        record = records[item.id]
        if sprint.archived_at is not None:
            raise CarryOverError(
                409, f"Task {item_id} belongs to an archived sprint; unarchive it first"
            )
        if not waits_for_a_person(item, record):
            raise CarryOverError(
                409, f"Task {item_id} is not blocked waiting for a person"
            )
        if carried_over(item, record):
            raise CarryOverError(409, f"Task {item_id} was already carried over")
        # Checked before the original is marked: a clone the worker would
        # refuse must not use up the task's one carry-over.
        if not sprint_team.on_team(new_sprint, item.assignee):
            raise CarryOverError(409, off_team_reason(item, new_sprint))
        originals.append((item, sprint, record))

    now = datetime.now(UTC)
    created: list[BacklogItem] = []
    triages = scrum_master.triages_for(
        session,
        [item for item, _, _ in originals],
        {item.id: record for item, _, record in originals},
    )
    # A dependency between carried tasks is kept between their new attempts;
    # one on a task not carried is dropped (it is done, or a person's).
    carried_ids = {item.id for item, _, _ in originals}
    clones_by_original: dict[int, BacklogItem] = {}
    held: dict[int, list[int]] = {}
    for item, sprint, record in originals:
        reason = record.block_reason()[:REASON_LIMIT]
        brief = (triages.get(item.id) or {}).get("brief") or ""
        clone = BacklogItem(
            sprint_id=new_sprint.id,
            title=item.title,
            description=item.description or "",
            priority=item.priority,
            assignee=item.assignee,
            status="todo",
            # Criteria ids mean something only against the same specification.
            covers=list(item.covers)
            if isinstance(item.covers, list)
            and new_sprint.specification_id is not None
            and new_sprint.specification_id == sprint.specification_id
            else None,
            kind=item.kind,
        )
        session.add(clone)
        session.flush()
        task_events.record(
            session,
            clone,
            "carried_over",
            task_events.SYSTEM,
            text=(
                f'{CARRIED_FROM_PREFIX} task {item.id} ("{sprint.sprint_name}"), '
                f"which was blocked: {reason or 'no reason recorded'}"
                + (f"\n{scrum_master.BRIEF_PREFIX}{brief}" if brief else "")
            ),
            payload={"direction": "from", "task": item.id, "by": by},
            created_at=now.isoformat(),
            company_id=new_sprint.company_id,
        )
        clones_by_original[item.id] = clone
        held[clone.id] = [
            d for d in dependencies.depends_on_of(item) if d in carried_ids
        ]
        created.append(clone)

        task_events.record(
            session,
            item,
            "carried_over",
            task_events.SYSTEM,
            text=f'{CARRIED_TO_PREFIX} {new_sprint.id} ("{new_sprint.sprint_name}") as task {clone.id} by {by}.',
            payload={"direction": "to", "task": clone.id, "sprint": new_sprint.id},
            created_at=now.isoformat(),
        )

        if not held[clone.id]:
            session.add(
                OutboxEvent(
                    aggregate_type="backlog_item",
                    aggregate_id=str(clone.id),
                    event_type="nats_publish",
                    payload={
                        "subject": "task.assign",
                        "message": {
                            "backlog_item_id": clone.id,
                            "interaction_id": str(uuid4()),
                            "agent": clone.assignee or "PO",
                            "sprint_id": new_sprint.id,
                        },
                    },
                )
            )
        session.add(
            AgentMessage(
                company_id=company_id,
                sprint_id=new_sprint.id,
                backlog_item_id=clone.id,
                from_name=by,
                from_role=by_role,
                to_role=clone.assignee,
                kind="decision",
                body=(
                    f'"{item.title}" gets another attempt in sprint "{new_sprint.sprint_name}". '
                    f"Last time it was blocked: {reason or 'no reason recorded'}"
                    + (f"\n{scrum_master.BRIEF_PREFIX}{brief}" if brief else "")
                ),
            )
        )
    for clone in created:
        if held[clone.id]:
            # The worker queues it once its new dependencies are done.
            clone.depends_on = [clones_by_original[d].id for d in held[clone.id]]
            dependencies.note(
                session,
                clone,
                dependencies.waiting_note(
                    [clones_by_original[d] for d in held[clone.id]]
                ),
            )
            session.add(clone)
    return created


def previous_attempt_context(item: dict[str, Any] | BacklogItem) -> str:
    """The prompt section telling an agent what blocked the earlier attempt, or ''."""
    comments = item.get("comments") if isinstance(item, dict) else item.comments
    for comment in comment_list(comments):
        text = str(comment.get("text", ""))
        if text.startswith(CARRIED_FROM_PREFIX):
            return (
                "\n\n## Previous attempt\n"
                "This task was tried in an earlier sprint and blocked. Do not repeat "
                f"what blocked it.\n{text}"
            )
    return ""


def carry_over_on_the_companys_own(
    session: Session,
    company_id: int,
    closed_sprint_id: int,
    new_sprint: Sprint,
    room: int,
) -> list[BacklogItem]:
    """At sprint close with autonomy 3: give each blocked task one more attempt.

    Tasks that were already a second attempt, and tasks the ScrumMaster's
    triage left for a person, stay for a person. At most *room* tasks, oldest
    first, so the next sprint keeps within its scope.
    """
    if room <= 0:
        return []
    candidates = session.exec(
        select(BacklogItem)
        .where(
            BacklogItem.sprint_id == closed_sprint_id, BacklogItem.status == "blocked"
        )
        .order_by(BacklogItem.id)
    ).all()
    candidates = list(candidates)
    records = task_projection.records_for(session, candidates)
    triages = scrum_master.triages_for(session, candidates, records)
    ids = [
        item.id
        for item in candidates
        if waits_for_a_person(item, records[item.id])
        and not carried_over(item, records[item.id])
        and not is_carry_over(item, records[item.id])
        and not _left_for_a_person(triages.get(item.id))
        # A person took back the Manager agent's mark: it waits for them.
        and carry_mark(item, records[item.id]) is not False
        # Approved, waiting for a person to accept it: not another attempt.
        and not records[item.id].awaits_risk_acceptance()
    ][:room]
    if not ids:
        return []
    return carry_over(
        session, company_id, ids, new_sprint, SCRUM_MASTER_ROLE, SCRUM_MASTER_ROLE
    )

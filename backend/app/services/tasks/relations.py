"""Typed relations between tasks, across sprints (issue #533).

``depends_on`` links tasks only within one sprint. A duplicate, related work
in another sprint or the attempt a carry-over made had no link anyone could
query. A ``TaskRelation`` (``models/task_relation.py``) is that link: one row
per pair of tasks of one company, ``task`` is ``kind`` of ``related task``.

- ``KINDS`` are ``blocked_by``, ``duplicate`` and ``relates_to``; ``REVERSE``
  is the one fixed table of the names read from the other task (``blocking``
  for ``blocked_by``). A request may name either side; ``blocking`` is stored
  as ``blocked_by`` with the tasks swapped.
- ``propose`` writes a ``proposed`` relation (an agent's claim, or a person's
  suggestion) or, with ``confirmed_by``, a confirmed one (a person, or a
  deterministic rule such as carry-over's ``rule:carry_over``). ``confirm``,
  ``reject`` (a proposed one) and ``remove`` (either) are a person's.
- Only a confirmed ``blocked_by`` between two tasks of one sprint reaches
  dispatch: confirming it adds the blocker to the task's ``depends_on``, and
  ``services/tasks/dependencies.py`` does the rest as for a plan's
  dependency. A proposed relation, and every other kind, is information only.
- Every change is a ``relation`` event on both tasks (``task_events.record``,
  no comment), so both hash chains cover it.

The design is Plane's ``IssueRelation`` (AGPL-3.0, design only): the choices
and their reverse pairs, one row per pair under a unique constraint, and the
mapper that names a relation from either side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import BacklogItem, Sprint, TaskEvent, TaskRelation
from sqlalchemy import or_
from sqlmodel import Session, select

from services.contracts.task_dependencies import depends_on_of
from services.tasks import dependencies, similarity, task_events

BLOCKED_BY = "blocked_by"
DUPLICATE = "duplicate"
RELATES_TO = "relates_to"
KINDS = (BLOCKED_BY, DUPLICATE, RELATES_TO)
# The name of each kind read from the related task's side.
REVERSE = {BLOCKED_BY: "blocking", DUPLICATE: DUPLICATE, RELATES_TO: RELATES_TO}
# Every name a request may use, and the stored kind and direction it means.
NAMES = {**{kind: (kind, False) for kind in KINDS}, "blocking": (BLOCKED_BY, True)}
PROPOSED = "proposed"
CONFIRMED = "confirmed"
STATES = (PROPOSED, CONFIRMED)
EVENT_KIND = "relation"
PERSON = task_events.PERSON
SYSTEM = task_events.SYSTEM
RULE_CARRY_OVER = "rule:carry_over"
REASON_LIMIT = 300
# A task the worker has not started yet: a dependency added now cannot be
# one it worked before (``history_check.worked_before_dependency``).
_NOT_STARTED = {"todo"}


class RelationError(ValueError):
    """A relation that cannot be written; ``status`` is the HTTP status."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def name_for(relation: TaskRelation, task_id: int) -> str:
    """The relation's name read from *task_id*'s side."""
    if relation.task_id == task_id:
        return relation.kind
    return REVERSE.get(relation.kind, relation.kind)


def other_of(relation: TaskRelation, task_id: int) -> int:
    return relation.related_task_id if relation.task_id == task_id else relation.task_id


def normalise(name: str, task_id: int, other_id: int) -> tuple[str, int, int]:
    """The stored kind and its ``(task, related task)`` for a request's name."""
    try:
        kind, swapped = NAMES[str(name or "").strip().lower()]
    except KeyError:
        raise RelationError(
            f"Unknown relation {name!r}; one of {', '.join(NAMES)}"
        ) from None
    return (kind, other_id, task_id) if swapped else (kind, task_id, other_id)


def _sprint(session: Session, item: BacklogItem) -> Sprint:
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is None:
        raise RelationError(f"Task {item.id} has no sprint", 404)
    return sprint


def between(session: Session, a: int, b: int) -> TaskRelation | None:
    """The relation between tasks *a* and *b*, whichever way round, or none."""
    low, high = sorted((a, b))
    return session.exec(
        select(TaskRelation).where(
            TaskRelation.pair_low == low, TaskRelation.pair_high == high
        )
    ).first()


def _record(
    session: Session,
    relation: TaskRelation,
    action: str,
    actor: str,
    by: str,
    extra: dict[str, Any] | None = None,
) -> list[TaskEvent]:
    """The change as a ``relation`` event on both tasks, each named from its side."""
    written = []
    for task_id in (relation.task_id, relation.related_task_id):
        item = session.get(BacklogItem, task_id)
        if item is None:
            continue
        written.append(
            task_events.record(
                session,
                item,
                EVENT_KIND,
                actor,
                payload={
                    "action": action,
                    "relation_id": relation.id,
                    "relation": name_for(relation, task_id),
                    "stored_kind": relation.kind,
                    "other_task": other_of(relation, task_id),
                    "state": relation.state,
                    "by": by,
                    # A relation nobody confirmed is its proposer's claim.
                    "claim": relation.state == PROPOSED,
                    **(extra or {}),
                },
                company_id=relation.company_id,
            )
        )
    return written


def _add_dependency(session: Session, relation: TaskRelation) -> bool:
    """Put a confirmed same-sprint ``blocked_by`` into ``depends_on``.

    Only for a task not started yet; a cycle among the sprint's dependencies
    is refused (409). Returns whether ``depends_on`` changed.
    """
    if relation.kind != BLOCKED_BY or relation.state != CONFIRMED:
        return False
    item = session.get(BacklogItem, relation.task_id)
    blocker = session.get(BacklogItem, relation.related_task_id)
    if item is None or blocker is None or item.sprint_id != blocker.sprint_id:
        return False
    if item.status not in _NOT_STARTED:
        return False
    deps = depends_on_of(item)
    if blocker.id in deps:
        return False
    graph = {
        other.id: depends_on_of(other)
        for other in dependencies.sprint_items(session, item.sprint_id).values()
    }
    graph[item.id] = [*deps, blocker.id]
    if cycle := dependencies.find_cycle(graph):
        raise RelationError(
            "The dependencies would run in a circle: "
            + " → ".join(str(n) for n in cycle),
            409,
        )
    item.depends_on = [*deps, blocker.id]
    session.add(item)
    return True


def _drop_dependency(session: Session, relation: TaskRelation) -> None:
    if not relation.added_dependency:
        return
    item = session.get(BacklogItem, relation.task_id)
    if item is None:
        return
    item.depends_on = [
        d for d in depends_on_of(item) if d != relation.related_task_id
    ] or None
    session.add(item)


def propose(
    session: Session,
    item: BacklogItem,
    other: BacklogItem,
    name: str,
    *,
    by: str,
    role: str,
    confirmed_by: str | None = None,
) -> TaskRelation:
    """Relate *item* to *other* as *name*; flushed, not committed.

    *by* is who proposed it and *role* as what (``person``, ``system`` or an
    agent's role). With *confirmed_by* (a person, or a ``rule:`` name) it is
    written confirmed. Refuses a task related to itself (422), tasks of two
    companies (404, as if the other did not exist), an archived sprint (409)
    and a pair already related (409).
    """
    if item.id is None or other.id is None:
        raise RelationError("Both tasks must exist", 404)
    if item.id == other.id:
        raise RelationError("A task cannot be related to itself")
    kind, task_id, related_id = normalise(name, item.id, other.id)
    sprint, other_sprint = _sprint(session, item), _sprint(session, other)
    if sprint.company_id != other_sprint.company_id:
        raise RelationError(f"Task {other.id} not found in this company", 404)
    if sprint.archived_at is not None or other_sprint.archived_at is not None:
        raise RelationError("A task of an archived sprint cannot be related", 409)
    if (existing := between(session, item.id, other.id)) is not None:
        raise RelationError(
            f"Tasks {item.id} and {other.id} are already related "
            f"({existing.kind}, {existing.state}); remove that relation first",
            409,
        )
    low, high = sorted((task_id, related_id))
    relation = TaskRelation(
        company_id=sprint.company_id,
        task_id=task_id,
        related_task_id=related_id,
        pair_low=low,
        pair_high=high,
        kind=kind,
        state=CONFIRMED if confirmed_by else PROPOSED,
        proposed_by=(by or "").strip()[:200] or role,
        proposed_role=role,
        confirmed_by=(confirmed_by or "").strip()[:200] or None,
        confirmed_at=_now() if confirmed_by else None,
        created_at=_now(),
    )
    session.add(relation)
    session.flush()
    relation.added_dependency = _add_dependency(session, relation)
    session.add(relation)
    actor = task_events.PERSON if role == task_events.PERSON else role
    _record(
        session,
        relation,
        "proposed" if relation.state == PROPOSED else "added",
        actor,
        relation.proposed_by,
        {
            "confirmed_by": relation.confirmed_by,
            "depends_on": relation.added_dependency,
        },
    )
    return relation


def best_duplicate(
    item: BacklogItem, others: list[BacklogItem]
) -> tuple[BacklogItem, float] | None:
    """The task of *others* whose title most likely repeats *item*'s, with its
    score, when one reaches ``similarity.THRESHOLD``; done tasks never count."""
    best: tuple[BacklogItem, float] | None = None
    for other in others:
        if other.id == item.id or other.status == "done":
            continue
        score = similarity.similarity(item.title, other.title)
        if score >= similarity.THRESHOLD and (best is None or score > best[1]):
            best = (other, score)
    return best


def propose_likely_duplicate(
    session: Session, item: BacklogItem, other: BacklogItem, score: float
) -> TaskRelation | None:
    """Record that *item* likely repeats *other* as a *proposed* ``duplicate``
    a person confirms or rejects; ``None`` when the pair is already related."""
    if between(session, item.id, other.id) is not None:
        return None
    return propose(
        session,
        item,
        other,
        DUPLICATE,
        by=f"{similarity.RULE} ({score:.2f})",
        role=SYSTEM,
    )


def propose_likely_duplicates(
    session: Session, items: list[BacklogItem]
) -> list[TaskRelation]:
    """Propose a ``duplicate`` for each pair of *items* (one plan) that likely
    repeat each other. Information only: both tasks still run."""
    proposed = []
    for position, item in enumerate(items):
        match = best_duplicate(item, items[position + 1 :])
        if match is not None:
            relation = propose_likely_duplicate(session, item, *match)
            if relation is not None:
                proposed.append(relation)
    return proposed


def confirm(session: Session, relation: TaskRelation, by: str) -> TaskRelation:
    """A person confirms a proposed relation; flushed, not committed."""
    if relation.state == CONFIRMED:
        raise RelationError("The relation is already confirmed", 409)
    relation.state = CONFIRMED
    relation.confirmed_by = (by or "").strip()[:200] or task_events.PERSON
    relation.confirmed_at = _now()
    relation.added_dependency = _add_dependency(session, relation)
    session.add(relation)
    _record(
        session,
        relation,
        "confirmed",
        task_events.PERSON,
        relation.confirmed_by,
        {"depends_on": relation.added_dependency},
    )
    session.flush()
    return relation


def _delete(
    session: Session, relation: TaskRelation, action: str, by: str, reason: str | None
) -> None:
    _drop_dependency(session, relation)
    _record(
        session,
        relation,
        action,
        task_events.PERSON,
        (by or "").strip()[:200] or task_events.PERSON,
        {
            "reason": (reason or "").strip()[:REASON_LIMIT] or None,
            "depends_on_removed": relation.added_dependency,
        },
    )
    session.delete(relation)
    session.flush()


def reject(
    session: Session, relation: TaskRelation, by: str, reason: str | None = None
) -> None:
    """A person rejects a proposed relation: the row goes, the events stay."""
    if relation.state != PROPOSED:
        raise RelationError("Only a proposed relation can be rejected", 409)
    _delete(session, relation, "rejected", by, reason)


def remove(
    session: Session, relation: TaskRelation, by: str, reason: str | None = None
) -> None:
    """A person removes a relation; a dependency it added to dispatch goes too."""
    _delete(session, relation, "removed", by, reason)


def for_task(session: Session, task_id: int) -> list[TaskRelation]:
    """The relations of a task, from either side, oldest first."""
    return list(
        session.exec(
            select(TaskRelation)
            .where(
                or_(
                    TaskRelation.task_id == task_id,
                    TaskRelation.related_task_id == task_id,
                )
            )
            .order_by(TaskRelation.id)  # type: ignore[arg-type]
        ).all()
    )


def for_tasks(session: Session, task_ids: list[int]) -> list[TaskRelation]:
    """The relations touching any of *task_ids*, oldest first."""
    if not task_ids:
        return []
    return list(
        session.exec(
            select(TaskRelation)
            .where(
                or_(
                    TaskRelation.task_id.in_(task_ids),  # type: ignore[attr-defined]
                    TaskRelation.related_task_id.in_(task_ids),  # type: ignore[attr-defined]
                )
            )
            .order_by(TaskRelation.id)  # type: ignore[arg-type]
        ).all()
    )


def _other_view(session: Session, task_id: int) -> dict[str, Any]:
    other = session.get(BacklogItem, task_id)
    if other is None:
        return {"id": task_id, "title": None, "status": None, "sprint_id": None}
    sprint = session.get(Sprint, other.sprint_id)
    return {
        "id": other.id,
        "title": other.title,
        "status": other.status,
        "sprint_id": other.sprint_id,
        "sprint_name": sprint.sprint_name if sprint is not None else None,
    }


def as_dict(session: Session, relation: TaskRelation, task_id: int) -> dict[str, Any]:
    """The relation as *task_id*'s side reads it, with the other task's facts."""
    return {
        "id": relation.id,
        "relation": name_for(relation, task_id),
        "stored_kind": relation.kind,
        "task_id": relation.task_id,
        "related_task_id": relation.related_task_id,
        "other_task": _other_view(session, other_of(relation, task_id)),
        "state": relation.state,
        "proposed_by": relation.proposed_by,
        "proposed_role": relation.proposed_role,
        "confirmed_by": relation.confirmed_by,
        "confirmed_at": relation.confirmed_at.isoformat()
        if relation.confirmed_at
        else None,
        # Whether dispatch waits on it (a confirmed same-sprint blocked_by).
        "drives_dispatch": bool(relation.added_dependency),
        "created_at": relation.created_at.isoformat() if relation.created_at else None,
    }


def sprint_metrics(session: Session, sprint_id: int) -> dict[str, Any]:
    """The relations of a sprint's tasks, by state, and the issue's measure:
    ``duplicates_delivered``, the confirmed duplicate pairs with a task in this
    sprint where both tasks were delivered (done), work run twice."""
    items = dependencies.sprint_items(session, sprint_id)
    rows = for_tasks(session, list(items))
    tally: dict[str, Any] = {
        "total": len(rows),
        **{state: 0 for state in STATES},
        "by_kind": {kind: 0 for kind in KINDS},
        "duplicates_delivered": 0,
    }
    for row in rows:
        tally[row.state] = tally.get(row.state, 0) + 1
        tally["by_kind"][row.kind] = tally["by_kind"].get(row.kind, 0) + 1
        if row.kind == DUPLICATE and row.state == CONFIRMED:
            both = [
                session.get(BacklogItem, t) for t in (row.task_id, row.related_task_id)
            ]
            if all(t is not None and t.status == "done" for t in both):
                tally["duplicates_delivered"] += 1
    return tally

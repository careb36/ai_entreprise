"""Dependencies between the tasks of a sprint, and the order they impose.

A plan may say that a task needs others done first (``depends_on``: the
ids of tasks in the same sprint). The worker then follows that order
instead of the queue's: a task waits in ``todo`` until every task it
depends on is done, and is queued the moment the last one closes; tasks
with nothing between them run in parallel as before. When a task a person
must act on (blocked for good) has tasks waiting on it, those are blocked
too, with the reason, so the sprint can close around them and the inbox
lists what the person's decision unblocks; when the dependency is done
after all, the tasks it held are released to ``todo`` again.

What a plan or a request asks is checked here, deterministically: an
unknown task, a task that depends on itself or a cycle is refused.
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Sprint
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from services.contracts.task_dependencies import depends_on_of
from services.tasks import task_events, task_projection

WAITING_PREFIX = "Waits for task"
BLOCKED_BY_DEPENDENCY_PREFIX = "Blocked: waits for task"
RELEASED_PREFIX = "Released: task"
SYSTEM = "System"


class DependencyError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def find_cycle(graph: dict[int, list[int]]) -> list[int] | None:
    """A cycle in *graph* (node → nodes it depends on) as the nodes along it, or ``None``."""
    state: dict[int, int] = {}
    stack: list[int] = []

    def visit(node: int) -> list[int] | None:
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                return stack[stack.index(dep) :] + [dep]
            if state.get(dep) is None and (cycle := visit(dep)):
                return cycle
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if state.get(node) is None and (cycle := visit(node)):
            return cycle
    return None


def check_positions(tasks: list[dict[str, Any]]) -> list[list[int]]:
    """Each task's ``depends_on`` as positions in *tasks*, checked.

    Refuses (422) a position that names no task, a task that depends on
    itself and a cycle; the lists come back without repeats.
    """
    graph: dict[int, list[int]] = {}
    for position, task in enumerate(tasks):
        raw = task.get("depends_on") or []
        if not isinstance(raw, list):
            raise DependencyError(
                422, f"Task {position}: depends_on must be a list of positions"
            )
        deps: list[int] = []
        for value in raw:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value < len(tasks)
            ):
                raise DependencyError(
                    422, f"Task {position}: depends on no task at position {value!r}"
                )
            if value == position:
                raise DependencyError(422, f"Task {position}: depends on itself")
            if value not in deps:
                deps.append(value)
        graph[position] = deps
    if cycle := find_cycle(graph):
        raise DependencyError(
            422,
            "The dependencies run in a circle: " + " → ".join(str(n) for n in cycle),
        )
    return [graph[position] for position in range(len(tasks))]


def without_cycles(graph: dict[int, list[int]]) -> dict[int, list[int]]:
    """*graph* with the edges that close a cycle dropped, one at a time, in order."""
    cleaned = {
        node: [d for d in deps if d != node and d in graph]
        for node, deps in graph.items()
    }
    while (cycle := find_cycle(cleaned)) is not None:
        last, first = cycle[-2], cycle[-1]
        cleaned[last] = [d for d in cleaned[last] if d != first]
    return cleaned


def unmet(item: Any, done_ids: set[int]) -> list[int]:
    """The dependencies of *item* not done yet."""
    return [dep for dep in depends_on_of(item) if dep not in done_ids]


def note(
    session: Session,
    item: BacklogItem,
    text: str,
    kind: str = "note",
    status: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    """The System's note on *item*, written as its event (``task_events.record``)."""
    return task_events.record(
        session,
        item,
        kind,
        task_events.SYSTEM,
        text=text,
        status=status,
        payload=payload,
    )


def _blocked_by_dependency(
    item: BacklogItem, record: task_projection.TaskRecord
) -> bool:
    """Held by a dependency: the System's latest note is the hold (from its event
    when the events cover the thread); a comment anyone else appends does not count."""
    return item.status == "blocked" and record.held_by_dependency(
        BLOCKED_BY_DEPENDENCY_PREFIX
    )


def waiting_note(deps: list[BacklogItem]) -> str:
    return (
        f"{WAITING_PREFIX}{'s' if len(deps) > 1 else ''} "
        + ", ".join(f'{d.id} ("{d.title}")' for d in deps)
        + " to be done first."
    )


def sprint_items(session: Session, sprint_id: int) -> dict[int, BacklogItem]:
    return {
        item.id: item
        for item in session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
        ).all()
    }


def is_ready(session: Session, item: BacklogItem) -> bool:
    """Whether every task *item* depends on is done; a task without any is ready."""
    deps = depends_on_of(item)
    if not deps:
        return True
    done = {
        i.id
        for i in session.exec(
            select(BacklogItem).where(
                BacklogItem.id.in_(deps), BacklogItem.status == "done"
            )
        ).all()
    }
    return not unmet(item, done)


def ready(engine: Engine, item_id: int) -> bool:
    """``is_ready`` for the task *item_id*, read with *engine*; an unknown task is not."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        return item is not None and is_ready(session, item)


def settle(session: Session, sprint_id: int) -> dict[str, list[int]]:
    """Block the tasks waiting on one a person must act on; release those whose wait ended.

    Returns ``{"blocked": [...], "released": [...]}``; the caller commits.
    """
    items = sprint_items(session, sprint_id)
    # Every task's events in one query; the decisions below read them.
    histories = task_projection.histories_for(session, list(items.values()))

    def record(item: BacklogItem) -> task_projection.TaskRecord:
        return task_projection.record_of(session, item, histories)

    def write(item: BacklogItem, text: str, **fields: Any) -> None:
        event = note(session, item, text, **fields)
        histories.setdefault(item.id, []).append(task_events.as_dict(event))

    done = {i for i, item in items.items() if item.status == "done"}
    held = {
        i for i, item in items.items() if _blocked_by_dependency(item, record(item))
    }
    stuck = {
        i
        for i, item in items.items()
        if item.status == "blocked"
        and record(item).is_settled(item.status)
        and i not in held
    }
    # A task already held stays a blocker for its own dependents while what it
    # waits for is still stuck; one whose wait ended is released below.
    changed = True
    while changed:
        changed = False
        for i in held - stuck:
            if any(d in stuck for d in unmet(items[i], done)):
                stuck.add(i)
                changed = True
    blocked: list[int] = []
    released: list[int] = []
    # Transitively: a task waiting on a task that is itself blocked by a dependency.
    changed = True
    while changed:
        changed = False
        for item in items.values():
            pending = unmet(item, done)
            if item.status == "todo" and any(
                dep in stuck or dep in blocked for dep in pending
            ):
                holding = [
                    items[d]
                    for d in pending
                    if d in items and (d in stuck or d in blocked)
                ]
                write(
                    item,
                    f"{BLOCKED_BY_DEPENDENCY_PREFIX}{'s' if len(holding) > 1 else ''} "
                    + ", ".join(f'{d.id} ("{d.title}")' for d in holding)
                    + ", which waits for a person.",
                    kind="blocked",
                    status="blocked",
                    payload={"hold": task_projection.DEPENDENCY_HOLD},
                )
                session.add(item)
                blocked.append(item.id)
                changed = True
            elif (
                _blocked_by_dependency(item, record(item))
                and item.id not in blocked
                and item.id not in stuck
                and not any(dep in stuck or dep in blocked for dep in pending)
            ):
                # Its wait ended: the tasks got done, or are back in work (a
                # person sent one back); the worker queues it once they are done.
                plural = "s" if len(depends_on_of(item)) > 1 else ""
                write(
                    item,
                    f"{RELEASED_PREFIX}{plural} it waited for got done."
                    if not pending
                    else f"{RELEASED_PREFIX}{plural} it waited for are back in work; "
                    + waiting_note([items[d] for d in pending if d in items]),
                    kind="released",
                    status="todo",
                )
                session.add(item)
                released.append(item.id)
                changed = True
    return {"blocked": blocked, "released": released}


def sprint_settled(session: Session, sprint_id: int) -> bool:
    """Whether every task of the sprint is settled, once the dependency holds
    are reconciled: a hold whose wait ended is released first, so a sprint
    never closes over a task that could run. A sprint without tasks is not."""
    settle(session, sprint_id)
    items = list(
        session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
        ).all()
    )
    records = task_projection.records_for(session, items)
    return bool(items) and all(
        records[item.id].is_settled(item.status) for item in items
    )


def settle_active(engine: Engine) -> dict[str, list[int]]:
    """``settle`` for every active sprint with a task that depends on another."""
    totals: dict[str, list[int]] = {"blocked": [], "released": []}
    with Session(engine) as session:
        sprint_ids = {
            sprint_id
            for sprint_id, depends_on in session.exec(
                select(BacklogItem.sprint_id, BacklogItem.depends_on).where(
                    BacklogItem.depends_on.is_not(None)
                )
            ).all()
            if depends_on
        }
        for sprint_id in sorted(sprint_ids):
            sprint = session.get(Sprint, sprint_id)
            if sprint is None or sprint.status != "active":
                continue
            result = settle(session, sprint_id)
            totals["blocked"] += result["blocked"]
            totals["released"] += result["released"]
        session.commit()
    return totals


def waiting_for(items: dict[int, BacklogItem], item: BacklogItem) -> list[int]:
    """The dependencies of *item* not done, for the board."""
    return unmet(item, {i for i, other in items.items() if other.status == "done"})

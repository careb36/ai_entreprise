"""
A task's life as events: every transition is written here first (issue #324).

``record`` is the one function a transition goes through. It appends a
``TaskEvent`` (what happened, who did it, the run or verification it is
about, the task's status after it) to the task's hash chain, and renders the
event as the board comment people read, with the event's id on it, so the
comment is the event's rendering and not a second source. The caller commits.

The model follows OpenHands' event stream (``openhands/events``): typed
events, each with a source (``actor``) and a cause (``run_id`` /
``verification_id``), persisted and replayable; the trajectory is the record.

Kinds:

- ``claimed``: the worker took the task (``in_progress``);
- ``advised``: advice the task got (console assistants, the Architect's brief);
  context, never a verdict;
- ``delivered``: the producer's deliverable, the text QA reviews;
- ``verified``: the Verifier's reading of a delivery (``verification_id``);
- ``reviewed``: a verdict, ``payload.verdict`` ``approved`` or ``needs_work``,
  from QA, a gate or a person;
- ``sent_back``: back to work, by the system (autopilot rework) or a person;
- ``blocked``: held, with what the hold needs in its flags;
- ``released``: a dependency hold ended;
- ``escalated`` / ``decided``: a block handed to the ScrumMaster and its answer
  (events only, the messages carry the text);
- ``approved_by_person``: a person accepted the task;
- ``carried_over``: the task was carried into another sprint, or came from one
  (``payload.direction``);
- ``risk_hold``: QA approved a delivery that waits for a person;
- ``review_failed``, ``retried``, ``recovered``, ``paused``: the worker's own
  steps; ``moved``: a person changed the status on the board; ``note``: a note
  with no transition;
- ``integration``: a sprint's integration of task branches;
- ``estimated``: the task's difficulty as its plan stood before an attempt
  and the routing it led to (``payload.difficulty``, ``payload.routing``;
  ``services/models/difficulty.py``); an event only, no comment;
- ``board_note``: a note on the sprint board (``services/tasks/sprint_board.py``)
  written or marked wrong, with its author: a claim, never a verdict; and
  ``board_note_rejected``: the blocks of a delivery that were not notes, and why;
- ``legacy``: the migration found a thread it could not read back whole.

Each event with text carries the flags the board's questions read
(``services/tasks/task_projection.py``): ``verdict``, ``explicit_block``,
``needs_person``, ``retryable``, ``retry``, ``frozen``. The writer passes them
when it knows them; otherwise they follow from the rendered text, read once
here, when the event is written.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import audit_signing
from audit_signing import SIGNED_PREFIX, UNSIGNED_PREFIX
from models import BacklogItem, Sprint, TaskEvent
from sqlalchemy import text as sql_text
from sqlmodel import Session, select

from services.tasks import board

KINDS = frozenset(
    {
        "claimed",
        "advised",
        "delivered",
        "verified",
        "reviewed",
        "sent_back",
        "blocked",
        "released",
        "escalated",
        "decided",
        "approved_by_person",
        "carried_over",
        "risk_hold",
        "review_failed",
        "retried",
        "recovered",
        "paused",
        "moved",
        "note",
        "integration",
        "estimated",
        "board_note",
        "board_note_rejected",
        "legacy",
    }
)
SYSTEM = "system"
GATE = "gate"
PERSON = "person"
# The comment author each actor's comments carry on the board.
_DEFAULT_AUTHORS = {SYSTEM: "System", PERSON: "user"}
FLAG_NAMES = ("explicit_block", "needs_person", "retryable", "retry", "frozen")
# The cap on text the platform writes; a person's text is kept whole.
TEXT_LIMIT = 4000
CHAIN_VERSION = 1
# The advisory-lock namespace for task chains ("tevt" in ASCII).
_LOCK_NAMESPACE = 0x74657674


class UnknownEventKind(ValueError):
    pass


def actor_for(author: str | None) -> str:
    """The actor behind a comment author: the system, a gate, a person or a role."""
    name = str(author or "").strip()
    lowered = name.lower()
    if lowered == "system":
        return SYSTEM
    if lowered in board.GATE_AUTHORS_LOWER:
        return GATE
    if lowered == "user":
        return PERSON
    return name or SYSTEM


def flags_for(author: str, text: str) -> dict[str, Any]:
    """What a rendered comment says for the board's questions, read once.

    The same rules ``services/tasks/board.py`` applies to a thread, applied to one
    comment when its event is written, so a projection over the events and
    the readers over the thread give the same answers.
    """
    comment = {"author": author, "text": text}
    lowered_author = str(author or "").strip().lower()
    lowered = (text or "").strip().lower()
    advice = (
        board.is_lineage_comment(comment)
        or board.is_console_advice_note(comment)
        or board.is_architect_consult_comment(comment)
    )
    if advice:
        return {name: False for name in FLAG_NAMES} | {"verdict": None}
    rework = board.is_rework_request(comment)
    system = lowered_author == "system"
    gate_or_system = system or lowered_author in board.GATE_AUTHORS_LOWER
    verdict = None
    if rework:
        verdict = "needs_work"
    elif lowered.upper().startswith("APPROVED:") and not (
        gate_or_system or lowered_author == "user"
    ):
        verdict = "approved"
    return {
        "verdict": verdict,
        "explicit_block": lowered_author in board.BLOCK_VERDICT_AUTHORS
        and board.is_explicit_block_reason(text),
        "needs_person": (gate_or_system and "manual triage" in lowered)
        or (system and "qa quality gate failed" in lowered)
        or board.is_risk_hold_note(comment)
        or rework,
        "retryable": system
        and any(
            phrase in lowered
            for phrase in (
                "qa review failed",
                "agent error:",
                "no deliverable found",
                "unfinished in-progress task returned to todo for redispatch",
            )
        ),
        "retry": system and "retry" in lowered,
        "frozen": "sleep-induced churn" in lowered
        or lowered.startswith("freeze after"),
    }


def _naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _chain_input(event: TaskEvent) -> bytes:
    payload = {
        "v": CHAIN_VERSION,
        "company_id": event.company_id,
        "sprint_id": event.sprint_id,
        "task_id": event.task_id,
        "seq": event.seq,
        "prev": event.prev_hash or "",
        "kind": event.event_type,
        "actor": event.actor,
        "run_id": event.run_id,
        "verification_id": event.verification_id,
        "task_status": event.task_status,
        "legacy": bool(event.legacy),
        "payload": event.payload or {},
        "created_at": event.created_at.replace(tzinfo=None).isoformat()
        if event.created_at
        else "",
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def event_hash(event: TaskEvent, key: str | None) -> str:
    """The chain hash of one event, keyed like the audit chain when a key is set."""
    data = _chain_input(event)
    if key is None:
        return f"{UNSIGNED_PREFIX}{hashlib.sha256(data).hexdigest()}"
    return f"{SIGNED_PREFIX}{hmac.new(key.encode('utf-8'), data, hashlib.sha256).hexdigest()}"


def _chain_scope(event: TaskEvent):
    if event.task_id is not None:
        return TaskEvent.task_id == event.task_id
    return (TaskEvent.task_id.is_(None)) & (TaskEvent.sprint_id == event.sprint_id)  # type: ignore[union-attr]


def lock_chain(session: Session, key: int) -> None:
    """Take the chain lock of task *key* (``-sprint_id`` for a sprint's own
    chain) until the transaction ends; a no-op off Postgres.

    Every writer takes it before it touches the task's row, as ``record``
    does, so a writer that also changes the row first (a conditional claim)
    takes it beforehand and keeps one lock order. Reentrant: ``seal`` takes
    it again. Run on the connection, so nothing pending is flushed (and no
    row locked) before it.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    session.connection().execute(
        sql_text("SELECT pg_advisory_xact_lock(:ns, :key)").bindparams(
            ns=_LOCK_NAMESPACE, key=key
        )
    )


def seal(session: Session, event: TaskEvent) -> TaskEvent:
    """Number *event* in its task's chain, link it and hash it; flushed, not committed."""
    lock_chain(
        session, event.task_id if event.task_id is not None else -event.sprint_id
    )
    last = session.exec(
        select(TaskEvent)
        .where(_chain_scope(event))
        .where(TaskEvent.seq.is_not(None))  # type: ignore[union-attr]
        .order_by(TaskEvent.seq.desc())  # type: ignore[union-attr]
        .limit(1)
    ).first()
    event.seq = (last.seq if last is not None else 0) + 1
    event.prev_hash = last.chain_hash if last is not None else None
    event.chain_hash = event_hash(event, audit_signing._configured_key())
    session.add(event)
    session.flush()
    return event


def record(
    session: Session,
    item: BacklogItem,
    kind: str,
    actor: str,
    *,
    text: str | None = None,
    author: str | None = None,
    status: str | None = None,
    run_id: int | None = None,
    verification_id: int | None = None,
    payload: dict[str, Any] | None = None,
    flags: dict[str, Any] | None = None,
    created_at: str | None = None,
    company_id: int | None = None,
) -> TaskEvent:
    """Write one transition of *item*: the event, then the comment rendering it.

    *status*, when given, is the task's status after the transition. *text*
    is the comment people read (none for an event without one, such as an
    escalation whose text is the message). The caller commits.
    """
    if kind not in KINDS:
        raise UnknownEventKind(kind)
    if not actor:
        raise ValueError("an event needs an actor")
    if item.id is None:
        session.add(item)
        session.flush()
    if company_id is None:
        sprint = session.get(Sprint, item.sprint_id)
        company_id = sprint.company_id if sprint is not None else None
    if company_id is None:
        raise ValueError(f"task {item.id} has no company")
    now = _naive_now()
    body = dict(payload or {})
    # A person's text is kept whole (a review body and its line comments are
    # the Developer's instructions); only what the platform writes is capped.
    rendered = (text if actor == PERSON else text[:TEXT_LIMIT]) if text else None
    if rendered:
        comment_author = author or _DEFAULT_AUTHORS.get(actor, actor)
        implied = actor_for(comment_author)
        if (implied != actor) and ({implied, actor} & {SYSTEM, GATE, PERSON}):
            # The projection reads the actor where the thread reads the
            # author; the two must name the same party.
            raise ValueError(f"actor {actor!r} cannot sign as {comment_author!r}")
        body.setdefault("author", comment_author)
        body["text"] = rendered
        for name, value in flags_for(comment_author, rendered).items():
            body.setdefault(name, value)
    for name, value in (flags or {}).items():
        body[name] = value
    if status:
        item.status = status
    event = TaskEvent(
        company_id=company_id,
        sprint_id=item.sprint_id,
        task_id=item.id,
        event_type=kind,
        actor=actor,
        run_id=run_id,
        verification_id=verification_id,
        task_status=status,
        payload=body,
        created_at=now,
    )
    seal(session, event)
    if rendered:
        # seal() holds the task's lock and flushed this session's changes:
        # read the thread again under it, so a comment another writer
        # committed since this item was loaded is kept, never overwritten.
        session.refresh(item, attribute_names=["comments"], with_for_update=True)
        item.comments = [
            *board.comment_list(item.comments),
            {
                "author": body["author"],
                "text": rendered,
                "created_at": created_at or datetime.now(UTC).isoformat(),
                "event_id": event.id,
            },
        ]
    item.updated_at = datetime.now(UTC)
    session.add(item)
    return event


def record_by_id(
    engine: Any,
    item_id: int,
    kind: str,
    actor: str | None = None,
    **fields: Any,
) -> TaskEvent | None:
    """``record`` in a session of its own, for the task *item_id*; committed.

    *actor* defaults to the one behind the comment's ``author``. An unknown
    task records nothing.
    """
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if item is None:
            return None
        event = record(
            session,
            item,
            kind,
            actor or actor_for(fields.get("author")),
            **fields,
        )
        session.commit()
        return event


def for_task(session: Session, task_id: int) -> list[TaskEvent]:
    """A task's events, in the order they happened."""
    return list(
        session.exec(
            select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id)  # type: ignore[arg-type]
        ).all()
    )


def for_tasks(session: Session, task_ids: list[int]) -> dict[int, list[TaskEvent]]:
    """The events of several tasks in one query, by task, each in order."""
    grouped: dict[int, list[TaskEvent]] = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return grouped
    for event in session.exec(
        select(TaskEvent)
        .where(TaskEvent.task_id.in_(task_ids))  # type: ignore[union-attr]
        .order_by(TaskEvent.id)  # type: ignore[arg-type]
    ).all():
        grouped.setdefault(event.task_id, []).append(event)
    return grouped


def as_dict(event: TaskEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "task_id": event.task_id,
        "sprint_id": event.sprint_id,
        "kind": event.event_type,
        "event_type": event.event_type,
        "actor": event.actor,
        "run_id": event.run_id,
        "verification_id": event.verification_id,
        "task_status": event.task_status,
        "legacy": bool(event.legacy),
        "payload": event.payload or {},
        "seq": event.seq,
        "prev_hash": event.prev_hash,
        "chain_hash": event.chain_hash,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def verify_chain(session: Session, task_id: int) -> dict[str, Any]:
    """Walk a task's events and report where the chain breaks.

    ``status`` is ``empty``, ``intact`` or ``broken``; ``breaks`` lists
    ``gap``, ``prev_mismatch``, ``hash_mismatch``, ``key_missing`` and
    ``downgrade`` (an unsigned event after a signed one while a key is set) by
    sequence number, as ``audit_chain.verify_chain`` does for the audit rows.
    """
    rows = [e for e in for_task(session, task_id) if e.seq is not None]
    if not rows:
        return {"task_id": task_id, "status": "empty", "events": 0, "breaks": []}
    key = audit_signing._configured_key()
    breaks: list[dict[str, Any]] = []
    expected, previous = 1, None
    keyed_seen = False
    for row in rows:
        stored = row.chain_hash or ""
        keyed = stored.startswith(SIGNED_PREFIX)
        if key is not None and keyed_seen and not keyed:
            # Rewriting a signed stretch without the key leaves unsigned hashes.
            breaks.append({"seq": row.seq, "reason": "downgrade"})
        keyed_seen = keyed_seen or keyed
        if row.seq != expected:
            breaks.append({"seq": row.seq, "reason": "gap"})
        if (row.prev_hash or None) != previous:
            breaks.append({"seq": row.seq, "reason": "prev_mismatch"})
        if keyed and key is None:
            breaks.append({"seq": row.seq, "reason": "key_missing"})
        elif not hmac.compare_digest(stored, event_hash(row, key if keyed else None)):
            breaks.append({"seq": row.seq, "reason": "hash_mismatch"})
        expected, previous = row.seq + 1, row.chain_hash
    return {
        "task_id": task_id,
        "status": "broken" if breaks else "intact",
        "events": len(rows),
        "head": {"seq": rows[-1].seq, "chain_hash": rows[-1].chain_hash},
        "breaks": breaks,
    }


def escalated(
    session: Session, item: BacklogItem, message_id: int | None, reason: str
) -> TaskEvent:
    """The block handed to the ScrumMaster: an event; the message carries the text.

    The actor is whoever wrote the escalation message (a gate, the system or
    the reviewer's role).
    """
    from models import AgentMessage

    message = session.get(AgentMessage, message_id) if message_id else None
    actor = actor_for(message.from_name) if message is not None else SYSTEM
    if message is not None and actor not in (SYSTEM, GATE, PERSON):
        actor = message.from_role or actor
    return record(
        session,
        item,
        "escalated",
        actor,
        payload={"message_id": message_id, "to": "ScrumMaster", "reason": reason[:500]},
    )


def person_kind(status: str | None, comment: str | None) -> str:
    """The kind of a person's change on the board (``PATCH /v1/backlog``)."""
    if status == "done":
        return "approved_by_person"
    if comment and comment.strip().startswith(board.SEND_BACK_MARKER):
        return "sent_back"
    if comment and comment.strip().upper().startswith("NEEDS WORK:"):
        return "reviewed"
    return "moved" if status else "note"

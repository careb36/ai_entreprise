"""The sprint board: writing, reading and measuring a sprint's notes (issue #439).

A sprint's tasks leave each other short, typed notes (``models/board_note.py``;
the kinds, bounds and format are ``services/contracts/board_notes.py``). This
module records them and answers about them; it never builds a prompt, calls a
model or reads the evidence (the loop does, ``services/loop/worker/sprint_notes.py``).

- ``add`` writes a note and, in the same transaction, a ``board_note`` task
  event with its author: on the author task's chain for an agent's note, on
  the sprint's own chain for a person's. A note that names an earlier active
  note of the same sprint in ``supersedes`` marks it ``superseded``.
- ``mark_wrong`` is a person saying a note is wrong: its status changes, with
  who, when and why, and the change is an event. The note's text never does.
- ``rejected`` records the blocks of a delivery that were not notes, and why.
- ``contradiction_rework`` counts the rework requests of a sprint that say
  they contradict another task of the same sprint (the measure of the issue).

A note is a claim, never evidence: nothing here reads or writes an
observation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from models import BacklogItem, Sprint, SprintBoardNote, TaskEvent
from sqlmodel import Session, select

from services.contracts import board_notes
from services.tasks import board, task_events

PERSON = task_events.PERSON
EVENT_KIND = "board_note"
REJECTED_KIND = "board_note_rejected"
# The words a rework request uses when it says the work contradicts another
# task (deterministic; ``contradiction_rework``).
CONTRADICTION_WORDS = (
    "contradict",
    "conflict",
    "inconsistent",
    "incompatible",
    "mismatch",
    "disagree",
)
_TASK_REF = re.compile(r"(?:task\s*#?|#)(\d+)", re.IGNORECASE)
_NOTE_REF = re.compile(r"note\s*#?(\d+)", re.IGNORECASE)
MIN_TITLE_CHARS = 8


class BoardNoteError(ValueError):
    """A note that cannot be written; ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def as_dict(note: SprintBoardNote) -> dict[str, Any]:
    """The note as the API and the prompt read it: the claim's fields only."""
    links = note.links or {}
    return {
        "id": note.id,
        "sprint_id": note.sprint_id,
        "kind": note.kind,
        "text": note.text,
        "author_role": note.author_role,
        "author_name": note.author_name,
        "author_task_id": note.author_task_id,
        "run_id": note.run_id,
        "links": {kind: list(links.get(kind) or []) for kind in board_notes.LINK_KINDS},
        "supersedes_id": note.supersedes_id,
        "status": note.status,
        "status_by": note.status_by,
        "status_at": note.status_at.isoformat() if note.status_at else None,
        "status_reason": note.status_reason,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


def _record_event(
    session: Session,
    sprint: Sprint,
    item: BacklogItem | None,
    actor: str,
    payload: dict[str, Any],
    kind: str = EVENT_KIND,
    run_id: int | None = None,
) -> TaskEvent:
    """The event of a board write: on the task's chain, else the sprint's own."""
    if item is not None:
        return task_events.record(
            session,
            item,
            kind,
            actor,
            run_id=run_id,
            payload=payload,
            company_id=sprint.company_id,
        )
    event = TaskEvent(
        company_id=sprint.company_id,
        sprint_id=sprint.id,
        task_id=None,
        event_type=kind,
        actor=actor,
        run_id=run_id,
        payload=payload,
        created_at=_now(),
    )
    return task_events.seal(session, event)


def add(
    session: Session,
    sprint: Sprint,
    *,
    kind: str,
    text: str,
    author_role: str,
    author_name: str | None = None,
    item: BacklogItem | None = None,
    run_id: int | None = None,
    links: dict[str, Any] | None = None,
    supersedes_id: int | None = None,
) -> SprintBoardNote:
    """Write a note on *sprint*'s board and its event; flushed, not committed.

    *item* is the author task (none for a person's note). Raises
    ``BoardNoteError`` for a note that does not pass ``board_notes.check`` or
    supersedes a note that is not an active note of this sprint.
    """
    checked, reason = board_notes.check(kind, text, links)
    if checked is None:
        raise BoardNoteError(reason or "not a note")
    replaced: SprintBoardNote | None = None
    if supersedes_id is not None:
        replaced = session.get(SprintBoardNote, supersedes_id)
        if (
            replaced is None
            or replaced.sprint_id != sprint.id
            or replaced.status != board_notes.ACTIVE
        ):
            raise BoardNoteError(
                f"supersedes #{supersedes_id}: not an active note of this sprint"
            )
    actor = PERSON if item is None else (author_role or task_events.SYSTEM)
    note = SprintBoardNote(
        company_id=sprint.company_id,
        sprint_id=sprint.id,
        kind=checked["kind"],
        text=checked["text"],
        author_role=author_role if item is not None else PERSON,
        author_name=(author_name or "").strip()[:200] or None,
        author_task_id=item.id if item is not None else None,
        run_id=run_id,
        links=checked["links"],
        supersedes_id=supersedes_id,
        created_at=_now(),
    )
    session.add(note)
    session.flush()
    if replaced is not None:
        replaced.status = board_notes.SUPERSEDED
        replaced.status_by = f"note #{note.id}"
        replaced.status_at = _now()
        replaced.status_reason = None
        session.add(replaced)
    _record_event(
        session,
        sprint,
        item,
        actor,
        {
            "action": "added",
            "note_id": note.id,
            "kind": note.kind,
            "author_role": note.author_role,
            "author_name": note.author_name,
            "supersedes_id": supersedes_id,
            "links": note.links,
            # A claim: what the author said, never what was observed.
            "claim": True,
        },
        run_id=run_id,
    )
    return note


def mark_wrong(
    session: Session, note: SprintBoardNote, by: str, reason: str | None = None
) -> SprintBoardNote:
    """A person says *note* is wrong; flushed, not committed. A note already
    marked wrong is refused (409); a superseded one can still be marked."""
    if note.status == board_notes.MARKED_WRONG:
        raise BoardNoteError("The note is already marked wrong", 409)
    sprint = session.get(Sprint, note.sprint_id)
    if sprint is None:
        raise BoardNoteError("The note's sprint is gone", 404)
    previous = note.status
    note.status = board_notes.MARKED_WRONG
    note.status_by = (by or "").strip()[:200] or PERSON
    note.status_at = _now()
    note.status_reason = (reason or "").strip()[: board_notes.REASON_LIMIT] or None
    session.add(note)
    _record_event(
        session,
        sprint,
        None,
        PERSON,
        {
            "action": "marked_wrong",
            "note_id": note.id,
            "previous_status": previous,
            "by": note.status_by,
            "reason": note.status_reason,
        },
    )
    session.flush()
    return note


def rejected(
    session: Session,
    sprint: Sprint,
    item: BacklogItem,
    actor: str,
    blocks: list[dict[str, str]],
    run_id: int | None = None,
) -> TaskEvent | None:
    """Record why the blocks of a delivery were not notes; none recorded for none."""
    if not blocks:
        return None
    return _record_event(
        session,
        sprint,
        item,
        actor or task_events.SYSTEM,
        {"rejected": blocks[:20]},
        kind=REJECTED_KIND,
        run_id=run_id,
    )


def notes_of(
    session: Session, sprint_id: int, *, active_only: bool = False
) -> list[SprintBoardNote]:
    """A sprint's notes, newest first; only the active ones with *active_only*."""
    query = select(SprintBoardNote).where(SprintBoardNote.sprint_id == sprint_id)
    if active_only:
        query = query.where(SprintBoardNote.status == board_notes.ACTIVE)
    return list(
        session.exec(
            query.order_by(SprintBoardNote.id.desc())  # type: ignore[union-attr]
        ).all()
    )


def notes_by_sprint(
    session: Session, sprint_ids: list[int]
) -> dict[int, list[SprintBoardNote]]:
    """The notes of several sprints in one query, by sprint, newest first."""
    grouped: dict[int, list[SprintBoardNote]] = {}
    if not sprint_ids:
        return grouped
    for note in session.exec(
        select(SprintBoardNote)
        .where(SprintBoardNote.sprint_id.in_(sprint_ids))  # type: ignore[union-attr]
        .order_by(SprintBoardNote.id.desc())  # type: ignore[union-attr]
    ).all():
        grouped.setdefault(note.sprint_id, []).append(note)
    return grouped


def counts(notes: Iterable[SprintBoardNote]) -> dict[str, Any]:
    """How many notes, by status and by kind."""
    tally: dict[str, Any] = {
        "total": 0,
        **{status: 0 for status in board_notes.STATUSES},
        "by_kind": {kind: 0 for kind in board_notes.KINDS},
    }
    for note in notes:
        tally["total"] += 1
        tally[note.status] = tally.get(note.status, 0) + 1
        tally["by_kind"][note.kind] = tally["by_kind"].get(note.kind, 0) + 1
    return tally


def _mentions_other(
    text: str,
    own_id: int,
    tasks: dict[int, str],
    note_task: dict[int, int | None],
    files_by_task: dict[int, set[str]],
) -> bool:
    lowered = text.lower()
    # ``note #N`` names a note, not a task: read apart.
    for match in _TASK_REF.finditer(_NOTE_REF.sub(" ", text)):
        ref = int(match.group(1))
        if ref != own_id and ref in tasks:
            return True
    for match in _NOTE_REF.finditer(text):
        author = note_task.get(int(match.group(1)), own_id)
        if author != own_id:
            return True
    for task_id, title in tasks.items():
        if (
            task_id != own_id
            and len(title) >= MIN_TITLE_CHARS
            and title.lower() in lowered
        ):
            return True
    for task_id, files in files_by_task.items():
        if task_id != own_id and any(f.lower() in lowered for f in files):
            return True
    return False


def contradiction_rework(
    items: Iterable[tuple[int, str | None, Any]],
    notes: Iterable[SprintBoardNote] = (),
) -> int:
    """The rework requests of a sprint that say they contradict another task.

    *items* are the sprint's tasks as ``(id, title, comments)``. A request
    (``NEEDS WORK:``, ``board.rework_request_count``'s) counts when its text
    has one of ``CONTRADICTION_WORDS`` and names another task of the same
    sprint: ``task #N`` / ``#N``, its title (``MIN_TITLE_CHARS`` or more), a
    note of another task (``note #N``), or a file a note of another task
    links. Deterministic, read from the board; the method is in
    ``docs/design/SPRINT_BOARD.md``.
    """
    items = list(items)
    tasks = {item_id: str(title or "") for item_id, title, _ in items}
    note_task: dict[int, int | None] = {}
    files_by_task: dict[int, set[str]] = {}
    for note in notes:
        note_task[note.id] = note.author_task_id
        if note.author_task_id is not None:
            files_by_task.setdefault(note.author_task_id, set()).update(
                str(f) for f in (note.links or {}).get("files") or []
            )
    total = 0
    for item_id, _title, comments in items:
        for comment in board.verdict_comments(comments):
            if not board.is_rework_request(comment):
                continue
            text = str(comment.get("text", ""))
            if not any(word in text.lower() for word in CONTRADICTION_WORDS):
                continue
            if _mentions_other(text, item_id, tasks, note_task, files_by_task):
                total += 1
    return total


def sprint_metrics(session: Session, sprint_id: int) -> dict[str, Any]:
    """The board's numbers for one sprint: the notes' counts and the measure."""
    notes = notes_of(session, sprint_id)
    items = session.exec(
        select(BacklogItem.id, BacklogItem.title, BacklogItem.comments).where(
            BacklogItem.sprint_id == sprint_id
        )
    ).all()
    return {
        "notes": counts(notes),
        "rework": sum(board.rework_request_count(c) for _, _, c in items),
        "contradiction_rework": contradiction_rework(items, notes),
    }

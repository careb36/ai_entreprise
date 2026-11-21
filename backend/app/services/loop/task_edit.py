"""A person edits a task's title or description (issue #766).

A task the agents could not finish could only be sent back with a note:
its title and description, which the Developer, the Verifier's acceptance
and QA all read, stayed as the PO wrote them, so narrowing an over-broad
task meant planning a new sprint. ``edit`` changes them, and records the
change as the task's own event:

- only a Manager (the router checks), and only while no agent is working on
  the task: ``todo`` or ``blocked``. In progress or in review an agent or
  the reviewer is reading it; done, it is reopened first;
- the event (``edited``, by the person) keeps the text before and after,
  with the description's sha256 each side, and the reason; it is chained
  like every other event of the task, so what the task asked at any time is
  on its record. The comment it renders says what changed and why;
- the change is audited as ``backlog_item_edited``;
- with ``send_back`` the task also goes back to work, as the board's "Send
  back to work" does: its rework budget starts again with the new text.

The worker reads the title and description when it claims the task, so the
next attempt works from the edited text.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from models import BacklogItem, Sprint
from sqlmodel import Session

from services.loop import board_moves
from services.platform.agent_service import AgentService
from services.tasks import task_events
from services.tasks.board import SEND_BACK_MARKER

EDITABLE = frozenset({"todo", "blocked"})
TITLE_MAX = 200
DESCRIPTION_MAX = 10_000
REASON_MIN = 10
REASON_MAX = 500
EVENT_KIND = "edited"
AUDIT_ACTION = "backlog_item_edited"


class EditRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _sha256(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _changes(
    item: BacklogItem, title: str | None, description: str | None
) -> tuple[str, str, list[str]]:
    new_title = item.title if title is None else title.strip()
    new_description = (
        (item.description or "") if description is None else description.strip()
    )
    if not new_title:
        raise EditRefused(422, "The title cannot be empty")
    if len(new_title) > TITLE_MAX:
        raise EditRefused(422, f"The title is longer than {TITLE_MAX} characters")
    if len(new_description) > DESCRIPTION_MAX:
        raise EditRefused(
            422, f"The description is longer than {DESCRIPTION_MAX} characters"
        )
    changed = [
        name
        for name, before, after in (
            ("title", item.title, new_title),
            ("description", item.description or "", new_description),
        )
        if before != after
    ]
    if not changed:
        raise EditRefused(
            422, "Nothing to change: the title and description are the same"
        )
    return new_title, new_description, changed


def edit(
    session: Session,
    item: BacklogItem,
    sprint: Sprint | None,
    user: Any,
    *,
    title: str | None,
    description: str | None,
    reason: str,
    send_back: bool = False,
) -> dict[str, Any]:
    """Change *item*'s title or description; refuses with ``EditRefused``
    before writing anything. The caller commits."""
    if sprint is None:
        raise EditRefused(404, "Sprint not found")
    if sprint.archived_at is not None:
        raise EditRefused(409, "The sprint is archived: unarchive it to edit its tasks")
    if item.status not in EDITABLE:
        raise EditRefused(
            409,
            f"A task in {item.status} cannot be edited: an agent or a reviewer is "
            "using it, or it is done. Edit it once it is in To do or Blocked.",
        )
    reason = (reason or "").strip()
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise EditRefused(422, f"Say why in {REASON_MIN} to {REASON_MAX} characters")
    new_title, new_description, changed = _changes(item, title, description)
    before = {"title": item.title, "description": item.description or ""}
    item.title = new_title
    item.description = new_description
    event = task_events.record(
        session,
        item,
        EVENT_KIND,
        task_events.PERSON,
        text=f"Task edited by a person ({', '.join(changed)}): {reason}",
        payload={
            "changed": changed,
            "before": before,
            "after": {"title": new_title, "description": new_description},
            "description_sha256": {
                "before": _sha256(before["description"]),
                "after": _sha256(new_description),
            },
            "reason": reason,
        },
        created_at=str(datetime.now(UTC).replace(tzinfo=None)),
    )
    actor = getattr(user, "email", None) or "a person"
    AgentService().audit_interaction(
        session=session,
        company_id=sprint.company_id,
        interaction_id=f"sprint:{sprint.id}:task:{item.id}",
        agent_id=f"human:{actor}",
        action=AUDIT_ACTION,
        details={"company_id": str(sprint.company_id), "sprint_id": sprint.id},
        result={"backlog_item_id": item.id, "changed": changed, "event_id": event.id},
        decision_signature=None,
        cost_tokens=0,
        commit=False,  # with the edit, in the caller's commit
    )
    started: dict[str, Any] = {}
    if send_back:
        try:
            _event, started = board_moves.send_back(
                session,
                item,
                sprint,
                user,
                f"{SEND_BACK_MARKER} by a person: the task was edited — {reason}",
            )
        except board_moves.BoardMoveError as exc:
            raise EditRefused(exc.status, exc.detail) from exc
    return {"event_id": event.id, "changed": changed, **started}

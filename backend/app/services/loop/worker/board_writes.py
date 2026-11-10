"""The task worker's writes to the board, shared by its stages (ADR 0014).

Every transition of a backlog item goes through ``task_events.record_by_id``
(issue #324): ``_update_item_status`` records an event with the item's new
status and the comment rendering it, ``_append_item_comment`` an event that
leaves the status as it was. Moved out of ``task_worker``, which still
re-exports both; every stage calls them here.
"""

from typing import Any

from database import engine

from services.tasks import task_events


def _append_item_comment(
    item_id: int, text: str, author: str, kind: str = "note", **fields: Any
) -> None:
    """Record an event on a backlog item, rendered as a comment, without changing its status."""
    task_events.record_by_id(engine, item_id, kind, text=text, author=author, **fields)


def _update_item_status(
    item_id: int,
    status: str,
    comment_text: str | None = None,
    comment_author: str | None = None,
    kind: str | None = None,
    **fields: Any,
):
    """Record a transition of a backlog item: its event, its status and the comment rendering it."""
    text = comment_text if comment_text and comment_author else None
    task_events.record_by_id(
        engine,
        item_id,
        kind or ("note" if text else "moved"),
        status=status,
        text=text,
        author=comment_author if text else None,
        **fields,
    )

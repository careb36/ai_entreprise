"""The task worker's writes to the board, shared by its stages (ADR 0014).

Every transition of a backlog item goes through ``task_events.record_by_id``
(issue #324): ``_update_item_status`` records an event with the item's new
status and the comment rendering it, ``_append_item_comment`` an event that
leaves the status as it was. Moved out of ``task_worker``, which still
re-exports both; every stage calls them here.

A ``verified`` event names the Verifier's reading it renders: the run's
latest ``Verification``, as ``verification_id`` (the column the documentation
pack joins on, ``planning/project_docs/collect.py``) and as
``payload.verification`` (``{id, verdict}``), looked up here from the run, so
no caller has to pass it (#329 retrospective: none did).
"""

from typing import Any

from database import engine
from logging_config import logger
from models.agent_run import Verification
from sqlmodel import Session, select

from services.tasks import task_events


def _verification_fields(kind: str | None, fields: dict[str, Any]) -> dict[str, Any]:
    """*fields* with the run's latest verification added to a ``verified`` event."""
    run_id = fields.get("run_id")
    if kind != "verified" or run_id is None or fields.get("verification_id"):
        return fields
    try:
        with Session(engine) as session:
            verification = session.exec(
                select(Verification)
                .where(Verification.agent_run_id == run_id)
                .order_by(Verification.id.desc())
            ).first()
    except Exception as exc:  # noqa: BLE001 - The event is recorded without it.
        logger.warning("No verification read for run=%s: %s", run_id, exc)
        return fields
    if verification is None:
        return fields
    payload = dict(fields.get("payload") or {})
    payload.setdefault(
        "verification", {"id": verification.id, "verdict": verification.verdict}
    )
    return {**fields, "verification_id": verification.id, "payload": payload}


def _append_item_comment(
    item_id: int, text: str, author: str, kind: str = "note", **fields: Any
) -> None:
    """Record an event on a backlog item, rendered as a comment, without changing its status."""
    task_events.record_by_id(
        engine,
        item_id,
        kind,
        text=text,
        author=author,
        **_verification_fields(kind, fields),
    )


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
        **_verification_fields(kind, fields),
    )

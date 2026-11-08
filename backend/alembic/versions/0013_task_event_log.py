"""The task event log: actor, cause, status, chain; events backfilled from threads (issue #324).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-08 22:00:00 UTC

Adds to ``task_events`` what an event of a task's life carries (``actor``,
``run_id``, ``verification_id``, ``task_status``, ``legacy`` and the per-task
chain ``seq`` / ``prev_hash`` / ``chain_hash``), then reads every existing
task thread back into events: each comment whose kind it can tell becomes an
event marked ``legacy`` (written by the migration, not by the transition, and
left out of the chain), with the event's id put on the comment; the last one
carries the task's status, and on a done task the person's note that closed it
(after its last approval, as ``history_check`` reads it) is read as
``approved_by_person``. A thread
holding a comment it cannot tell gets one ``legacy`` event of kind
``legacy`` too, so the board keeps reading that task from its comments.

The rules below restate, frozen as of this revision, what
``services/task_events.flags_for`` reads from a comment; a later change to
the live rules does not change what this migration wrote.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_GATES = {"quality gate", "verifier"}
_RISK_HOLD = "High-risk delivery approved by QA, waiting for a person to accept it"
_ADVICE = "External assistant context used for this task:"
_CARRIED = ("Carried over to sprint", "Carried over from")
_SEND_BACK = "Sent back to work"
_ACCEPT = "High-risk delivery accepted by a person"
_EXPLICIT = (
    "manual triage",
    "pre-review quality gate failed",
    "qa quality gate failed",
    "qa review failed",
    "agent error:",
    "missing deliverable",
    "freeze after",
    "sleep-induced churn",
    _RISK_HOLD.lower(),
    "blocked: waits for task",
)
_RETRYABLE = (
    "qa review failed",
    "agent error:",
    "no deliverable found",
    "unfinished in-progress task returned to todo for redispatch",
)
# The System's notes this revision knows, by how they start.
_SYSTEM_KINDS = (
    (_RISK_HOLD, "risk_hold"),
    ("Blocked: waits for task", "blocked"),
    ("Released: task", "released"),
    ("Waits for task", "note"),
    ("Autopilot rework scheduled", "sent_back"),
    ("QA quality gate failed", "blocked"),
    ("QA review failed", "review_failed"),
    ("QA review aborted", "blocked"),
    ("Automatic retry", "retried"),
    ("Worker recovery", "recovered"),
    ("Autonomous recovery", "recovered"),
    ("Paused:", "paused"),
    ("Stopped:", "blocked"),
    ("Agent error", "blocked"),
    ("Delivered to", "verified"),
    ("Delivery ", "verified"),
    ("Queued delivery:", "verified"),
)


def _classify(author: str, text: str) -> tuple[str, str, dict] | None:
    """(kind, actor, flags) for one comment, or None when this revision cannot tell."""
    lowered_author = author.strip().lower()
    lowered = text.lower()
    upper = text.upper()
    system = lowered_author == "system"
    gate = lowered_author in _GATES
    person = lowered_author == "user"
    advice = (
        text.startswith(_CARRIED)
        or (system and text.startswith(_ADVICE))
        or (lowered_author == "architect" and upper.startswith("ARCHITECT_CONSULTED:"))
    )
    rework = upper.startswith("NEEDS WORK:")
    flags = {
        "verdict": None,
        "explicit_block": False,
        "needs_person": False,
        "retryable": False,
        "retry": False,
        "frozen": False,
    }
    if not advice:
        flags = {
            "verdict": "needs_work"
            if rework
            else (
                "approved"
                if upper.startswith("APPROVED:") and not (system or gate or person)
                else None
            ),
            "explicit_block": (system or gate or person)
            and any(t in lowered for t in _EXPLICIT),
            "needs_person": ((system or gate) and "manual triage" in lowered)
            or (system and "qa quality gate failed" in lowered)
            or (system and text.startswith(_RISK_HOLD))
            or rework,
            "retryable": system and any(p in lowered for p in _RETRYABLE),
            "retry": system and "retry" in lowered,
            "frozen": "sleep-induced churn" in lowered
            or lowered.startswith("freeze after"),
        }
    if text.startswith(_CARRIED):
        return "carried_over", "system" if system else "person", flags
    if system:
        if text.startswith(_ADVICE):
            return "advised", "system", flags
        for prefix, kind in _SYSTEM_KINDS:
            if text.startswith(prefix):
                return kind, "system", flags
        return None
    if gate:
        return ("reviewed" if rework else "blocked"), "gate", flags
    if person:
        if text.startswith(_SEND_BACK):
            return "sent_back", "person", flags
        if rework:
            return "reviewed", "person", flags
        if text.startswith(_ACCEPT):
            return "approved_by_person", "person", flags
        return "note", "person", flags
    if lowered_author == "architect" and upper.startswith("ARCHITECT_CONSULTED:"):
        return "advised", author.strip(), flags
    if upper.startswith(("APPROVED:", "NEEDS WORK:")):
        return "reviewed", author.strip() or "system", flags
    return "delivered", author.strip() or "system", flags


def _when(raw: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(raw or ""))
    except ValueError:
        return datetime.now(UTC).replace(tzinfo=None)
    return (
        parsed if parsed.tzinfo is None else parsed.astimezone(UTC).replace(tzinfo=None)
    )


def _comments(raw: object) -> list:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return []
    return raw if isinstance(raw, list) else []


def _backfill() -> None:
    bind = op.get_bind()
    items = sa.table(
        "backlog_items",
        sa.column("id", sa.Integer),
        sa.column("sprint_id", sa.Integer),
        sa.column("status", sa.String),
        sa.column("comments", sa.JSON),
    )
    sprints = sa.table(
        "sprints", sa.column("id", sa.Integer), sa.column("company_id", sa.Integer)
    )
    events = sa.table(
        "task_events",
        sa.column("id", sa.Integer),
        sa.column("company_id", sa.Integer),
        sa.column("sprint_id", sa.Integer),
        sa.column("task_id", sa.Integer),
        sa.column("event_type", sa.String),
        sa.column("payload", sa.JSON),
        sa.column("created_at", sa.DateTime),
        sa.column("actor", sa.String),
        sa.column("task_status", sa.String),
        sa.column("legacy", sa.Boolean),
    )
    already = {
        row[0]
        for row in bind.execute(
            sa.select(events.c.task_id).where(events.c.task_id.is_not(None))
        )
    }
    rows = bind.execute(
        sa.select(
            items.c.id,
            items.c.sprint_id,
            items.c.status,
            items.c.comments,
            sprints.c.company_id,
        )
        .select_from(items.join(sprints, sprints.c.id == items.c.sprint_id))
        .order_by(items.c.id)
    ).all()
    for item_id, sprint_id, status, raw, company_id in rows:
        comments = _comments(raw)
        if item_id in already or company_id is None or not comments:
            continue
        read: list[tuple[int, str, str, dict]] = []
        for index, comment in enumerate(comments):
            if not isinstance(comment, dict):
                continue
            text = str(comment.get("text") or "").strip()
            found = _classify(str(comment.get("author") or ""), text) if text else None
            if found is not None:
                read.append((index, *found))
        unread = sum(1 for c in comments if isinstance(c, dict)) - len(read)
        if status == "done":
            # Closed by a person when their note came after the last approval.
            approvals = [i for i, r in enumerate(read) if r[3]["verdict"] == "approved"]
            notes = [i for i, r in enumerate(read) if r[2] == "person"]
            if notes and (not approvals or notes[-1] > approvals[-1]):
                index, _kind, actor, flags = read[notes[-1]]
                read[notes[-1]] = (index, "approved_by_person", actor, flags)
        changed = list(comments)
        for position, (index, kind, actor, flags) in enumerate(read):
            comment = comments[index]
            event_id = bind.execute(
                events.insert()
                .values(
                    company_id=company_id,
                    sprint_id=sprint_id,
                    task_id=item_id,
                    event_type=kind,
                    payload={
                        "author": str(comment.get("author") or ""),
                        # The whole text: the comment keeps it, so must its event.
                        "text": str(comment.get("text") or "").strip(),
                        "backfilled": True,
                        **flags,
                    },
                    created_at=_when(comment.get("created_at")),
                    actor=actor,
                    # The last event read back carries the status the task had.
                    task_status=status if position == len(read) - 1 else None,
                    legacy=True,
                )
                .returning(events.c.id)
            ).scalar_one()
            changed[index] = {**comment, "event_id": event_id}
        if unread:
            bind.execute(
                events.insert().values(
                    company_id=company_id,
                    sprint_id=sprint_id,
                    task_id=item_id,
                    event_type="legacy",
                    payload={"unread_comments": unread},
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                    actor="system",
                    task_status=status,
                    legacy=True,
                )
            )
        bind.execute(
            items.update().where(items.c.id == item_id).values(comments=changed)
        )


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    with op.batch_alter_table("task_events", schema=None) as batch:
        batch.add_column(
            sa.Column("actor", text(), nullable=False, server_default="system")
        )
        batch.add_column(sa.Column("run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("verification_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("task_status", text(), nullable=True))
        batch.add_column(
            sa.Column("legacy", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("seq", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("prev_hash", text(), nullable=True))
        batch.add_column(sa.Column("chain_hash", text(), nullable=True))
        batch.create_foreign_key(
            "fk_task_events_run_id", "agent_runs", ["run_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_task_events_verification_id",
            "verifications",
            ["verification_id"],
            ["id"],
        )
        batch.create_index("ix_task_events_task_id", ["task_id"])
    _backfill()


def downgrade() -> None:
    with op.batch_alter_table("task_events", schema=None) as batch:
        batch.drop_index("ix_task_events_task_id")
        batch.drop_constraint("fk_task_events_verification_id", type_="foreignkey")
        batch.drop_constraint("fk_task_events_run_id", type_="foreignkey")
        for column in (
            "chain_hash",
            "prev_hash",
            "seq",
            "legacy",
            "task_status",
            "verification_id",
            "run_id",
            "actor",
        ):
            batch.drop_column(column)

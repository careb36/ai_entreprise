"""Task lifecycle events: the board is a projection of them (issue #324).

One row per transition of a task, written before (and with) the board comment
that renders it (``services/task_events.record``): what happened (``event_type``,
the kind), who did it (``actor``: ``system``, ``gate``, a role name or
``person``), the run or verification it is about when there is one, the
task's status after it and a payload (the rendered text and its author among
it). A task's events form a hash chain (``seq``, ``prev_hash``,
``chain_hash``) keyed like the audit chain. ``legacy`` marks the events the
migration read back from a thread written before events existed.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class TaskEvent(SQLModel, table=True):
    __tablename__ = "task_events"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    sprint_id: int = Field(foreign_key="sprints.id", index=True)
    task_id: int | None = Field(
        default=None, foreign_key="backlog_items.id", index=True
    )
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str = "system"
    run_id: int | None = Field(default=None, foreign_key="agent_runs.id")
    verification_id: int | None = Field(default=None, foreign_key="verifications.id")
    task_status: str | None = None
    legacy: bool = False
    seq: int | None = None
    prev_hash: str | None = None
    chain_hash: str | None = None

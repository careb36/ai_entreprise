"""A company's routine: a task that runs on a schedule (issue #774).

``schedule`` is ``services/contracts/routine_schedule``'s shape;
``next_run_at`` (naive UTC) is when it next runs, recomputed from the schedule
after each occurrence. Each run opens a small sprint with the routine's task
(``last_sprint_id``), which the worker runs like any other: run window,
budget, Verifier and QA. ``services/loop/routines.py`` says what happens when
the previous run is still open. Every change and every run is audited.

The design follows Paperclip's routines (MIT: ``packages/db/src/schema/
routines.ts``, ``server/src/services/routines.ts``); no code taken.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


class Routine(SQLModel, table=True):
    __tablename__ = "routines"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    title: str
    description: str = Field(default="", sa_column=Column(Text, nullable=False))
    # The hired role that gets the task.
    assignee: str
    schedule: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    next_run_at: datetime | None = Field(default=None, index=True)
    last_run_at: datetime | None = None
    last_sprint_id: int | None = Field(default=None, foreign_key="sprints.id")
    created_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

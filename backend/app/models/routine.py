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

from sqlalchemy import JSON, Column, Text, UniqueConstraint
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
    # Its declared inputs (``services/contracts/routine_variables``).
    variables: list[Any] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    # The number of its latest revision (``RoutineRevision``).
    revision: int = 1
    enabled: bool = True
    next_run_at: datetime | None = Field(default=None, index=True)
    last_run_at: datetime | None = None
    last_sprint_id: int | None = Field(default=None, foreign_key="sprints.id")
    created_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RoutineRevision(SQLModel, table=True):
    """One version of a routine: what it was after a change, whole (issue
    #774, from Paperclip's ``routine_revisions``). A run records the number of
    the revision it ran; a restore writes a new revision with
    ``restored_from``. Never changed once written."""

    __tablename__ = "routine_revisions"
    __table_args__ = (UniqueConstraint("routine_id", "number"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    routine_id: int = Field(index=True)
    number: int
    snapshot: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    change_summary: str | None = None
    restored_from: int | None = None
    created_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

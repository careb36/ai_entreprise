"""A sprint's progress as it stood when it closed (issue #531).

One row per close of a sprint: the time series built from the sprint's task
events (``services/tasks/progress.py``) and the retrospective's numbers, with
the sha256 the backend computed over both and the audit row that seals that
digest into the company's audit chain. A row is never edited: a sprint a
person reopened and that closes again gets a new row with the next
``close_number``; the first close keeps its own.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class SprintProgressSnapshot(SQLModel, table=True):
    __tablename__ = "sprint_progress_snapshots"
    __table_args__ = (UniqueConstraint("sprint_id", "close_number"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    sprint_id: int = Field(foreign_key="sprints.id", index=True)
    # 1 for the sprint's first close, 2 after a reopening, and so on.
    close_number: int
    series: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    retrospective: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # ``sha256:<hex>`` over the canonical bytes of the snapshot, computed by
    # the backend (``services/tasks/progress_snapshot.digest_of``).
    digest: str
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )

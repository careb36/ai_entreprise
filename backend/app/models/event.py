"""Task lifecycle events: the board is a projection of them."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class TaskEvent(SQLModel, table=True):
    __tablename__ = "task_events"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    sprint_id: int = Field(foreign_key="sprints.id", index=True)
    task_id: int | None = Field(default=None, foreign_key="backlog_items.id")
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

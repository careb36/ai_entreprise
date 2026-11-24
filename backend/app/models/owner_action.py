"""A run of an owner action (``docs/design/owner-actions.md``).

``OwnerActionRun`` — one start of a catalogue action
(``services/management/owner_actions.py``) by a company's Owner: the
validated parameters, who started it, its status (``running``,
``succeeded``, ``failed``, ``cancelled``), the backend's clock at start and
finish, and ``observations``, what the platform created or measured during
the run (ids its own API returned, the daemon's replies, the tokens it
recorded). Nothing a producer reported is stored here. Start, cancel and
finish are also rows of the company's audit chain.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Index
from sqlmodel import Column, Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OwnerActionRun(SQLModel, table=True):
    __tablename__ = "owner_action_runs"
    __table_args__ = (
        Index("ix_owner_action_runs_action_status", "action", "status"),
        Index("ix_owner_action_runs_company_id", "company_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    action: str
    status: str = "running"
    params: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    started_by_user_id: int = Field(foreign_key="users.id")
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    cancel_requested_by_user_id: int | None = Field(
        default=None, foreign_key="users.id"
    )
    exit_status: int | None = None
    reason: str | None = None
    observations: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

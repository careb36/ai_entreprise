"""Saved views of a company's tasks, and each person's display preferences (issue #535).

A ``SavedView`` keeps a filter (``services/contracts/task_filters``, a JSON
tree over an allowlist of fields) with how its tasks are shown: grouped by,
ordered by, the layout (``board`` or ``list``) and the visible columns. Its
``access`` is ``private`` (only its author lists it) or ``company`` (every
member does); a ``locked`` view is changed only by a Manager or the Owner.

What one person changes for themselves (collapsed groups, column widths, the
density) is a ``SavedViewPreference`` of their own, so it never rewrites a
view the company shares. The design follows Plane's ``IssueView`` and its
per-user display properties (AGPL-3.0-only, design only: no code taken).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class SavedView(SQLModel, table=True):
    __tablename__ = "saved_views"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    name: str
    # The parsed filter tree; ``{}`` for every task.
    filters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    group_by: str | None = None
    order_by: str | None = None
    layout: str = "list"
    visible_columns: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    access: str = "private"
    locked: bool = False
    # Its author: a member's user id (none in a local demo without sign-in)
    # and the name shown.
    owner_user_id: int | None = Field(default=None, foreign_key="users.id", index=True)
    owner_name: str
    updated_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SavedViewPreference(SQLModel, table=True):
    __tablename__ = "saved_view_preferences"
    __table_args__ = (
        UniqueConstraint("view_id", "user_id", name="uq_saved_view_preferences"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    view_id: int = Field(foreign_key="saved_views.id", index=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    # {"collapsed_groups": [...], "column_widths": {...}, "density": "compact"}
    display: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=_now)

"""
SQLModel ORM model for the ``bug_reports`` table.

Stores structured support tickets submitted by users via the Bug Reporter
tab or the ``POST /v1/support/report_bug`` API endpoint.  Ticket numbers
follow the ``FAB-XXXXX`` format (zero-padded 5-digit integer).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Column, Field, SQLModel


class BugReport(SQLModel, table=True):
    """
    A single support ticket capturing a defect, feature request, or incident.

    Lifecycle (``status`` transitions):
        open → in_progress → waiting → resolved → closed
    """

    __tablename__ = "bug_reports"

    id: int | None = Field(default=None, primary_key=True)
    ticket_num: str = Field(unique=True)  # Human-readable ID, e.g. "FAB-00042"
    title: str
    description: str
    steps: str | None = None  # Step-by-step reproduction instructions
    expected: str | None = None  # What the user expected to happen
    actual: str | None = None  # What actually happened (the bug)
    severity: str = "medium"  # "low" | "medium" | "high" | "critical"
    category: str = "bug"  # "bug" | "feature" | "question" | "incident"
    status: str = "open"  # "open" | "in_progress" | "waiting" | "resolved" | "closed"
    reporter_email: str | None = None
    assigned_to: str | None = None
    resolution: str | None = None  # Free-text explanation of how the issue was resolved
    browser_info: Any | None = Field(
        default=None, sa_column=Column(JSON)
    )  # User-agent, viewport, etc.
    created_at: datetime | None = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = (
        None  # Set when status transitions to "resolved" or "closed"
    )

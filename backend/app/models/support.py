"""
SQLModel ORM model for the ``bug_reports`` table.

Stores structured support tickets submitted by users via the Bug Reporter
tab or the ``POST /v1/support/report_bug`` API endpoint.  Ticket numbers
follow the ``FAB-XXXXX`` format (zero-padded 5-digit integer).
"""

from typing import Optional, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class BugReport(SQLModel, table=True):
    """
    A single support ticket capturing a defect, feature request, or incident.

    Lifecycle (``status`` transitions):
        open → in_progress → waiting → resolved → closed
    """

    __tablename__ = "bug_reports"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_num: str = Field(unique=True)  # Human-readable ID, e.g. "FAB-00042"
    title: str
    description: str
    steps: Optional[str] = None       # Step-by-step reproduction instructions
    expected: Optional[str] = None    # What the user expected to happen
    actual: Optional[str] = None      # What actually happened (the bug)
    severity: str = "medium"          # "low" | "medium" | "high" | "critical"
    category: str = "bug"             # "bug" | "feature" | "question" | "incident"
    status: str = "open"              # "open" | "in_progress" | "waiting" | "resolved" | "closed"
    reporter_email: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution: Optional[str] = None  # Free-text explanation of how the issue was resolved
    browser_info: Optional[Any] = Field(default=None, sa_column=Column(JSON))  # User-agent, viewport, etc.
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None  # Set when status transitions to "resolved" or "closed"

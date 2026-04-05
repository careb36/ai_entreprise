from typing import Optional, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class BugReport(SQLModel, table=True):
    __tablename__ = "bug_reports"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_num: str = Field(unique=True)
    title: str
    description: str
    steps: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    severity: str = "medium"           # low | medium | high | critical
    category: str = "bug"              # bug | feature | question | incident
    status: str = "open"               # open | in_progress | waiting | resolved | closed
    reporter_email: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution: Optional[str] = None
    browser_info: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None

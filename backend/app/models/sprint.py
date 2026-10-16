"""
SQLModel ORM models for sprint management and the transactional outbox.

Tables:
- ``Sprint``       — a time-boxed iteration for a company.
- ``BacklogItem``  — a single task on the sprint board (Kanban card).
- ``OutboxEvent``  — transactional outbox rows published to NATS by the background worker.
"""

from typing import Optional, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class Sprint(SQLModel, table=True):
    """A scope-bounded work iteration owned by a company."""

    __tablename__ = "sprints"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    business_unit_id: Optional[int] = Field(default=None, foreign_key="business_units.id")
    sprint_name: str
    start_date: Optional[str] = None  # ISO-8601 date string
    scope_size: str = "M"             # "S" (≤4 items) | "M" (≤8) | "L" (≤12)
    duration_days: Optional[int] = None  # deprecated — kept for backwards compat
    status: str = "active"            # "active" | "completed" | "cancelled" | "needs_split"
    goal: Optional[str] = None        # Sprint goal statement for the team


class BacklogItem(SQLModel, table=True):
    """
    A single task / user story on the sprint board.

    ``status`` drives the Kanban column placement.
    ``comments`` stores a JSON list of ``{author, text, created_at}`` objects
    appended by agents and human reviewers over the item's lifecycle.
    """

    __tablename__ = "backlog_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    sprint_id: int = Field(foreign_key="sprints.id")
    title: str
    description: str = ""
    status: str = "todo"              # "todo" | "in_progress" | "in_review" | "done" | "blocked"
    priority: str = "medium"          # "low" | "medium" | "high" | "critical"
    assignee: Optional[str] = None    # Agent role or human name
    comments: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class OutboxEvent(SQLModel, table=True):
    """
    Transactional outbox entry for reliable NATS message delivery.

    The ``process_outbox`` background worker polls this table for pending
    rows, publishes them to NATS, and marks them completed.  Rows that
    exceed 5 retries are marked ``"failed"`` and require manual inspection.

    Using ``FOR UPDATE SKIP LOCKED`` in the worker query prevents duplicate
    processing when multiple worker replicas run concurrently (e.g. in K8s).
    """

    __tablename__ = "outbox_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    aggregate_type: str    # Domain entity type (e.g. "backlog_item", "sprint")
    aggregate_id: str      # PK of the domain entity that triggered this event
    event_type: str        # Action that produced the event (e.g. "nats_publish")
    payload: Optional[Any] = Field(default=None, sa_column=Column(JSON))  # {"subject": "...", "message": {...}}
    status: str = "pending"          # "pending" | "completed" | "failed"
    retry_count: int = 0             # Incremented on each failed delivery attempt
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None  # Set when status transitions to completed/failed

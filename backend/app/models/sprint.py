"""
SQLModel ORM models for sprint management and the transactional outbox.

Tables:
- ``Sprint``       — a time-boxed iteration for a company.
- ``BacklogItem``  — a single task on the sprint board (Kanban card).
- ``OutboxEvent``  — transactional outbox rows published to NATS by the background worker.
- ``TaskQueue``    — human-approval queue for AI-generated tasks (Human-in-the-Loop).
"""

from typing import Optional, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class Sprint(SQLModel, table=True):
    """A time-boxed work iteration owned by a company."""

    __tablename__ = "sprints"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    sprint_name: str
    start_date: Optional[str] = None  # ISO-8601 date string
    duration_days: int = 14
    status: str = "active"            # "active" | "completed" | "cancelled"
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
    payload: Optional[str] = None   # JSON string: {"subject": "...", "message": {...}}
    status: str = "pending"          # "pending" | "completed" | "failed"
    retry_count: int = 0             # Incremented on each failed delivery attempt
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None  # Set when status transitions to completed/failed


class TaskQueue(SQLModel, table=True):
    """
    Human-in-the-Loop approval queue for AI-generated tasks.

    When ``autonomy_level < 3``, generated tasks are inserted here and
    paused until a human manager approves or rejects them via
    ``POST /v1/approve_task``.
    """

    __tablename__ = "task_queue"

    id: Optional[int] = Field(default=None, primary_key=True)
    interaction_id: Optional[str] = None      # Links back to the originating workflow run
    approval_status: Optional[str] = None     # "approved" | "rejected" | None (pending)
    approved_by: Optional[str] = None         # Name of the human who reviewed the task
    status: str = "pending"                   # "pending" | "in_progress" | "completed" | "failed"

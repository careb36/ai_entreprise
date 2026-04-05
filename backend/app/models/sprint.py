from typing import Optional, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class Sprint(SQLModel, table=True):
    __tablename__ = "sprints"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    sprint_name: str
    start_date: Optional[str] = None
    duration_days: int = 14
    status: str = "active"
    goal: Optional[str] = None


class BacklogItem(SQLModel, table=True):
    __tablename__ = "backlog_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    sprint_id: int = Field(foreign_key="sprints.id")
    title: str
    description: str = ""
    status: str = "todo"
    priority: str = "medium"
    assignee: Optional[str] = None
    comments: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class OutboxEvent(SQLModel, table=True):
    __tablename__ = "outbox_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: Optional[str] = None  # JSON string
    status: str = "pending"
    retry_count: int = 0
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None


class TaskQueue(SQLModel, table=True):
    __tablename__ = "task_queue"

    id: Optional[int] = Field(default=None, primary_key=True)
    interaction_id: Optional[str] = None
    approval_status: Optional[str] = None
    approved_by: Optional[str] = None
    status: str = "pending"

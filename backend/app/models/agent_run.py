from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field, SQLModel


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("company_id", "external_ref", "version"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    external_ref: str
    version: int = 1
    producer: str
    model: Optional[str] = None
    agent_role: Optional[str] = None
    subject: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    claims: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    artifact_text: Optional[str] = None
    artifact_digest: str
    artifact_size: int
    status: str = "ingested"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    audit_log_id: Optional[int] = Field(default=None, foreign_key="audit_log.id")

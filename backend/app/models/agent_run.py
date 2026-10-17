from datetime import datetime
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field, SQLModel


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("company_id", "external_ref", "version"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    external_ref: str
    version: int = 1
    producer: str
    model: str | None = None
    agent_role: str | None = None
    subject: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    claims: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    artifact_text: str | None = None
    artifact_digest: str
    artifact_size: int
    status: str = "ingested"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")


class Verification(SQLModel, table=True):
    __tablename__ = "verifications"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    agent_run_id: int = Field(foreign_key="agent_runs.id")
    verdict: str
    checks: list | None = Field(default=None, sa_column=Column(JSON))
    evidence: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")


class AgentRunApproval(SQLModel, table=True):
    __tablename__ = "agent_run_approvals"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    agent_run_id: int = Field(foreign_key="agent_runs.id")
    approver_name: str
    decision: str
    feedback: str | None = None
    rejection_category: str | None = None
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")

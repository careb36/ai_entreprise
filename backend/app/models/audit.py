"""
SQLModel ORM model for the ``audit_log`` table.

Audit logs provide an immutable, append-only record of every agent decision,
including the raw prompt context, the LLM output, and the token cost.
They are written by ``AgentService.audit_interaction()`` to both the
filesystem (JSONL) and this table for queryable history.
"""

from typing import Optional, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class AuditLog(SQLModel, table=True):
    """
    Immutable record of a single agent action within a LangGraph workflow run.

    Each row is created once and never updated; updates would compromise
    the non-repudiation guarantees of the audit trail.
    """

    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="companies.id", index=True)

    # Correlates all audit rows that belong to the same workflow execution.
    interaction_id: Optional[str] = None

    # Identifier of the AI agent that performed the action (e.g. "developer_1").
    agent_id: Optional[str] = None

    # Short label describing what was done (e.g. "generate_code", "burn_engram").
    action: str

    # Free-form JSON context passed to the action (prompt, config, etc.).
    details: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # JSON-serialised outcome returned by the action.
    result: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Server-computed HMAC-SHA256 or explicit unsigned digest; legacy rows may be null.
    decision_signature: Optional[str] = None

    # Number of LLM tokens consumed; used for quota accounting per business unit.
    cost_tokens: int = 0

    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

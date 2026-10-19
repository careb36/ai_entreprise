"""
SQLModel ORM model for the ``audit_log`` table.

Audit logs provide an immutable, append-only record of every agent decision,
including the raw prompt context, the LLM output, and the token cost.
They are written by ``AgentService.audit_interaction()`` to both the
filesystem (JSONL) and this table for queryable history.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Column, Field, SQLModel


class AuditLog(SQLModel, table=True):
    """
    Immutable record of a single agent action within a LangGraph workflow run.

    Each row is created once and never updated; updates would compromise
    the non-repudiation guarantees of the audit trail.
    """

    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int | None = Field(default=None, foreign_key="companies.id", index=True)

    # Correlates all audit rows that belong to the same workflow execution.
    interaction_id: str | None = None

    # Identifier of the AI agent that performed the action (e.g. "developer_1").
    agent_id: str | None = None

    # Short label describing what was done (e.g. "generate_code", "burn_engram").
    action: str

    # Free-form JSON context passed to the action (prompt, config, etc.).
    details: Any | None = Field(default=None, sa_column=Column(JSON))

    # JSON-serialised outcome returned by the action.
    result: Any | None = Field(default=None, sa_column=Column(JSON))

    # Server-computed HMAC-SHA256 or explicit unsigned digest; legacy rows may be null.
    decision_signature: str | None = None

    # Number of LLM tokens consumed; used for quota accounting per business unit.
    cost_tokens: int = 0

    created_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))

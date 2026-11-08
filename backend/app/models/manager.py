"""The Manager agent's policy and decisions (issue #331).

``ManagerPolicy`` is what a company's Manager allows the Manager agent to
decide alone: a list of named rules (``services/manager_policy.py``) and the
size limits they read. Only a person writes it; the agent never does.

``ManagerDecision`` is one inbox item the agent looked at: its suggestion
(``suggested_*``, a claim, empty when its answer was unusable) and, when the
policy allowed it, the decision it took alone (``decided_by``, ``rule``,
``outcome``), kept apart from what the client decides. ``overridden_*`` is
the client taking that decision back. ``item_state`` names the situation the
agent looked at, so one situation gets one row even across replicas (the
unique constraint), and a new situation of the same item gets a new one.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ManagerPolicy(SQLModel, table=True):
    __tablename__ = "manager_policies"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True, unique=True)
    # The rule names the agent may apply alone; empty: it only suggests.
    rules: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    # Size limits a rule reads: files a delivery wrote or declared, and
    # acceptance criteria a task covers.
    max_files: int = 10
    max_criteria: int = 5
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class ManagerDecision(SQLModel, table=True):
    __tablename__ = "manager_decisions"
    __table_args__ = (
        UniqueConstraint("company_id", "item_kind", "item_id", "item_state"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # task, procedure or run; item_id is the backlog item, procedure or run.
    item_kind: str
    item_id: int
    item_state: str
    # The category the item belongs to, decided by the platform, not the model.
    category: str
    # What the agent was shown, for the record (counts, tier, verdict).
    evidence: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    suggested_decision: str | None = None
    suggested_reason: str | None = None
    suggested_by: str = "Manager agent"
    suggested_at: datetime = Field(default_factory=_utcnow)
    # What became of the suggestion: pending (the policy not applied yet; a
    # replica that stopped leaves it to be decided again), done, not_allowed,
    # stale (the situation changed before acting) or unusable.
    action_state: str | None = None
    # Set only when the policy let the agent decide alone.
    decided_by: str | None = None
    rule: str | None = None
    outcome: str | None = None
    decided_at: datetime | None = None
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")
    overridden_by: str | None = None
    overridden_at: datetime | None = None
    override_audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")

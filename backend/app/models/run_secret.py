"""Secrets that live only for one task run (issue #357)."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class RunSecret(SQLModel, table=True):
    __tablename__ = "run_secrets"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    task_id: int = Field(foreign_key="backlog_items.id", index=True)
    name: str
    encrypted_value: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

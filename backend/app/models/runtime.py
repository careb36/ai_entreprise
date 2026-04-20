"""Runtime persistence models for lightweight assistant state."""

from typing import Optional
from datetime import datetime

from sqlmodel import SQLModel, Field


class AssistantRuntimeState(SQLModel, table=True):
    """Persistent per-company runtime state for external console assistants."""

    __tablename__ = "assistant_runtime_state"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    assistant_key: str = Field(index=True)
    cooldown_until: Optional[datetime] = None
    last_error: Optional[str] = None
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
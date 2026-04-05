from typing import Optional, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    interaction_id: Optional[str] = None
    agent_id: Optional[str] = None
    action: str
    details: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    result: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    decision_signature: Optional[str] = None
    cost_tokens: int = 0
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

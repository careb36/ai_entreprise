from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class AIAuditLog(Base):
    __tablename__ = 'ai_audit_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    agent_id = Column(String, nullable=False)
    
    # Resilience tracking metrics
    llm_timeout_duration_ms = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    self_correction_iterations = Column(Integer, default=0, nullable=False)
    
    # Claims vs Observations (STD-AUD-1)
    claims = Column(JSON, nullable=False, default=dict)
    observations = Column(JSON, nullable=False, default=dict)
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, JSON, Float
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class AgentAuditLog(Base):
    """
    Core audit logging model for AI agent executions.
    Ensures immutable, audit-proof records of autonomous actions.
    """
    __tablename__ = "agent_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String, index=True, nullable=False)
    task_id = Column(String, index=True, nullable=False)
    
    # STD-AUD-1: Claims (what the agent says it did) and Observations (what the system saw) 
    # must never share a field to maintain evidentiary integrity.
    agent_claims = Column(JSON, nullable=False, default=dict)
    system_observations = Column(JSON, nullable=False, default=dict)
    
    # Enhanced tracking for Agent Exceptions (Timeout & Resiliency retro analysis)
    timeout_duration_seconds = Column(Float, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    self_correction_iterations = Column(Integer, nullable=False, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

from datetime import datetime
from typing import Dict, Any
from sqlalchemy import Column, Integer, String, DateTime, Float, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class AgentAuditLog(Base):
    """
    Audit log for autonomous agent executions.
    Ensures dependable, audit-proof records for AI-generated code.
    """
    __tablename__ = "agent_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    agent_id = Column(String, index=True, nullable=False)
    task_id = Column(String, index=True, nullable=False)
    
    # STD-AUD-1: Claims and observations never share a field
    claimed_actions = Column(JSON, nullable=True, default=dict)
    observed_actions = Column(JSON, nullable=True, default=dict)
    
    # Exception & Resilience Metrics
    timeout_duration_ms = Column(Float, nullable=True, default=0.0)
    retry_count = Column(Integer, nullable=False, default=0)
    self_correction_iterations = Column(Integer, nullable=False, default=0)
    
    # Execution Status
    status = Column(String, nullable=False)
    error_message = Column(String, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for ACC-1 compliant log aggregation."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "claimed_actions": self.claimed_actions,
            "observed_actions": self.observed_actions,
            "timeout_duration_ms": self.timeout_duration_ms,
            "retry_count": self.retry_count,
            "self_correction_iterations": self.self_correction_iterations,
            "status": self.status,
            "error_message": self.error_message
        }

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class AgentExecutionAudit(Base):
    """
    Audit log for AI agent execution, tracking both agent claims and system observations
    to ensure evidentiary integrity.
    """
    __tablename__ = 'agent_execution_audit'

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String, nullable=False, index=True)
    task_id = Column(String, nullable=False, index=True)
    
    # STD-AUD-1: Claims and observations never share a field.
    # Claims (What the agent asserts it has done)
    claimed_files_written = Column(JSON, default=list, nullable=False)
    claimed_tests_run = Column(JSON, default=list, nullable=False)
    
    # Observations (What the system verifiably recorded)
    observed_llm_timeout_duration_ms = Column(Float, default=0.0, nullable=False)
    observed_retry_count = Column(Integer, default=0, nullable=False)
    observed_self_correction_iterations = Column(Integer, default=0, nullable=False)
    observed_files_written = Column(JSON, default=list, nullable=False)
    observed_tests_run = Column(JSON, default=list, nullable=False)
    
    # Execution metadata
    status = Column(String, nullable=False, index=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "claims": {
                "files_written": self.claimed_files_written,
                "tests_run": self.claimed_tests_run,
            },
            "observations": {
                "llm_timeout_duration_ms": self.observed_llm_timeout_duration_ms,
                "retry_count": self.observed_retry_count,
                "self_correction_iterations": self.observed_self_correction_iterations,
                "files_written": self.observed_files_written,
                "tests_run": self.observed_tests_run,
            },
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

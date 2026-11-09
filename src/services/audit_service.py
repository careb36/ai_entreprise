from sqlalchemy.orm import Session
from src.models.audit import AIAuditLog
from typing import Dict, Any, Optional

class AuditService:
    def __init__(self, session: Session):
        self.session = session

    def log_agent_execution(
        self,
        event_type: str,
        agent_id: str,
        claims: Dict[str, Any],
        observations: Dict[str, Any],
        llm_timeout_duration_ms: Optional[int] = None,
        retry_count: int = 0,
        self_correction_iterations: int = 0
    ) -> AIAuditLog:
        """
        Log an AI agent execution event with resilience metrics.
        Claims and observations are separated per STD-AUD-1.
        """
        log_entry = AIAuditLog(
            event_type=event_type,
            agent_id=agent_id,
            claims=claims,
            observations=observations,
            llm_timeout_duration_ms=llm_timeout_duration_ms,
            retry_count=retry_count,
            self_correction_iterations=self_correction_iterations
        )
        self.session.add(log_entry)
        self.session.commit()
        self.session.refresh(log_entry)
        return log_entry

from sqlalchemy.orm import Session
from src.models.audit import AgentExecutionAudit

class AuditService:
    def __init__(self, db_session: Session):
        self.db = db_session

    def log_execution(
        self, 
        agent_id: str, 
        task_id: str, 
        claimed_files: list,
        claimed_tests: list,
        observed_timeout_ms: float, 
        observed_retries: int, 
        observed_iterations: int,
        observed_files: list,
        observed_tests: list,
        status: str,
        error_message: str = None
    ) -> AgentExecutionAudit:
        """
        Persists an execution audit record separating claims from observations.
        """
        audit_record = AgentExecutionAudit(
            agent_id=agent_id,
            task_id=task_id,
            claimed_files_written=claimed_files,
            claimed_tests_run=claimed_tests,
            observed_llm_timeout_duration_ms=observed_timeout_ms,
            observed_retry_count=observed_retries,
            observed_self_correction_iterations=observed_iterations,
            observed_files_written=observed_files,
            observed_tests_run=observed_tests,
            status=status,
            error_message=error_message
        )
        self.db.add(audit_record)
        self.db.commit()
        self.db.refresh(audit_record)
        return audit_record

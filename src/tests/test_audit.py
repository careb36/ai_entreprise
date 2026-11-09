import datetime
from src.models.audit import AIAuditLog

def test_audit_log_creation_and_exception_tracking(db_session):
    """
    Verifies that the audit log correctly captures LLM timeout durations,
    retry counts, and self-correction iterations.
    """
    log = AIAuditLog(
        execution_id="exec-12345",
        agent_claims={"files_written": ["main.py", "utils.py"]},
        system_observations={"tests_passed": 4, "cpu_time_ms": 120},
        llm_timeout_duration_ms=5004.5,
        retry_count=3,
        self_correction_iterations=2
    )
    db_session.add(log)
    db_session.commit()
    
    retrieved = db_session.query(AIAuditLog).filter_by(execution_id="exec-12345").first()
    
    assert retrieved is not None
    assert retrieved.llm_timeout_duration_ms == 5004.5
    assert retrieved.retry_count == 3
    assert retrieved.self_correction_iterations == 2

def test_std_aud_1_claims_and_observations_separated(db_session):
    """
    STD-AUD-1: Claims and observations never share a field.
    Ensures that agent claims and system observations are strictly separated in the schema.
    """
    log = AIAuditLog(
        execution_id="exec-67890",
        agent_claims={"status": "success", "action": "refactor"},
        system_observations={"status": "timeout", "action_verified": False}
    )
    db_session.add(log)
    db_session.commit()
    
    retrieved = db_session.query(AIAuditLog).filter_by(execution_id="exec-67890").first()
    
    # Verify separation of fields
    assert retrieved.agent_claims["status"] == "success"
    assert retrieved.system_observations["status"] == "timeout"
    assert retrieved.agent_claims != retrieved.system_observations

def test_audit_log_defaults(db_session):
    """
    Verifies that default values for exception tracking fields are correctly applied.
    """
    log = AIAuditLog(
        execution_id="exec-default"
    )
    db_session.add(log)
    db_session.commit()
    
    retrieved = db_session.query(AIAuditLog).filter_by(execution_id="exec-default").first()
    
    assert retrieved.llm_timeout_duration_ms == 0.0
    assert retrieved.retry_count == 0
    assert retrieved.self_correction_iterations == 0
    assert isinstance(retrieved.agent_claims, dict)
    assert isinstance(retrieved.system_observations, dict)
    assert isinstance(retrieved.timestamp, datetime.datetime)

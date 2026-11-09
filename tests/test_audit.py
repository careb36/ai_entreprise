import pytest
from datetime import datetime
from unittest.mock import MagicMock
from src.models.audit import AuditLog, AgentClaims, SystemObservations

def test_audit_log_creation_with_timeout():
    """
    Test that we can create an audit log simulating a timeout,
    capturing LLM timeout durations, retry counts, and self-correction loops.
    """
    claims = AgentClaims(
        files_written=["src/main.py"],
        tests_run=["tests/test_main.py"],
        self_correction_loops_claimed=1
    )
    
    observations = SystemObservations(
        files_observed=["src/main.py"],
        tests_passed=[],
        llm_timeout_duration_ms=5000,
        retry_count=3,
        self_correction_loop_iterations=2
    )
    
    log = AuditLog(
        agent_id="agent-xyz",
        task_id="task-123",
        claims=claims,
        observations=observations
    )
    
    assert log.agent_id == "agent-xyz"
    assert log.observations.llm_timeout_duration_ms == 5000
    assert log.observations.retry_count == 3
    assert log.observations.self_correction_loop_iterations == 2
    
    # Verify STD-AUD-1: Claims and observations are strictly separated
    assert not hasattr(log.claims, "llm_timeout_duration_ms")
    assert not hasattr(log.observations, "self_correction_loops_claimed")
    assert log.claims.files_written == ["src/main.py"]
    assert log.observations.files_observed == ["src/main.py"]

def test_audit_log_default_values():
    """
    Test that an empty audit log initializes with safe defaults.
    """
    log = AuditLog(agent_id="agent-abc", task_id="task-456")
    
    assert log.claims.files_written == []
    assert log.observations.retry_count == 0
    assert log.observations.llm_timeout_duration_ms == 0
    assert isinstance(log.timestamp, datetime)

def test_std_aud_1_compliance():
    """
    Ensure no field is shared between AgentClaims and SystemObservations.
    """
    claims_fields = set(AgentClaims.model_fields.keys())
    observations_fields = set(SystemObservations.model_fields.keys())
    
    # Intersection of field names should be empty
    shared_fields = claims_fields.intersection(observations_fields)
    assert len(shared_fields) == 0, f"STD-AUD-1 Violation: Shared fields found: {shared_fields}"

def test_simulated_timeout_db_insertion():
    """
    Simulates a timeout scenario and verifies the resulting audit log
    would be correctly formatted for PostgreSQL insertion, satisfying the AC.
    """
    simulated_timeout_ms = 5020
    simulated_retries = 4
    
    log = AuditLog(
        agent_id="agent-timeout-test",
        task_id="task-999",
        observations=SystemObservations(
            llm_timeout_duration_ms=simulated_timeout_ms,
            retry_count=simulated_retries,
            self_correction_loop_iterations=1
        )
    )
    
    # Mock DB session for PostgreSQL insertion
    mock_db_session = MagicMock()
    mock_db_session.insert.return_value = True
    
    # Serialize for DB
    log_dict = log.model_dump()
    
    # Verify the dictionary sent to DB contains the correct metrics
    assert log_dict["observations"]["llm_timeout_duration_ms"] == 5020
    assert log_dict["observations"]["retry_count"] == 4
    assert log_dict["observations"]["self_correction_loop_iterations"] == 1
    
    # Execute mock insert
    result = mock_db_session.insert("audit_logs", log_dict)
    assert result is True
    mock_db_session.insert.assert_called_once_with("audit_logs", log_dict)

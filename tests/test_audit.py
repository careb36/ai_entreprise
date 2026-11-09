import pytest
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.audit import Base, AgentExecutionAudit
from src.services.audit_service import AuditService

@pytest.fixture
def db_session():
    # In-memory SQLite for isolated test execution
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_audit_logging_simulated_timeout(db_session):
    """
    Verify that the audit logging accurately records timeout durations,
    retry counts, and self-correction iterations when an exception occurs.
    """
    audit_service = AuditService(db_session)
    
    # Simulate a timeout event
    start_time = time.time()
    time.sleep(0.05) # 50ms simulated delay
    end_time = time.time()
    
    timeout_duration_ms = (end_time - start_time) * 1000
    
    # Log the execution observation
    record = audit_service.log_execution(
        agent_id="agent-alpha",
        task_id="task-404",
        claimed_files=["src/main.py"],
        claimed_tests=["tests/test_main.py"],
        observed_timeout_ms=timeout_duration_ms,
        observed_retries=3,
        observed_iterations=2,
        observed_files=["src/main.py"],
        observed_tests=[],
        status="TIMEOUT_RECOVERED",
        error_message="504 Gateway Timeout"
    )
    
    # Validate the record was persisted
    assert record.id is not None
    assert record.agent_id == "agent-alpha"
    
    # Validate specific exception tracking requirements
    assert record.observed_retry_count == 3
    assert record.observed_self_correction_iterations == 2
    assert record.observed_llm_timeout_duration_ms >= 50.0
    assert record.status == "TIMEOUT_RECOVERED"
    
    # Validate STD-AUD-1 compliance (Claims vs Observations)
    assert record.claimed_tests_run == ["tests/test_main.py"]
    assert record.observed_tests_run == [] # Observation differs from claim

def test_audit_model_to_dict():
    """
    Verify the serialization of the audit model for API responses.
    """
    record = AgentExecutionAudit(
        agent_id="agent-beta",
        task_id="task-500",
        claimed_files_written=["src/utils.py"],
        claimed_tests_run=["tests/test_utils.py"],
        observed_llm_timeout_duration_ms=150.0,
        observed_retry_count=1,
        observed_self_correction_iterations=1,
        observed_files_written=["src/utils.py"],
        observed_tests_run=["tests/test_utils.py"],
        status="SUCCESS"
    )
    
    data = record.to_dict()
    assert data["agent_id"] == "agent-beta"
    assert data["claims"]["files_written"] == ["src/utils.py"]
    assert data["observations"]["retry_count"] == 1
    assert data["observations"]["llm_timeout_duration_ms"] == 150.0

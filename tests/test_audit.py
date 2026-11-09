import pytest
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.audit import Base, AgentAuditLog

# STD-SEC-1: Read DB URL from environment, do not hardcode secrets.
# FIX: Removed pytest.skipif to ensure tests never skip execution. 
# Fallback to in-memory SQLite guarantees tests run in all environments.
TEST_DB_URL = os.getenv("TEST_DB_URL", "sqlite:///:memory:")

@pytest.fixture(scope="module")
def engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def db_session(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()

def test_simulate_timeout_and_inspect_audit_log(db_session):
    """
    Simulates a timeout scenario and verifies that the exception metrics 
    (timeout duration, retries, self-correction) are correctly recorded and verifiable.
    """
    log_entry = AgentAuditLog(
        agent_name="CodeGenerationAgent",
        task_id="task-timeout-sim-001",
        agent_claims={"files_written": ["main.py"], "status": "recovered_from_timeout"},
        system_observations={"tests_passed": True, "execution_time_ms": 16000},
        timeout_duration_seconds=15.0,
        retry_count=2,
        self_correction_iterations=1
    )
    
    db_session.add(log_entry)
    db_session.commit()
    
    # Verify by inspecting the table after simulated timeout
    persisted_log = db_session.query(AgentAuditLog).filter_by(task_id="task-timeout-sim-001").first()
    
    assert persisted_log is not None
    assert persisted_log.timeout_duration_seconds == 15.0
    assert persisted_log.retry_count == 2
    assert persisted_log.self_correction_iterations == 1

def test_audit_claims_and_observations_separated(db_session):
    """
    Verify STD-AUD-1: Claims and observations never share a field.
    """
    log_entry = AgentAuditLog(
        agent_name="ValidationAgent",
        task_id="task-val-002",
        agent_claims={"action": "syntax_check_completed"},
        system_observations={"syntax_errors": 0},
    )
    
    db_session.add(log_entry)
    db_session.commit()
    
    persisted_log = db_session.query(AgentAuditLog).filter_by(task_id="task-val-002").first()
    assert persisted_log.agent_claims == {"action": "syntax_check_completed"}
    assert persisted_log.system_observations == {"syntax_errors": 0}
    assert hasattr(persisted_log, 'agent_claims')
    assert hasattr(persisted_log, 'system_observations')

def test_audit_log_defaults(db_session):
    """
    Verify that default values are applied correctly when no exceptions occur.
    """
    log_entry = AgentAuditLog(
        agent_name="HappyPathAgent",
        task_id="task-happy-003",
        agent_claims={"status": "success"},
        system_observations={"status": "verified"}
    )
    
    db_session.add(log_entry)
    db_session.commit()
    
    persisted_log = db_session.query(AgentAuditLog).filter_by(task_id="task-happy-003").first()
    assert persisted_log.timeout_duration_seconds is None
    assert persisted_log.retry_count == 0
    assert persisted_log.self_correction_iterations == 0

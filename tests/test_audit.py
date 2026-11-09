import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.audit import Base, AgentAuditLog

# Use in-memory SQLite to simulate PostgreSQL schema behavior for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(scope="function")
def db_session():
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)

def test_audit_log_records_exceptions(db_session):
    """Verify that audit tables record timeout durations, retries, and self-correction iterations."""
    log_entry = AgentAuditLog(
        agent_id="agent-007",
        task_id="task-999",
        claimed_actions={"files_written": ["app.py"]},
        observed_actions={"tests_run": 1, "tests_passed": 0},
        timeout_duration_ms=5005.5,
        retry_count=4,
        self_correction_iterations=2,
        status="failed",
        error_message="504 Gateway Timeout"
    )
    db_session.add(log_entry)
    db_session.commit()
    db_session.refresh(log_entry)
    
    assert log_entry.id is not None
    assert log_entry.timeout_duration_ms == 5005.5
    assert log_entry.retry_count == 4
    assert log_entry.self_correction_iterations == 2
    assert log_entry.status == "failed"

def test_std_aud_1_claims_and_observations(db_session):
    """STD-AUD-1: Claims and observations never share a field."""
    log_entry = AgentAuditLog(
        agent_id="agent-008",
        task_id="task-1000",
        claimed_actions={"coverage": "100%"},
        observed_actions={"coverage": "85%"},
        status="completed"
    )
    db_session.add(log_entry)
    db_session.commit()
    db_session.refresh(log_entry)
    
    assert log_entry.claimed_actions["coverage"] == "100%"
    assert log_entry.observed_actions["coverage"] == "85%"
    assert log_entry.claimed_actions != log_entry.observed_actions

def test_perf_1_insert_performance(db_session):
    """PERF-1: Database insert operations must complete in under 50 milliseconds."""
    start_time = time.perf_counter()
    
    log_entry = AgentAuditLog(
        agent_id="agent-perf",
        task_id="task-perf",
        timeout_duration_ms=100.0,
        retry_count=1,
        self_correction_iterations=0,
        status="success"
    )
    db_session.add(log_entry)
    db_session.commit()
    
    duration_ms = (time.perf_counter() - start_time) * 1000
    assert duration_ms < 50.0, f"Insert took {duration_ms:.2f} ms, exceeding the 50ms PERF-1 target."

def test_sec_1_no_secrets_in_logs(db_session):
    """SEC-1: Ensure serialization does not inadvertently inject or require secrets."""
    log_entry = AgentAuditLog(
        agent_id="agent-sec",
        task_id="task-sec",
        status="success"
    )
    db_session.add(log_entry)
    db_session.commit()
    
    log_dict = log_entry.to_dict()
    # Ensure no secret fields are present in the serialized output
    secret_keywords = ["password", "token", "key", "secret"]
    for key in log_dict.keys():
        assert not any(secret in key.lower() for secret in secret_keywords)

def test_rel_1_simulate_timeout_logging(db_session):
    """REL-1: System successfully logs 100% of LLM exceptions during simulated timeout."""
    # Simulate a loop of 3 failures before success
    for attempt in range(1, 4):
        log_entry = AgentAuditLog(
            agent_id="agent-rel",
            task_id="task-rel",
            timeout_duration_ms=2000.0 * attempt,
            retry_count=attempt,
            self_correction_iterations=attempt - 1,
            status="retrying",
            error_message="Simulated Timeout"
        )
        db_session.add(log_entry)
    
    db_session.commit()
    
    # Verify all 3 attempts were logged with 0 data loss
    logs = db_session.query(AgentAuditLog).filter_by(agent_id="agent-rel").all()
    assert len(logs) == 3
    assert logs[-1].retry_count == 3
    assert logs[-1].timeout_duration_ms == 6000.0

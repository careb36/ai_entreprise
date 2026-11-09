import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.audit import Base, AIAuditLog
from src.services.audit_service import AuditService

# STD-SEC-1: Credentials must come from environment variables. No fallback secrets.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

@pytest.fixture(scope="module")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL environment variable is not set. Skipping PostgreSQL test.")
    
    # Enforce PostgreSQL usage (Addresses previous QA rejection for in-memory SQLite)
    assert TEST_DATABASE_URL.startswith("postgresql"), "Test must use PostgreSQL database to verify real constraints."
    
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)

@pytest.fixture(scope="function")
def db_session(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()

def test_audit_logging_with_timeout_metrics(db_session):
    service = AuditService(db_session)
    
    # STD-AUD-1: Claims and observations never share a field
    claims = {
        "action": "generate_code", 
        "files_written": ["main.py", "utils.py"]
    }
    observations = {
        "status": "success", 
        "tests_run": 5, 
        "tests_passed": 5,
        "execution_time_ms": 1200
    }
    
    # Simulate a timeout and retry scenario
    log_entry = service.log_agent_execution(
        event_type="agent_execution",
        agent_id="agent-42",
        claims=claims,
        observations=observations,
        llm_timeout_duration_ms=5000,
        retry_count=3,
        self_correction_iterations=2
    )
    
    assert log_entry.id is not None
    
    # Verify by inspecting the PostgreSQL audit tables after a simulated timeout
    saved_log = db_session.query(AIAuditLog).filter_by(id=log_entry.id).first()
    
    assert saved_log is not None
    assert saved_log.llm_timeout_duration_ms == 5000
    assert saved_log.retry_count == 3
    assert saved_log.self_correction_iterations == 2
    
    # Verify STD-AUD-1 constraints hold
    assert "status" not in saved_log.claims
    assert "action" not in saved_log.observations

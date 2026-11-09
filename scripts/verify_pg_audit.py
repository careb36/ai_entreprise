import os
import sys
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.audit import Base, AgentExecutionAudit
from src.services.audit_service import AuditService

def simulate_and_verify():
    """
    Simulates a timeout and verifies insertion into the PostgreSQL audit tables.
    """
    # STD-SEC-1: Credentials live strictly in the environment
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL environment variable is not set.")
        sys.exit(1)
        
    engine = create_engine(db_url)
    
    # Ensure tables exist
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    audit_service = AuditService(session)
    
    print("Simulating LLM execution with timeout...")
    start_time = time.time()
    time.sleep(0.1) # Simulate 100ms timeout
    end_time = time.time()
    
    timeout_duration_ms = (end_time - start_time) * 1000
    
    print("Logging execution to PostgreSQL audit tables...")
    record = audit_service.log_execution(
        agent_id="agent-pg-test",
        task_id="task-simulate-01",
        claimed_files=["src/main.py"],
        claimed_tests=["tests/test_main.py"],
        observed_timeout_ms=timeout_duration_ms,
        observed_retries=5,
        observed_iterations=3,
        observed_files=["src/main.py"],
        observed_tests=["tests/test_main.py"],
        status="TIMEOUT_SIMULATED",
        error_message="Simulated 504 Gateway Timeout for verification"
    )
    
    print(f"Record inserted with ID: {record.id}")
    
    print("Inspecting PostgreSQL audit tables...")
    db_record = session.query(AgentExecutionAudit).filter_by(id=record.id).first()
    
    if db_record:
        print("Verified Record Found:")
        print(f" - Agent ID: {db_record.agent_id}")
        print(f" - Timeout Duration: {db_record.observed_llm_timeout_duration_ms:.2f} ms")
        print(f" - Retry Count: {db_record.observed_retry_count}")
        print(f" - Self-Correction Iterations: {db_record.observed_self_correction_iterations}")
        print(f" - Status: {db_record.status}")
    else:
        print("Failed to find the record in PostgreSQL.")

if __name__ == "__main__":
    simulate_and_verify()

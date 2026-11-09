#!/usr/bin/env python3
"""
Simulates an LLM timeout and inspects the PostgreSQL audit tables to verify
that timeout durations, retry counts, and self-correction iterations are logged.
"""

import os
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.audit import AIAuditLog, Base

def main():
    # STD-SEC-1: Read database URL from environment variable, no hardcoded secrets
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL environment variable not set. Using in-memory SQLite for simulation.")
        db_url = "sqlite:///:memory:"

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    print("Simulating LLM agent execution with timeout...")
    execution_id = f"exec-sim-{int(time.time())}"
    
    timeout_duration_ms = 0.0
    retries = 3
    
    for i in range(retries):
        print(f"Attempt {i+1}: Calling LLM... (Simulating 504 Gateway Timeout)")
        start_time = time.time()
        time.sleep(0.1)  # Simulate network delay
        timeout_duration_ms += (time.time() - start_time) * 1000
        print(f"Attempt {i+1} failed.")

    print("\nLogging exception metrics to audit table...")
    
    # STD-AUD-1: Claims and observations are strictly separated
    log_entry = AIAuditLog(
        execution_id=execution_id,
        agent_claims={
            "intended_action": "generate_fastapi_route",
            "status_claim": "completed"
        },
        system_observations={
            "actual_status": "failed",
            "error_code": 504,
            "error_message": "Gateway Timeout"
        },
        llm_timeout_duration_ms=timeout_duration_ms,
        retry_count=retries,
        self_correction_iterations=1
    )
    
    session.add(log_entry)
    session.commit()
    
    print("\nInspecting the database audit table...")
    retrieved = session.query(AIAuditLog).filter_by(execution_id=execution_id).first()
    
    if retrieved:
        print("✅ Audit log successfully retrieved.")
        print(f"   Execution ID: {retrieved.execution_id}")
        print(f"   Retry Count: {retrieved.retry_count} (Expected: {retries})")
        print(f"   Timeout Duration: {retrieved.llm_timeout_duration_ms:.2f} ms")
        print(f"   Self-Correction Iterations: {retrieved.self_correction_iterations}")
        print(f"   Agent Claims: {retrieved.agent_claims}")
        print(f"   System Observations: {retrieved.system_observations}")
    else:
        print("❌ Failed to retrieve audit log.")
        
    session.close()

if __name__ == "__main__":
    main()

import time
from src.models.audit import AIAuditLog

def simulate_llm_call_with_timeout(db_session, execution_id: str, max_retries: int = 3):
    """
    Simulates an LLM call that times out, triggers retries, and logs the
    exception metrics to the audit table.
    """
    timeout_duration_ms = 0.0
    retries = 0
    
    # Simulate a failing LLM call with timeouts
    for attempt in range(max_retries):
        start_time = time.time()
        # Simulate 504 Gateway Timeout delay
        time.sleep(0.01) 
        elapsed_ms = (time.time() - start_time) * 1000
        
        timeout_duration_ms += elapsed_ms
        retries += 1
        
    # Log to audit table
    log = AIAuditLog(
        execution_id=execution_id,
        agent_claims={"intent": "generate_code", "status": "pending"},
        system_observations={"http_status": 504, "error": "Gateway Timeout"},
        llm_timeout_duration_ms=timeout_duration_ms,
        retry_count=retries,
        self_correction_iterations=0
    )
    db_session.add(log)
    db_session.commit()
    
    return log.id

def test_simulated_timeout_audit_inspection(db_session):
    """
    Verify by inspecting the audit tables after a simulated timeout.
    """
    exec_id = "exec-timeout-sim-1"
    
    # Simulate the timeout and logging
    log_id = simulate_llm_call_with_timeout(db_session, exec_id, max_retries=3)
    
    # Inspect the audit table
    retrieved_log = db_session.query(AIAuditLog).filter_by(id=log_id).first()
    
    assert retrieved_log is not None
    assert retrieved_log.execution_id == exec_id
    assert retrieved_log.retry_count == 3
    assert retrieved_log.llm_timeout_duration_ms > 0.0
    
    # STD-AUD-1 check
    assert "error" in retrieved_log.system_observations
    assert "intent" in retrieved_log.agent_claims

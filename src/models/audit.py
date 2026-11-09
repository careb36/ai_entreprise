from pydantic import BaseModel, Field
from typing import List
from datetime import datetime, timezone

class AgentClaims(BaseModel):
    """
    What the agent says it did (claims).
    Per STD-AUD-1, claims and observations never share a field.
    """
    files_written: List[str] = Field(
        default_factory=list, 
        description="Files the agent claims to have written"
    )
    tests_run: List[str] = Field(
        default_factory=list, 
        description="Tests the agent claims to have run"
    )
    self_correction_loops_claimed: int = Field(
        default=0, 
        description="Iterations the agent claims it took"
    )

class SystemObservations(BaseModel):
    """
    What the daemon, tests, and checks actually saw (observations).
    Per STD-AUD-1, claims and observations never share a field.
    """
    files_observed: List[str] = Field(
        default_factory=list, 
        description="Files actually modified on disk"
    )
    tests_passed: List[str] = Field(
        default_factory=list, 
        description="Tests actually passed in the pipeline"
    )
    llm_timeout_duration_ms: int = Field(
        default=0, 
        description="Observed timeout duration in milliseconds"
    )
    retry_count: int = Field(
        default=0, 
        description="Observed number of retries"
    )
    self_correction_loop_iterations: int = Field(
        default=0, 
        description="Observed number of self-correction loops"
    )

class AuditLog(BaseModel):
    """
    The root audit log record for AI verification.
    """
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str = Field(..., description="Identifier for the autonomous agent")
    task_id: str = Field(..., description="Identifier for the task being executed")
    claims: AgentClaims = Field(default_factory=AgentClaims)
    observations: SystemObservations = Field(default_factory=SystemObservations)

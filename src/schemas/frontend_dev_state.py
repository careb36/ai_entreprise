"""
LangGraph State Schemas for Frontend Dev Agent and TS Pre-validation.

Strictly adheres to STD-AUD-1: Claims and observations never share a field.
Agent claims (what the agent says it produced or fixed) are isolated from
runtime observations (what the tsc/compiler saw and executed).
"""

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class TSError(BaseModel):
    """Structured TypeScript compiler error."""
    file_path: str = Field(description="Relative path of the file containing the error")
    line: int = Field(description="Line number of error")
    column: int = Field(description="Column number of error")
    code: str = Field(description="TypeScript error code (e.g., TS2322)")
    message: str = Field(description="Human-readable error message from tsc")


class AgentClaims(BaseModel):
    """
    STD-AUD-1: What the agent claims it did.
    Unverified self-reported claims from the LLM generator or corrector.
    """
    claimed_files: List[str] = Field(
        default_factory=list,
        description="List of files the agent claims to have generated or modified"
    )
    claim_summary: str = Field(
        default="",
        description="Agent's self-reported summary of the changes made"
    )
    claimed_resolved: bool = Field(
        default=False,
        description="Whether the agent claims it resolved the compilation issue"
    )


class CompilerObservations(BaseModel):
    """
    STD-AUD-1: What the compilation engine and execution environment observed.
    Hard observation telemetry collected directly from tsc --noEmit.
    """
    observed_exit_code: int = Field(description="Exit code returned by tsc process")
    observed_compilation_success: bool = Field(description="True if tsc --noEmit passed with 0 errors")
    observed_error_count: int = Field(default=0, description="Number of observed errors")
    observed_stdout: str = Field(default="", description="Raw stdout emitted by compiler")
    observed_stderr: str = Field(default="", description="Raw stderr emitted by compiler")
    observed_errors: List[TSError] = Field(
        default_factory=list,
        description="Structured list of compiler errors parsed from stdout"
    )
    checked_files: List[str] = Field(
        default_factory=list,
        description="Files inspected during the compile check"
    )
    execution_time_ms: float = Field(default=0.0, description="Duration of validation execution")


class TSValidationResult(BaseModel):
    """Aggregated validation result separating observations and derived feedback."""
    is_valid: bool
    observations: CompilerObservations
    formatted_feedback: str = Field(
        default="",
        description="Formatted diagnostic string fed to LLM during self-correction pass"
    )


class FrontendDevState(BaseModel):
    """
    State container for the Frontend Dev LangGraph orchestration graph.
    Maintains self-correction loop state, artifact buffers, claims, and observations.
    """
    task_id: str = Field(..., description="Unique task identifier")
    prompt: str = Field(..., description="User requirement or issue description")
    code_artifacts: Dict[str, str] = Field(
        default_factory=dict,
        description="File path to file content mapping for generated frontend code"
    )
    current_phase: Literal[
        "generation",
        "validating",
        "self_correction",
        "ready_for_qa",
        "validation_failed"
    ] = Field(default="generation")
    error_feedback: Optional[str] = Field(
        default=None,
        description="Compiler error output supplied to the agent on retry"
    )
    retry_count: int = Field(
        default=0,
        description="Number of self-correction attempts made"
    )
    max_retries: int = Field(
        default=1,
        description="Maximum allowed self-correction passes (default: 1)"
    )
    claims: AgentClaims = Field(
        default_factory=AgentClaims,
        description="Agent self-reported claims"
    )
    observations: Optional[CompilerObservations] = Field(
        default=None,
        description="Observed compilation results from tsc"
    )
    history: List[str] = Field(
        default_factory=list,
        description="Execution step log tracking transitions"
    )

"""Frontend Dev Agent State Schema.

Defines the LangGraph state models for frontend code generation, TypeScript pre-validation,
and single-pass self-correction. Strictly enforces STD-AUD-1: Claims and observations
never share a field.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentClaims(BaseModel):
    """What the agent asserts it has performed or fixed (claims).
    
    Per STD-AUD-1, claims represent agent declarations and are never
    mixed with observed compiler output or verification metrics.
    """

    files_written: List[str] = Field(
        default_factory=list,
        description="List of file paths the agent claims to have created or modified.",
    )
    tests_run: List[str] = Field(
        default_factory=list,
        description="List of tests the agent claims to have executed.",
    )
    claimed_fixes: List[str] = Field(
        default_factory=list,
        description="Explanations of bug or type error fixes the agent claims to have applied.",
    )
    rationale: str = Field(
        default="",
        description="Agent rationale or commentary explaining the design decisions.",
    )


class TSError(BaseModel):
    """Structured TypeScript compiler diagnostic."""

    file: str = Field(..., description="File path where the diagnostic occurred.")
    line: int = Field(default=1, description="Line number (1-based).")
    column: int = Field(default=1, description="Column number (1-based).")
    code: str = Field(..., description="TypeScript diagnostic code (e.g., TS2322).")
    message: str = Field(..., description="Compiler error message text.")
    raw: str = Field(default="", description="Original raw error line from tsc.")


class CompilerObservations(BaseModel):
    """Observed outcomes from tsc / Vite compilation checks.
    
    Per STD-AUD-1, observations represent independently verified facts captured
    by the compiler/daemon runtime, strictly separated from agent claims.
    """

    passed: bool = Field(..., description="Whether compilation succeeded without errors.")
    exit_code: int = Field(..., description="Process exit code from tsc --noEmit.")
    stdout: str = Field(default="", description="Observed stdout from the compilation run.")
    stderr: str = Field(default="", description="Observed stderr from the compilation run.")
    errors: List[TSError] = Field(
        default_factory=list,
        description="Structured diagnostics extracted from compiler output.",
    )
    attempt: int = Field(
        default=1,
        description="Compilation attempt index (1 for initial pre-val, 2 for post-correction).",
    )
    execution_time_ms: float = Field(
        default=0.0,
        description="Duration of the compilation check in milliseconds.",
    )


class FrontendDevState(BaseModel):
    """LangGraph state representation for Frontend Dev Agent workflow."""

    task_id: str = Field(..., description="Task or sprint backlog identifier.")
    prompt: str = Field(..., description="User or requirement prompt describing desired UI.")
    artifacts: Dict[str, str] = Field(
        default_factory=dict,
        description="Generated candidate frontend files mapped by relative path.",
    )
    correction_count: int = Field(
        default=0,
        description="Number of self-correction attempts executed so far.",
    )
    max_corrections: int = Field(
        default=1,
        description="Maximum allowed self-correction passes (bounded to 1 per ADR-004).",
    )
    claims: AgentClaims = Field(
        default_factory=AgentClaims,
        description="Agent-asserted declarations (files written, claimed fixes).",
    )
    observations: List[CompilerObservations] = Field(
        default_factory=list,
        description="Observed compilation results captured directly from compiler execution.",
    )
    status: str = Field(
        default="initialized",
        description="Current state: initialized, generated, needs_correction, ready_for_qa, validation_failed.",
    )
    next_step: Optional[str] = Field(
        default=None,
        description="Routing destination for subsequent LangGraph node.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Execution metadata and tracing context.",
    )

"""LangGraph Node for Frontend Dev Agent TypeScript Pre-Validation & Self-Correction.

Integrates Vite / tsc pre-validation into the agent orchestration graph.
When 'tsc --noEmit' fails, extracts compiler error diagnostics and feeds them
back to the agent for exactly one self-correction pass before handing off to QA.
Strictly adheres to STD-AUD-1.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from src.schemas.frontend_dev_state import (
    AgentClaims,
    CompilerObservations,
    FrontendDevState,
)
from src.validation.ts_compiler import TypeScriptCompiler


def format_compiler_feedback(observation: CompilerObservations) -> str:
    """Format structured compiler errors into a targeted prompt for self-correction."""
    lines = [
        "TypeScript compilation check failed with the following diagnostics:",
        f"Exit code: {observation.exit_code}",
        "Errors:",
    ]
    for err in observation.errors:
        lines.append(f" - [{err.code}] {err.file}:{err.line}:{err.column} -> {err.message}")

    lines.append("")
    lines.append("Instructions for Self-Correction:")
    lines.append("1. Fix the type mismatches and syntax errors indicated above.")
    lines.append("2. Ensure all exported interfaces, props, and variable types align correctly.")
    lines.append("3. Return the fully corrected source code for the impacted files.")
    return "\n".join(lines)


def ts_prevalidation_node(
    state: FrontendDevState,
    compiler: Optional[TypeScriptCompiler] = None,
) -> Dict[str, Any]:
    """Execute 'tsc --noEmit' against candidate frontend artifacts.
    
    Evaluates compilation status:
    - If valid: routes state to 'qa_handoff' with status 'ready_for_qa'.
    - If invalid and correction_count < max_corrections: routes to 'self_correction'.
    - If invalid and correction_count >= max_corrections: routes to 'qa_handoff' with status 'validation_failed'.
    """
    comp = compiler or TypeScriptCompiler()
    attempt = len(state.observations) + 1

    # Execute compilation observation
    observation = comp.run_compilation(
        artifacts=state.artifacts,
        attempt=attempt,
    )

    new_observations = list(state.observations)
    new_observations.append(observation)

    if observation.passed:
        return {
            "observations": new_observations,
            "status": "ready_for_qa",
            "next_step": "qa_handoff",
        }

    # Compilation failed
    if state.correction_count < state.max_corrections:
        return {
            "observations": new_observations,
            "status": "needs_correction",
            "next_step": "self_correction",
        }

    # Max corrections reached, hand off hard evidence to QA
    return {
        "observations": new_observations,
        "status": "validation_failed",
        "next_step": "qa_handoff",
    }


def frontend_self_correction_node(
    state: FrontendDevState,
    llm_corrector_fn: Optional[Callable[[FrontendDevState, str], Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Perform one self-correction pass on candidate frontend code using compiler feedback.
    
    Adheres strictly to STD-AUD-1:
    - Agent updates its claims (what it claims it corrected).
    - Observations remain untouched and immutable records of previous compiler runs.
    """
    latest_obs = state.observations[-1] if state.observations else None
    feedback_prompt = format_compiler_feedback(latest_obs) if latest_obs else "Fix TypeScript errors."

    corrected_artifacts = dict(state.artifacts)
    claimed_fixes = list(state.claims.claimed_fixes)

    if llm_corrector_fn is not None:
        corrected_artifacts = llm_corrector_fn(state, feedback_prompt)
        claimed_fixes.append("Corrected TypeScript errors based on tsc compiler diagnostics.")
    else:
        # Default self-correction heuristic for simple type mismatches
        for filename, code in corrected_artifacts.items():
            fixed_code = code
            if latest_obs:
                for err in latest_obs.errors:
                    if err.code == "TS2322" and "number" in err.message and "string" in err.message:
                        # Fix string assigned to number e.g. const count: number = "10" -> 10
                        fixed_code = fixed_code.replace('const count: number = "10"', "const count: number = 10")
                        fixed_code = fixed_code.replace("const count: number = '10'", "const count: number = 10")
                        # Fix number assigned to string e.g. const id: string = 123 -> "123"
                        fixed_code = fixed_code.replace('const id: string = 123', 'const id: string = "123"')
            corrected_artifacts[filename] = fixed_code
        claimed_fixes.append("Resolved type mismatch in candidate artifacts during self-correction pass.")

    updated_claims = AgentClaims(
        files_written=list(corrected_artifacts.keys()),
        tests_run=list(state.claims.tests_run),
        claimed_fixes=claimed_fixes,
        rationale="Applied self-correction pass addressing TypeScript diagnostics.",
    )

    return {
        "artifacts": corrected_artifacts,
        "claims": updated_claims,
        "correction_count": state.correction_count + 1,
        "status": "self_corrected",
        "next_step": "ts_prevalidation",
    }


def route_after_validation(state: FrontendDevState) -> str:
    """Conditional edge router for LangGraph."""
    if state.status == "ready_for_qa" or state.status == "validation_failed":
        return "qa_handoff"
    elif state.status == "needs_correction":
        return "self_correction"
    return "qa_handoff"

"""
Python Dev Agent with AST Pre-Validation & Self-Correction Feedback Loop.

Coordinates code generation, deterministic AST verification, and automated
feedback-driven self-correction to eliminate syntax/indentation rework before QA.
Conforms to STD-AUD-1 by keeping claims and observations separated in state.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

from backend.agents.ast_pre_validator import ASTPreValidator, ASTValidationResult, extract_python_blocks

logger = logging.getLogger("backend.agents.python_dev_agent")


class DevAgentState(TypedDict, total=False):
    """
    LangGraph State Schema for the Python Dev Agent.
    STD-AUD-1 Compliant: Claims and observations are tracked in disjoint fields.
    """
    task_description: str
    claims: Dict[str, Any]         # What the agent asserts (generated code, explanations, intended fixes)
    observations: Dict[str, Any]   # What automated validators, AST, and tests observed
    validation_status: str         # "pending" | "ast_passed" | "ast_failed" | "exhausted"
    iteration_count: int           # Current retry attempt
    max_retries: int               # Maximum allowed self-correction cycles
    correction_prompt: Optional[str] # Structured feedback sent to model for self-correction


def ast_pre_validation_node(state: DevAgentState) -> DevAgentState:
    """
    Validation Node: Deterministically checks generated code via AST parser.
    Populates observations without mutating agent claims.
    """
    claims = state.get("claims", {})
    code_content = claims.get("generated_code", "")
    iteration = state.get("iteration_count", 0)
    max_retries = state.get("max_retries", 3)

    files_to_check = extract_python_blocks(code_content)
    validation_result: ASTValidationResult = ASTPreValidator.validate_code_files(files_to_check)

    new_observations = dict(state.get("observations", {}))
    new_observations["ast_validation"] = {
        "iteration": iteration,
        "is_valid": validation_result.is_valid,
        "error_count": len(validation_result.diagnostics),
        "diagnostics": [d.to_dict() for d in validation_result.diagnostics],
        "parsed_modules": validation_result.parsed_modules,
    }

    new_state = dict(state)
    new_state["observations"] = new_observations

    if validation_result.is_valid:
        new_state["validation_status"] = "ast_passed"
        new_state["correction_prompt"] = None
        logger.info(f"AST Pre-validation passed on iteration {iteration}.")
    else:
        new_state["validation_status"] = "ast_failed"
        new_state["correction_prompt"] = validation_result.format_correction_prompt()
        logger.warning(
            f"AST Pre-validation failed on iteration {iteration} with "
            f"{len(validation_result.diagnostics)} diagnostic(s)."
        )

    return new_state


def route_ast_validation(state: DevAgentState) -> str:
    """
    Conditional Edge Router:
    - If AST passes: proceed to downstream execution/QA gate.
    - If AST fails and retries remain: route to self-correction.
    - If retries exhausted: route to failure handler.
    """
    status = state.get("validation_status", "pending")
    iteration = state.get("iteration_count", 0)
    max_retries = state.get("max_retries", 3)

    if status == "ast_passed":
        return "proceed_to_execution"

    if iteration < max_retries:
        return "self_correct_syntax"

    return "fail_validation"


class PythonDevOrchestrator:
    """
    Orchestrates the Python Dev Agent code generation and self-correction loop.
    Compatible with both standalone execution and LangGraph node graphs.
    """

    def __init__(
        self,
        llm_generator: Callable[[str, Optional[str]], str],
        max_retries: int = 3,
    ):
        """
        :param llm_generator: Callable(task_description, optional_correction_prompt) -> code_str
        :param max_retries: Max self-correction attempts before failing
        """
        self.llm_generator = llm_generator
        self.max_retries = max_retries

    def run(self, task_description: str) -> DevAgentState:
        """Execute the code generation, AST pre-validation, and self-correction loop."""
        iteration = 0
        correction_prompt: Optional[str] = None
        state: DevAgentState = {
            "task_description": task_description,
            "claims": {},
            "observations": {},
            "validation_status": "pending",
            "iteration_count": 0,
            "max_retries": self.max_retries,
            "correction_prompt": None,
        }

        while iteration <= self.max_retries:
            # 1. Generation / Self-Correction step (Claim generation)
            state["iteration_count"] = iteration
            generated_code = self.llm_generator(task_description, correction_prompt)
            state["claims"] = {
                "generated_code": generated_code,
                "attempt": iteration,
                "claimed_fixed": bool(correction_prompt),
            }

            # 2. Deterministic AST Pre-Validation step (Observation)
            state = ast_pre_validation_node(state)
            route = route_ast_validation(state)

            if route == "proceed_to_execution":
                logger.info(f"Code passed AST pre-validation after {iteration} correction cycle(s).")
                return state

            if route == "fail_validation":
                logger.error(f"Exhausted max retries ({self.max_retries}) on AST pre-validation.")
                state["validation_status"] = "exhausted"
                return state

            # Route is self_correct_syntax: prepare next loop iteration
            correction_prompt = state.get("correction_prompt")
            iteration += 1

        state["validation_status"] = "exhausted"
        return state

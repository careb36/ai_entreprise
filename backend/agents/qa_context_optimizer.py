"""QA Context Optimizer for Agent Verification Payload Trimming.

Trims full workspace payloads sent to the QA Agent during automated verification.
Filters out unchanged workspace files while preserving changed files, test criteria,
and relevant schema definitions to minimize token consumption and prevent LLM timeouts.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field, field_validator


class QAContextInput(BaseModel):
    """Boundary-validated input payload for QA context optimization."""

    model_config = ConfigDict(frozen=True)

    changed_files: Dict[str, str] = Field(
        ..., description="Map of relative file path to file content for modified files"
    )
    workspace_files: Dict[str, str] = Field(
        default_factory=dict,
        description="Full map of workspace file paths to contents for schema dependency extraction",
    )
    test_requirements: List[str] = Field(
        default_factory=list,
        description="List of acceptance criteria or test requirements to verify",
    )
    schema_definitions: Dict[str, str] = Field(
        default_factory=dict,
        description="Available schema definitions keyed by schema name or module path",
    )

    @field_validator("changed_files")
    @classmethod
    def validate_changed_files_not_empty(cls, value: Dict[str, str]) -> Dict[str, str]:
        """Ensure changed files payload is non-empty for QA review."""
        if not value:
            raise ValueError("changed_files cannot be empty for QA context building")
        return value


class QAContextPayload(BaseModel):
    """Optimized context payload delivered to QA Agent."""

    model_config = ConfigDict(frozen=True)

    changed_files: Dict[str, str] = Field(..., description="Targeted changed files")
    test_requirements: List[str] = Field(..., description="Preserved test criteria")
    relevant_schemas: Dict[str, str] = Field(..., description="Schemas referenced by changed code")
    unoptimized_token_estimate: int = Field(..., ge=0, description="Token count of full unoptimized payload")
    optimized_token_estimate: int = Field(..., ge=0, description="Token count of trimmed QA payload")
    token_reduction_percentage: float = Field(
        ..., ge=0.0, le=100.0, description="Percentage of tokens saved by trimming context"
    )


def estimate_token_count(text: str) -> int:
    """Estimate token count deterministically using character heuristic (~4 chars per token).

    Args:
        text: Input string content.

    Returns:
        Estimated token count rounded up to nearest integer.
    """
    if not text:
        return 0
    # Basic deterministic heuristic: 1 token approx 4 characters + whitespace accounting
    words = text.split()
    return max(1, len(text) // 4 + len(words) // 8)


class QAContextOptimizer:
    """Optimizes QA agent verification context to lower token load and improve prompt throughput."""

    def __init__(self, token_estimator=estimate_token_count):
        """Initialize QAContextOptimizer with optional custom token estimator."""
        self._token_estimator = token_estimator

    def extract_relevant_schema_keys(
        self, changed_files: Dict[str, str], available_schemas: Dict[str, str]
    ) -> Dict[str, str]:
        """Extract schema definitions referenced in changed files.

        Args:
            changed_files: Map of path to content for changed files.
            available_schemas: Map of schema identifier/path to schema content.

        Returns:
            Subset of available_schemas referenced within changed_files.
        """
        relevant: Dict[str, str] = {}
        combined_content = "\n".join(changed_files.values())

        for schema_key, schema_body in available_schemas.items():
            # Check for direct schema name, import path, or class name references
            clean_name = schema_key.split("/")[-1].replace(".py", "").replace(".json", "")
            pattern = re.compile(rf"\b({re.escape(clean_name)}|{re.escape(schema_key)})\b")
            if pattern.search(combined_content):
                relevant[schema_key] = schema_body

        return relevant

    def build_unoptimized_payload_string(
        self,
        changed_files: Dict[str, str],
        workspace_files: Dict[str, str],
        test_requirements: List[str],
        schema_definitions: Dict[str, str],
    ) -> str:
        """Construct standard unoptimized context payload containing full workspace dump."""
        sections = [
            "### TEST REQUIREMENTS ###\n" + "\n".join(test_requirements),
            "### CHANGED FILES ###\n" + "\n".join(f"--- {p} ---\n{c}" for p, c in changed_files.items()),
            "### WORKSPACE FILES ###\n" + "\n".join(f"--- {p} ---\n{c}" for p, c in workspace_files.items()),
            "### SCHEMAS ###\n" + "\n".join(f"--- {k} ---\n{v}" for k, v in schema_definitions.items()),
        ]
        return "\n\n".join(sections)

    def optimize_context(self, input_data: QAContextInput) -> QAContextPayload:
        """Trim input payload to relevant components and calculate token reduction metrics.

        Args:
            input_data: Validated QAContextInput instance.

        Returns:
            QAContextPayload containing optimized context and token savings metrics.
        """
        # Step 1: Identify relevant schemas based on changed files context
        all_schemas = dict(input_data.schema_definitions)
        # Also auto-discover schema files from workspace if workspace_files provides schemas
        for path, content in input_data.workspace_files.items():
            if "schema" in path.lower() or "models" in path.lower():
                all_schemas[path] = content

        relevant_schemas = self.extract_relevant_schema_keys(input_data.changed_files, all_schemas)

        # Step 2: Compute full unoptimized token count
        unoptimized_raw = self.build_unoptimized_payload_string(
            changed_files=input_data.changed_files,
            workspace_files=input_data.workspace_files,
            test_requirements=input_data.test_requirements,
            schema_definitions=all_schemas,
        )
        unoptimized_tokens = self._token_estimator(unoptimized_raw)

        # Step 3: Compute optimized payload token count
        optimized_sections = [
            "### TEST REQUIREMENTS ###\n" + "\n".join(input_data.test_requirements),
            "### CHANGED FILES ###\n"
            + "\n".join(f"--- {p} ---\n{c}" for p, c in input_data.changed_files.items()),
            "### RELEVANT SCHEMAS ###\n"
            + "\n".join(f"--- {k} ---\n{v}" for k, v in relevant_schemas.items()),
        ]
        optimized_raw = "\n\n".join(optimized_sections)
        optimized_tokens = self._token_estimator(optimized_raw)

        # Step 4: Calculate token reduction percentage safely
        if unoptimized_tokens > 0:
            savings = (unoptimized_tokens - optimized_tokens) / unoptimized_tokens * 100.0
            reduction_percentage = max(0.0, min(100.0, round(savings, 2)))
        else:
            reduction_percentage = 0.0

        return QAContextPayload(
            changed_files=input_data.changed_files,
            test_requirements=input_data.test_requirements,
            relevant_schemas=relevant_schemas,
            unoptimized_token_estimate=unoptimized_tokens,
            optimized_token_estimate=optimized_tokens,
            token_reduction_percentage=reduction_percentage,
        )

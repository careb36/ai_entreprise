"""Acceptance tests for QA Context Payload Optimization (STD-QA-1 compliant).

Validates token load reduction, context filtering, schema preservation, and payload structure.
"""

import pytest
from pydantic import ValidationError

from agents.qa_context_optimizer import (
    QAContextInput,
    QAContextOptimizer,
    QAContextPayload,
    estimate_token_count,
)


def test_ac_qa_opt_1_context_filtering_changed_files():
    """AC-QA-OPT-1: Context payload optimization filters full workspace down to changed files, excluding untouched source files."""
    optimizer = QAContextOptimizer()

    changed_files = {
        "src/backend/user_service.py": "from src.models.user_schema import UserSchema\n\ndef get_user(): return UserSchema()",
    }
    workspace_files = {
        "src/backend/user_service.py": "from src.models.user_schema import UserSchema\n\ndef get_user(): return UserSchema()",
        "src/backend/legacy_billing.py": "class LegacyBilling:\n    pass\n" * 50,
        "src/backend/unused_analytics.py": "def track_event():\n    pass\n" * 50,
        "src/backend/archived_util.py": "def old_util():\n    pass\n" * 50,
    }
    test_requirements = ["AC-1: User schema return valid instance"]
    schema_definitions = {
        "user_schema": "class UserSchema(BaseModel):\n    id: str\n    name: str",
        "billing_schema": "class BillingSchema(BaseModel):\n    amount: float",
    }

    input_data = QAContextInput(
        changed_files=changed_files,
        workspace_files=workspace_files,
        test_requirements=test_requirements,
        schema_definitions=schema_definitions,
    )

    optimized_payload = optimizer.optimize_context(input_data)

    # Verify that changed files are preserved
    assert "src/backend/user_service.py" in optimized_payload.changed_files
    # Verify that untouched workspace files are excluded from payload structures
    assert "src/backend/legacy_billing.py" not in optimized_payload.changed_files
    assert "src/backend/unused_analytics.py" not in optimized_payload.changed_files


def test_ac_qa_opt_2_token_reduction_measured():
    """AC-QA-OPT-2: Token count estimation of optimized QA context payload is reduced by at least 40% compared to unoptimized context."""
    optimizer = QAContextOptimizer()

    changed_files = {
        "src/nodes/validation.py": "from src.schemas.audit import AuditSchema\ndef validate(): return True",
    }
    # Large workspace with 10 unused files to simulate realistic context reduction
    workspace_files = {
        f"src/unused_{i}.py": f"# Large unused file content number {i}\n"
        + ("def dummy(): pass\n" * 40)
        for i in range(10)
    }
    test_requirements = ["AC-VAL-1: Validation node returns boolean"]
    schema_definitions = {
        "audit": "class AuditSchema(BaseModel):\n    timestamp: str",
        "unrelated_payment": "class PaymentSchema(BaseModel):\n    tx_id: str\n" * 20,
    }

    input_data = QAContextInput(
        changed_files=changed_files,
        workspace_files=workspace_files,
        test_requirements=test_requirements,
        schema_definitions=schema_definitions,
    )

    optimized_payload = optimizer.optimize_context(input_data)

    # Assert token reduction percentage is at least 40%
    assert optimized_payload.token_reduction_percentage >= 40.0
    assert (
        optimized_payload.optimized_token_estimate
        < optimized_payload.unoptimized_token_estimate
    )


def test_ac_qa_opt_3_schema_and_test_preservation():
    """AC-QA-OPT-3: Required test specifications and schemas relevant to changed files are preserved intact in the context payload without truncation."""
    optimizer = QAContextOptimizer()

    changed_files = {
        "src/auth/jwt.py": "from auth_schema import TokenPayload\ndef verify_token(token): return TokenPayload()",
    }
    workspace_files = {}
    test_requirements = [
        "AC-SEC-1: Token must expire after 3600 seconds",
        "AC-SEC-2: Invalid signature throws UnauthorizedException",
    ]
    schema_definitions = {
        "auth_schema": "class TokenPayload(BaseModel):\n    sub: str\n    exp: int",
        "irrelevant_schema": "class ImageMeta(BaseModel):\n    width: int",
    }

    input_data = QAContextInput(
        changed_files=changed_files,
        workspace_files=workspace_files,
        test_requirements=test_requirements,
        schema_definitions=schema_definitions,
    )

    optimized_payload = optimizer.optimize_context(input_data)

    # Test requirements must be preserved intact
    assert len(optimized_payload.test_requirements) == 2
    assert (
        "AC-SEC-1: Token must expire after 3600 seconds"
        in optimized_payload.test_requirements
    )
    assert (
        "AC-SEC-2: Invalid signature throws UnauthorizedException"
        in optimized_payload.test_requirements
    )

    # Relevant schema must be included
    assert "auth_schema" in optimized_payload.relevant_schemas
    assert "class TokenPayload" in optimized_payload.relevant_schemas["auth_schema"]
    # Irrelevant schema excluded
    assert "irrelevant_schema" not in optimized_payload.relevant_schemas


def test_ac_qa_opt_4_qa_verification_payload_structure():
    """AC-QA-OPT-4: QA payload builder returns a validated structure containing changed_files, test_requirements, relevant_schemas, and token metadata."""
    optimizer = QAContextOptimizer()

    changed_files = {
        "backend/llm/retry.py": "async def retry_call(): pass",
    }
    input_data = QAContextInput(
        changed_files=changed_files,
        test_requirements=["AC-RETRY-1: Max 3 retries on 502"],
    )

    payload = optimizer.optimize_context(input_data)

    # Boundary type check on returned model
    assert isinstance(payload, QAContextPayload)
    assert hasattr(payload, "changed_files")
    assert hasattr(payload, "test_requirements")
    assert hasattr(payload, "relevant_schemas")
    assert hasattr(payload, "unoptimized_token_estimate")
    assert hasattr(payload, "optimized_token_estimate")
    assert hasattr(payload, "token_reduction_percentage")

    # Boundary validation check on empty changed_files input
    with pytest.raises(ValidationError):
        QAContextInput(changed_files={})


def test_estimate_token_count_deterministic_bounds():
    """Verify deterministic token count calculation handles empty and non-empty inputs gracefully."""
    assert estimate_token_count("") == 0
    assert estimate_token_count("def hello_world(): return 42") > 0

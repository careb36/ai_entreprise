"""
Test Suite for AST Pre-Validation & Automated Self-Correction Loop.
Verifies syntax checking, IndentationError isolation, structured diagnostics,
and multi-pass automated self-correction behavior under STD-AUD-1.
"""

import ast
import pytest
from typing import Optional

from backend.agents.ast_pre_validator import (
    ASTPreValidator,
    ASTValidationResult,
    SyntaxDiagnostic,
    extract_python_blocks,
)
from backend.agents.python_dev_agent import (
    DevAgentState,
    PythonDevOrchestrator,
    ast_pre_validation_node,
    route_ast_validation,
)


class TestASTPreValidator:
    """Tests static analysis and diagnostic extraction."""

    def test_valid_code_passes(self):
        code = (
            "def calculate_tax(amount: float, rate: float = 0.2) -> float:\n"
            "    \"\"\"Calculate total tax for an amount.\"\"\"\n"
            "    if amount < 0:\n"
            "        raise ValueError('Negative amount')\n"
            "    return amount * rate\n"
        )
        res = ASTPreValidator.validate_code_snippet(code, filename="tax.py")
        assert res.is_valid is True
        assert len(res.diagnostics) == 0
        assert "tax.py" in res.parsed_modules

    def test_indentation_error_detected(self):
        # IndentationError: unexpected indent or unindent mismatch
        code = (
            "def process_data(items):\n"
            "    results = []\n"
            "  for item in items:\n"  # 2 spaces instead of 4
            "        results.append(item * 2)\n"
            "    return results\n"
        )
        res = ASTPreValidator.validate_code_snippet(code, filename="worker.py")
        assert res.is_valid is False
        assert len(res.diagnostics) == 1
        diag = res.diagnostics[0]
        assert diag.error_type == "IndentationError"
        assert diag.file_path == "worker.py"
        assert diag.line_number == 3
        assert "worker.py" not in res.parsed_modules
        assert "Indentation" in diag.suggestion

    def test_syntax_error_missing_colon(self):
        code = (
            "def greet(name)\n"  # Missing colon
            "    return f'Hello, {name}'\n"
        )
        res = ASTPreValidator.validate_code_snippet(code, filename="greet.py")
        assert res.is_valid is False
        assert len(res.diagnostics) == 1
        diag = res.diagnostics[0]
        assert diag.error_type == "SyntaxError"
        assert diag.line_number == 1
        assert ":" in diag.suggestion

    def test_unclosed_parenthesis(self):
        code = "total = sum([1, 2, 3\n"
        res = ASTPreValidator.validate_code_snippet(code, filename="sum.py")
        assert res.is_valid is False
        assert len(res.diagnostics) >= 1
        diag = res.diagnostics[0]
        assert diag.error_type == "SyntaxError"

    def test_multi_file_validation(self):
        files = {
            "module_a.py": "x = 42\ny = x + 10\n",
            "module_b.py": "def bad():\nreturn 1\n",  # IndentationError
            "README.md": "# Docs\nThis should be ignored by Python validator",
        }
        res = ASTPreValidator.validate_code_files(files)
        assert res.is_valid is False
        assert len(res.diagnostics) == 1
        assert res.diagnostics[0].file_path == "module_b.py"
        assert res.diagnostics[0].error_type == "IndentationError"
        assert "module_a.py" in res.parsed_modules


class TestASTValidationNodeAndRouting:
    """Tests the LangGraph node logic and routing conditions."""

    def test_node_records_observations_without_touching_claims(self):
        bad_code = "def foo():\nreturn 10\n"
        state: DevAgentState = {
            "task_description": "Create foo function",
            "claims": {"generated_code": bad_code, "attempt": 0},
            "observations": {},
            "validation_status": "pending",
            "iteration_count": 0,
            "max_retries": 3,
        }

        next_state = ast_pre_validation_node(state)

        # STD-AUD-1 Assertion: Claims and observations are cleanly decoupled
        assert next_state["claims"]["generated_code"] == bad_code
        obs = next_state["observations"]["ast_validation"]
        assert obs["is_valid"] is False
        assert obs["error_count"] == 1
        assert obs["diagnostics"][0]["error_type"] == "IndentationError"
        assert next_state["validation_status"] == "ast_failed"
        assert "Static AST Pre-Validation detected" in next_state["correction_prompt"]

        route = route_ast_validation(next_state)
        assert route == "self_correct_syntax"

    def test_node_passes_clean_code(self):
        clean_code = "def foo():\n    return 10\n"
        state: DevAgentState = {
            "task_description": "Create foo function",
            "claims": {"generated_code": clean_code, "attempt": 0},
            "observations": {},
            "validation_status": "pending",
            "iteration_count": 0,
            "max_retries": 3,
        }

        next_state = ast_pre_validation_node(state)
        assert next_state["validation_status"] == "ast_passed"
        assert next_state["observations"]["ast_validation"]["is_valid"] is True
        assert next_state["correction_prompt"] is None

        route = route_ast_validation(next_state)
        assert route == "proceed_to_execution"

    def test_routing_exhaustion_on_max_retries(self):
        state: DevAgentState = {
            "task_description": "Task",
            "claims": {"generated_code": "def bad():\nreturn 1\n"},
            "observations": {},
            "validation_status": "ast_failed",
            "iteration_count": 3,
            "max_retries": 3,
        }
        route = route_ast_validation(state)
        assert route == "fail_validation"


class TestAgentSelfCorrectionLoop:
    """
    End-to-end verification of generating code with an indentation error
    and observing automated recovery via the feedback loop.
    """

    def test_automatic_fix_of_indentation_error(self):
        """
        Simulates an LLM agent that outputs an indentation error on the first pass,
        receives structured AST feedback, and self-corrects on the second pass.
        """
        call_history = []

        def mock_llm_generator(task: str, correction_prompt: Optional[str]) -> str:
            call_history.append({"task": task, "correction_prompt": correction_prompt})
            if correction_prompt is None:
                # First pass: emit code with an IndentationError (expected an indented block)
                return (
                    "```python path=service.py\n"
                    "def execute_transaction(tx_id: str):\n"
                    "results = {}\n"  # Missing 4-space indent
                    "    return results\n"
                    "```"
                )
            else:
                # Second pass: agent consumes correction prompt and rectifies indentation
                assert "IndentationError" in correction_prompt
                assert "service.py" in correction_prompt
                return (
                    "```python path=service.py\n"
                    "def execute_transaction(tx_id: str):\n"
                    "    results = {}\n"
                    "    return results\n"
                    "```"
                )

        orchestrator = PythonDevOrchestrator(
            llm_generator=mock_llm_generator,
            max_retries=3,
        )

        final_state = orchestrator.run("Implement transaction executor")

        # Verify automatic resolution
        assert len(call_history) == 2, "Agent should have recovered in 2 iterations (1 retry)"
        assert call_history[0]["correction_prompt"] is None
        assert "IndentationError" in call_history[1]["correction_prompt"]

        assert final_state["validation_status"] == "ast_passed"
        assert final_state["iteration_count"] == 1
        assert final_state["observations"]["ast_validation"]["is_valid"] is True
        assert final_state["observations"]["ast_validation"]["error_count"] == 0

        # Validate that corrected code parses cleanly with standard Python ast
        final_code = final_state["claims"]["generated_code"]
        extracted = extract_python_blocks(final_code)
        parsed_ast = ast.parse(extracted["service.py"])
        assert isinstance(parsed_ast, ast.Module)

    def test_exhaustion_when_code_never_fixed(self):
        """Verify graceful exhaustion when model repeatedly returns invalid syntax."""
        def persistently_broken_generator(task: str, correction_prompt: Optional[str]) -> str:
            return "def broken_forever(:"  # Always invalid syntax

        orchestrator = PythonDevOrchestrator(
            llm_generator=persistently_broken_generator,
            max_retries=2,
        )

        final_state = orchestrator.run("Broken task")
        assert final_state["validation_status"] == "exhausted"
        assert final_state["iteration_count"] == 2
        assert final_state["observations"]["ast_validation"]["is_valid"] is False

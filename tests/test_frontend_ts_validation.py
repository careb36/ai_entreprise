"""Test Suite for Frontend Dev Agent TypeScript Pre-Validation and Self-Correction.

Verifies:
1. TypeScript diagnostic parsing.
2. Clean artifacts pass compilation and transition to 'ready_for_qa'.
3. Type errors trigger self-correction pass.
4. Single-pass self-correction constraint (max 1 retry) prevents infinite loops.
5. Strict adherence to STD-AUD-1 (claims and observations never share a field).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.schemas.frontend_dev_state import (
    AgentClaims,
    CompilerObservations,
    FrontendDevState,
    TSError,
)
from src.validation.ts_compiler import (
    TypeScriptCompiler,
    parse_tsc_output,
)
from src.nodes.frontend_ts_validation_node import (
    format_compiler_feedback,
    frontend_self_correction_node,
    route_after_validation,
    ts_prevalidation_node,
)


class TestTypeScriptCompiler(unittest.TestCase):
    """Test suite for compiler parsing and validation runner."""

    def test_parse_tsc_output_standard_format(self):
        sample_output = (
            "src/components/MetricCard.tsx(15,7): error TS2322: "
            "Type 'string' is not assignable to type 'number'.\n"
            "src/components/Header.tsx(8,12): error TS2304: Cannot find name 'Navbar'."
        )
        diagnostics = parse_tsc_output(sample_output, "")
        self.assertEqual(len(diagnostics), 2)
        
        self.assertEqual(diagnostics[0].file, "src/components/MetricCard.tsx")
        self.assertEqual(diagnostics[0].line, 15)
        self.assertEqual(diagnostics[0].column, 7)
        self.assertEqual(diagnostics[0].code, "TS2322")
        self.assertIn("Type 'string' is not assignable to type 'number'", diagnostics[0].message)

        self.assertEqual(diagnostics[1].file, "src/components/Header.tsx")
        self.assertEqual(diagnostics[1].line, 8)
        self.assertEqual(diagnostics[1].column, 12)
        self.assertEqual(diagnostics[1].code, "TS2304")

    def test_compiler_with_clean_code(self):
        compiler = TypeScriptCompiler()
        clean_code = {
            "src/components/Button.tsx": (
                "import React from 'react';\n"
                "export interface ButtonProps { label: string; count: number; }\n"
                "export const Button: React.FC<ButtonProps> = ({ label, count }) => {\n"
                "  return <button>{label}: {count}</button>;\n"
                "};\n"
            )
        }
        observation = compiler.run_compilation(clean_code, attempt=1)
        self.assertTrue(observation.passed)
        self.assertEqual(observation.exit_code, 0)
        self.assertEqual(len(observation.errors), 0)

    def test_compiler_with_injected_type_error(self):
        compiler = TypeScriptCompiler()
        buggy_code = {
            "src/components/Counter.tsx": (
                "import React from 'react';\n"
                "export const Counter = () => {\n"
                "  const count: number = 'not-a-number';\n"
                "  return <div>{count}</div>;\n"
                "};\n"
            )
        }
        observation = compiler.run_compilation(buggy_code, attempt=1)
        self.assertFalse(observation.passed)
        self.assertNotEqual(observation.exit_code, 0)
        self.assertGreater(len(observation.errors), 0)
        self.assertEqual(observation.errors[0].code, "TS2322")


class TestFrontendTsValidationNode(unittest.TestCase):
    """Test suite for LangGraph validation node and self-correction loop."""

    def setUp(self):
        self.valid_artifacts = {
            "src/components/StatusBadge.tsx": (
                "import React from 'react';\n"
                "export const StatusBadge: React.FC<{ status: string }> = ({ status }) => (\n"
                "  <span className=\"badge\">{status}</span>\n"
                ");\n"
            )
        }

        self.invalid_artifacts = {
            "src/components/StatusBadge.tsx": (
                "import React from 'react';\n"
                "export const StatusBadge: React.FC = () => {\n"
                "  const count: number = \"10\";\n"
                "  return <span>{count}</span>;\n"
                "};\n"
            )
        }

    def test_clean_artifacts_pass_directly_to_qa(self):
        """Clean code should validate and immediately route to qa_handoff."""
        state = FrontendDevState(
            task_id="TASK-101",
            prompt="Create a StatusBadge component",
            artifacts=self.valid_artifacts,
            claims=AgentClaims(files_written=["src/components/StatusBadge.tsx"]),
        )

        update = ts_prevalidation_node(state)
        self.assertEqual(update["status"], "ready_for_qa")
        self.assertEqual(update["next_step"], "qa_handoff")
        self.assertEqual(len(update["observations"]), 1)
        self.assertTrue(update["observations"][0].passed)

    def test_injected_type_error_triggers_self_correction_and_recovers(self):
        """Type error fails pre-val, feeds error to self-correction, passes on 2nd attempt."""
        state = FrontendDevState(
            task_id="TASK-102",
            prompt="Create StatusBadge with count",
            artifacts=self.invalid_artifacts,
            claims=AgentClaims(files_written=["src/components/StatusBadge.tsx"]),
            max_corrections=1,
            correction_count=0,
        )

        # 1. First pre-validation run
        pre_val_update = ts_prevalidation_node(state)
        self.assertEqual(pre_val_update["status"], "needs_correction")
        self.assertEqual(pre_val_update["next_step"], "self_correction")
        self.assertFalse(pre_val_update["observations"][0].passed)

        state.observations = pre_val_update["observations"]
        state.status = pre_val_update["status"]
        state.next_step = pre_val_update["next_step"]

        self.assertEqual(route_after_validation(state), "self_correction")

        # 2. Self-correction node pass
        correction_update = frontend_self_correction_node(state)
        self.assertEqual(correction_update["correction_count"], 1)
        self.assertEqual(correction_update["status"], "self_corrected")
        self.assertEqual(correction_update["next_step"], "ts_prevalidation")

        # Apply state updates
        state.artifacts = correction_update["artifacts"]
        state.claims = correction_update["claims"]
        state.correction_count = correction_update["correction_count"]
        state.status = correction_update["status"]

        # Ensure type mismatch was resolved
        self.assertIn("const count: number = 10", state.artifacts["src/components/StatusBadge.tsx"])

        # 3. Second pre-validation run
        second_pre_val_update = ts_prevalidation_node(state)
        self.assertEqual(second_pre_val_update["status"], "ready_for_qa")
        self.assertEqual(second_pre_val_update["next_step"], "qa_handoff")
        self.assertEqual(len(second_pre_val_update["observations"]), 2)
        self.assertTrue(second_pre_val_update["observations"][1].passed)

    def test_max_corrections_bounded_at_one(self):
        """If code fails after 1 self-correction pass, it must NOT loop; routes to qa_handoff as failed."""
        state = FrontendDevState(
            task_id="TASK-103",
            prompt="Generate broken code",
            artifacts=self.invalid_artifacts,
            claims=AgentClaims(files_written=["src/components/StatusBadge.tsx"]),
            max_corrections=1,
            correction_count=1,  # Already used the 1 correction pass
        )

        # Force persistent failure compiler
        mock_compiler = MagicMock()
        mock_compiler.run_compilation.return_value = CompilerObservations(
            passed=False,
            exit_code=1,
            stdout="error TS2322",
            stderr="",
            errors=[
                TSError(
                    file="src/components/StatusBadge.tsx",
                    line=3,
                    column=9,
                    code="TS2322",
                    message="Type mismatch persists",
                    raw="src/components/StatusBadge.tsx(3,9): error TS2322: Type mismatch persists",
                )
            ],
            attempt=2,
            execution_time_ms=12.0,
        )

        update = ts_prevalidation_node(state, compiler=mock_compiler)
        self.assertEqual(update["status"], "validation_failed")
        self.assertEqual(update["next_step"], "qa_handoff")

        state.status = update["status"]
        self.assertEqual(route_after_validation(state), "qa_handoff")

    def test_std_aud_1_claims_and_observations_strict_separation(self):
        """Ensure claims and observations never share a field or mutate each other."""
        state = FrontendDevState(
            task_id="TASK-AUD-1",
            prompt="Verify audit separation",
            artifacts=self.valid_artifacts,
        )

        self.assertIsInstance(state.claims, AgentClaims)
        self.assertIsInstance(state.observations, list)

        # Agent claim additions
        state.claims.files_written.append("src/components/NewCard.tsx")
        state.claims.claimed_fixes.append("Fixed prop types")

        # System compiler observation additions
        obs = CompilerObservations(
            passed=True,
            exit_code=0,
            stdout="",
            stderr="",
            errors=[],
            attempt=1,
            execution_time_ms=5.0,
        )
        state.observations.append(obs)

        # Confirm fields do not bleed into each other
        claims_dict = state.claims.model_dump()
        obs_dict = state.observations[0].model_dump()

        for key in claims_dict.keys():
            self.assertNotIn(key, obs_dict, f"Collision detected: field {key} in both claims and observations")

        self.assertEqual(state.claims.files_written, ["src/components/NewCard.tsx"])
        self.assertEqual(state.observations[0].exit_code, 0)


if __name__ == "__main__":
    unittest.main()

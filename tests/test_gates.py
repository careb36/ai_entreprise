import pytest
from unittest.mock import patch, MagicMock
from src.verification.gates import (
    Artifact,
    GraphState,
    artifact_verification_node
)

def test_python_valid_artifact():
    """Test that valid Python code passes the verification gate."""
    code = "def hello_world():\n    return 'Hello, La Fabrique!'\n"
    artifact = Artifact(
        id="py-valid-1", 
        language="python", 
        content=code, 
        claims={"status": "complete"}
    )
    state: GraphState = {"artifacts": [artifact], "validation_errors": []}
    
    new_state = artifact_verification_node(state)
    
    assert len(new_state["validation_errors"]) == 0
    assert new_state["artifacts"][0].observations["syntax_valid"] is True
    # Ensure claims were not overwritten
    assert new_state["artifacts"][0].claims["status"] == "complete"


def test_python_invalid_artifact():
    """Test that malformed Python code is caught and logged as an observation."""
    code = "def hello_world()\n    return 'Missing colon!'\n"
    artifact = Artifact(
        id="py-invalid-1", 
        language="python", 
        content=code,
        claims={"status": "complete"}
    )
    state: GraphState = {"artifacts": [artifact], "validation_errors": []}
    
    new_state = artifact_verification_node(state)
    
    assert len(new_state["validation_errors"]) == 1
    assert "SyntaxError" in new_state["validation_errors"][0]
    assert new_state["artifacts"][0].observations["syntax_valid"] is False
    assert "SyntaxError" in new_state["artifacts"][0].observations["syntax_error"]


@patch("src.verification.gates.subprocess.run")
def test_typescript_valid_artifact(mock_run):
    """Test that valid TypeScript code passes the verification gate."""
    # Mocking subprocess to avoid dependency on Node.js in the test runner
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    
    code = "const target: number = 100;\nconsole.log(target);"
    artifact = Artifact(
        id="ts-valid-1", 
        language="typescript", 
        content=code,
        claims={"tests_passed": True}
    )
    state: GraphState = {"artifacts": [artifact], "validation_errors": []}
    
    new_state = artifact_verification_node(state)
    
    assert len(new_state["validation_errors"]) == 0
    assert new_state["artifacts"][0].observations["syntax_valid"] is True


@patch("src.verification.gates.subprocess.run")
def test_typescript_invalid_artifact(mock_run):
    """Test that malformed TypeScript code is caught and logged as an observation."""
    mock_run.return_value = MagicMock(returncode=1, stderr="SyntaxError: Unexpected token")
    
    code = "const target: number = ;\nconsole.log(target);"
    artifact = Artifact(
        id="ts-invalid-1", 
        language="typescript", 
        content=code
    )
    state: GraphState = {"artifacts": [artifact], "validation_errors": []}
    
    new_state = artifact_verification_node(state)
    
    assert len(new_state["validation_errors"]) == 1
    assert "SyntaxError: Unexpected token" in new_state["validation_errors"][0]
    assert new_state["artifacts"][0].observations["syntax_valid"] is False
    assert "SyntaxError" in new_state["artifacts"][0].observations["syntax_error"]


def test_unsupported_language_artifact():
    """Test that unsupported languages are gracefully rejected."""
    artifact = Artifact(
        id="unknown-1", 
        language="ruby", 
        content="puts 'Hello'"
    )
    state: GraphState = {"artifacts": [artifact], "validation_errors": []}
    
    new_state = artifact_verification_node(state)
    
    assert len(new_state["validation_errors"]) == 1
    assert "Unsupported language" in new_state["validation_errors"][0]
    assert new_state["artifacts"][0].observations["syntax_valid"] is False

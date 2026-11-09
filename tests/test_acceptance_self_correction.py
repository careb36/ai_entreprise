import pytest
from langgraph.graph import StateGraph, END
from typing import List
from src.schemas.state import GraphState
from src.la_fabrique.graph_builder import route_preflight_results, setup_self_correction_graph
from src.nodes.dev_agent_node import dev_agent_node

def mock_preflight_check_node(state: GraphState) -> GraphState:
    """
    Mock preflight check that fails the first time and passes the second time,
    simulating the behavior of the actual AST/Linter validation node.
    """
    artifact = state.get("artifact", "")
    
    # If the artifact doesn't contain the fix, fail it
    if "Fixed errors" not in artifact and state.get("correction_attempts", 0) == 0:
        return {
            "artifact": artifact,
            "validation_errors": ["SyntaxError on line 1", "Missing type hint"],
            "correction_attempts": state.get("correction_attempts", 0),
            "max_correction_attempts": state.get("max_correction_attempts", 3)
        }
        
    # Otherwise, pass
    return {
        "artifact": artifact,
        "validation_errors": [],
        "correction_attempts": state.get("correction_attempts", 0),
        "max_correction_attempts": state.get("max_correction_attempts", 3)
    }

def test_acceptance_dev_self_correction_loop():
    """
    Acceptance Test: Dev agent successfully parses the error report and submits 
    a corrected artifact without human or QA intervention.
    Adheres to STD-QA-1 (No skipped tests, exact naming convention).
    """
    # 1. Setup Graph
    workflow = StateGraph(GraphState)
    workflow.add_node("dev_agent", dev_agent_node)
    workflow.add_node("preflight_check", mock_preflight_check_node)
    workflow.set_entry_point("dev_agent")
    
    # 2. Add edges for self-correction
    setup_self_correction_graph(workflow)
    app = workflow.compile()
    
    # 3. Run the graph
    initial_state = {
        "artifact": "def foo(): pass",
        "validation_errors": [],
        "correction_attempts": 0,
        "max_correction_attempts": 3
    }
    
    # Execute the graph
    result = app.invoke(initial_state)
    
    # 4. Verify self-correction occurred
    assert result["correction_attempts"] == 1, "Dev agent should have attempted correction exactly once"
    assert len(result["validation_errors"]) == 0, "Validation errors should be resolved after correction"
    assert "Fixed errors: SyntaxError on line 1, Missing type hint" in result["artifact"], \
        "Dev agent should parse error report and include the fix in the corrected artifact"

def test_acceptance_max_correction_attempts_exceeded():
    """
    Acceptance Test: If the Dev agent fails to correct the artifact within the maximum
    allowed attempts, the loop terminates to prevent infinite loops.
    """
    def strictly_failing_preflight(state: GraphState) -> GraphState:
        return {
            "artifact": state.get("artifact", ""),
            "validation_errors": ["Persistent Error"],
            "correction_attempts": state.get("correction_attempts", 0),
            "max_correction_attempts": state.get("max_correction_attempts", 3)
        }
        
    workflow = StateGraph(GraphState)
    workflow.add_node("dev_agent", dev_agent_node)
    workflow.add_node("preflight_check", strictly_failing_preflight)
    workflow.set_entry_point("dev_agent")
    
    setup_self_correction_graph(workflow)
    app = workflow.compile()
    
    initial_state = {
        "artifact": "def foo(): pass",
        "validation_errors": [],
        "correction_attempts": 0,
        "max_correction_attempts": 2
    }
    
    result = app.invoke(initial_state)
    
    assert result["correction_attempts"] == 2, "Should strictly stop after max attempts"
    assert len(result["validation_errors"]) == 1, "Should still have errors recorded in state"

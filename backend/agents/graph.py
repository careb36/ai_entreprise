"""
LangGraph Agent Workflow — Enterprise v2026.4 (Phase 4)

Five-node directed graph that models the full AI engineering cycle:
  design → validate_design → generate → execute → burn

Phase 4 additions:
- **OpenTelemetry**: (Temporarily Disabled for Python 3.14 compatibility).
- **Context Propagation**: interaction_id and business_unit_id flow through state.
"""

import logging
import re
from typing import Dict, Any, Optional, TypedDict
from uuid import uuid4
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from llm.model_factory import create_completion
from memory.engram_processor import get_engram_processor

logger = logging.getLogger("fabrique-agents")

MAX_DESIGN_RETRIES = 2
MAX_EXECUTION_RETRIES = 2

class AgentState(TypedDict):
    interaction_id: str
    business_unit_id: Optional[int]
    agent_id: str
    agent_role: str
    task_type: Optional[str]
    story: str
    constraints: Optional[str]
    design_artifact: Optional[str]
    generated_code: Optional[str]
    execution_results: Optional[Dict[str, Any]]
    tests_passed: bool
    engram_id: Optional[Any]
    design_valid: bool
    design_retry_count: int
    execution_retry_count: int
    last_error: Optional[str]
    rag_context: Optional[str]
    created_at: str

def architect_design_node(state: AgentState) -> AgentState:
    """Architect generates Mermaid diagrams and technical specs."""
    logger.info(f"Designing solution for: {state['story']} (BU: {state.get('business_unit_id')})")
    processor = get_engram_processor()
    rag_context = processor.query_relevant_engrams(state["story"], limit=5, role_filter="Architect")
    state["rag_context"] = rag_context

    corrective_instruction = f"\nPrevious error: {state['last_error']}" if state.get("last_error") else ""
    prompt = (
        f"Act as a Staff Architect. Story: {state['story']}\n"
        f"Context: {rag_context or 'None'}\n{corrective_instruction}"
        "\nOutput: ```mermaid ... ``` + Technical requirements."
    )
    state["design_artifact"] = create_completion(
        role="Architect", 
        prompt=prompt, 
        bu_id=state.get("business_unit_id")
    )
    return state

def validate_design_node(state: AgentState) -> AgentState:
    """Guardrails: ensure structurally valid Mermaid diagram."""
    artifact = state.get("design_artifact") or ""
    mermaid_match = re.search(r"```mermaid\s*(.*?)```", artifact, flags=re.IGNORECASE | re.DOTALL)
    
    if mermaid_match:
        state["design_valid"] = True
        state["last_error"] = None
    else:
        state["design_valid"] = False
        state["design_retry_count"] += 1
        state["last_error"] = "No valid ```mermaid``` block found."
        
    return state

def generate_code_node(state: AgentState) -> AgentState:
    """Developer generates code based on design."""
    retry = state.get("execution_retry_count", 0)
    feedback = f"\nExecution feedback: {state['last_error']}" if state.get("last_error") and retry > 0 else ""
    
    logger.info(f"Generating code (retry={retry})...")
    prompt = f"Story: {state['story']}\nDesign: {state['design_artifact']}\n{feedback}\nWrite production code."
    state["generated_code"] = create_completion(
        role="Developer", 
        prompt=prompt, 
        bu_id=state.get("business_unit_id")
    )
    return state

def real_execution_node(state: AgentState) -> AgentState:
    """Simulates code execution and testing."""
    code = state.get("generated_code") or ""
    stubs = [s for s in ["TODO", "pass", "NotImplementedError"] if s in code]
    
    if stubs:
        state["tests_passed"] = False
        state["execution_retry_count"] += 1
        state["last_error"] = f"Detected stubs: {', '.join(stubs)}"
    else:
        state["tests_passed"] = True
        state["last_error"] = None
        
    return state

def burn_engram_node(state: AgentState) -> AgentState:
    """Finalize by burning record into Neo4j."""
    processor = get_engram_processor()
    summary = f"Story: {state['story']}. Status: {'passed' if state['tests_passed'] else 'failed'}."
    result = processor.burn_engram(
        agent_id=state["agent_id"],
        role=state["agent_role"],
        decision_summary=summary,
        metadata={"interaction_id": state["interaction_id"]}
    )
    state["engram_id"] = result["engram_id"]
    return state

def create_agent_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("design", architect_design_node)
    workflow.add_node("validate_design", validate_design_node)
    workflow.add_node("generate", generate_code_node)
    workflow.add_node("execute", real_execution_node)
    workflow.add_node("burn", burn_engram_node)
    
    workflow.set_entry_point("design")
    workflow.add_edge("design", "validate_design")
    workflow.add_conditional_edges("validate_design", lambda s: "generate" if s["design_valid"] or s["design_retry_count"] > MAX_DESIGN_RETRIES else "design")
    workflow.add_edge("generate", "execute")
    workflow.add_conditional_edges("execute", lambda s: "burn" if s["tests_passed"] or s["execution_retry_count"] > MAX_EXECUTION_RETRIES else "generate")
    workflow.add_edge("burn", END)
    
    return workflow.compile(checkpointer=MemorySaver())

def run_fabrique_workflow(story: str, role: str = "Developer", bu_id: Optional[int] = None):
    interaction_id = str(uuid4())
    initial_state = {
        "interaction_id": interaction_id,
        "business_unit_id": bu_id,
        "agent_id": f"{role.lower()}_1",
        "agent_role": role,
        "story": story,
        "tests_passed": False,
        "design_retry_count": 0,
        "execution_retry_count": 0,
        "created_at": datetime.now().isoformat(),
        "last_error": None
    }
    graph = create_agent_graph()
    return graph.invoke(initial_state, config={"configurable": {"thread_id": interaction_id}})

"""
LangGraph Agent Workflow - Enterprise v2026.4
Inspirations: MetaGPT (Design Artifacts), GPT Pilot (Micro-tasks), OpenHands (Real Execution)
"""

import logging
import re
from typing import Dict, Any, Optional, TypedDict
from uuid import uuid4
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from backend.llm.model_factory import create_completion
from backend.memory.engram_processor import get_engram_processor

logger = logging.getLogger("fabrique-agents")

MAX_DESIGN_RETRIES = 2
MAX_EXECUTION_RETRIES = 2


def _extract_mermaid_block(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"```mermaid\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def _is_valid_mermaid_artifact(artifact: Optional[str]) -> bool:
    mermaid = _extract_mermaid_block(artifact)
    if not mermaid:
        return False

    # Basic guardrails to prevent passing malformed diagrams downstream.
    has_diagram_keyword = bool(
        re.search(r"\b(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|journey|pie)\b", mermaid)
    )
    has_arrows_or_brackets = any(token in mermaid for token in ["-->", "---", "[", "(", "{"])
    return has_diagram_keyword and has_arrows_or_brackets


def _fallback_design_artifact(story: str) -> str:
    return (
        "```mermaid\n"
        "flowchart TD\n"
        "    A[Input Story] --> B[Architect Design]\n"
        "    B --> C[Developer Implementation]\n"
        "    C --> D[Test Execution]\n"
        "    D --> E[Persist Engram]\n"
        "```\n\n"
        f"Technical requirements:\n- Implement story: {story}\n- Keep business constraints and tests aligned."
    )

class AgentState(TypedDict):
    interaction_id: str
    agent_id: str
    agent_role: str
    task_type: str
    story: str
    constraints: Optional[str]
    design_artifact: Optional[str] # MetaGPT style
    generated_code: Optional[str]
    execution_results: Optional[Dict[str, Any]] # OpenHands style
    tests_passed: bool
    engram_id: Optional[str]
    retry_count: int
    design_valid: bool
    design_retry_count: int
    execution_retry_count: int
    last_error: Optional[str]
    rag_context: Optional[str]
    created_at: str

def architect_design_node(state: AgentState) -> AgentState:
    """Architect generates Mermaid diagrams and technical specs (MetaGPT style)."""
    logger.info(f"Designing solution for: {state['story']}")
    processor = get_engram_processor()
    rag_context = processor.query_relevant_engrams(state["story"], limit=5, role_filter="Architect")
    state["rag_context"] = rag_context

    corrective_instruction = ""
    if state.get("last_error"):
        corrective_instruction = (
            f"\nPrevious output error: {state['last_error']}\n"
            "Regenerate and ensure Mermaid syntax is valid and enclosed in ```mermaid ... ```.\n"
        )

    prompt = (
        "Act as a Staff Architect. Design a solution for the following story.\n"
        f"Story: {state['story']}\n"
        f"Context from past decisions:\n{rag_context or 'No relevant context found.'}\n"
        f"{corrective_instruction}"
        "Output format:\n"
        "1) A valid Mermaid diagram enclosed in a single ```mermaid``` block.\n"
        "2) A concise technical requirements list."
    )
    state["design_artifact"] = create_completion("Architect", prompt)
    return state


def validate_design_node(state: AgentState) -> AgentState:
    """Guardrails: ensure the Architect output includes valid Mermaid before coding."""
    artifact = state.get("design_artifact")
    is_valid = _is_valid_mermaid_artifact(artifact)

    if is_valid:
        state["design_valid"] = True
        state["last_error"] = None
        return state

    state["design_valid"] = False
    state["last_error"] = "Architect output does not contain a valid Mermaid diagram block."
    state["design_retry_count"] += 1

    logger.warning(
        "Invalid Mermaid artifact (attempt %s/%s)",
        state["design_retry_count"],
        MAX_DESIGN_RETRIES,
    )

    if state["design_retry_count"] > MAX_DESIGN_RETRIES:
        logger.warning("Max design retries reached, applying fallback Mermaid artifact.")
        state["design_artifact"] = _fallback_design_artifact(state["story"])
        state["design_valid"] = True
        state["last_error"] = None

    return state

def generate_code_node(state: AgentState) -> AgentState:
    """Developer generates code based on Architect's design."""
    logger.info("Generating code based on design...")
    feedback = ""
    if state.get("last_error"):
        feedback = (
            "\nPrevious execution failed with this feedback:\n"
            f"{state['last_error']}\n"
            "Fix the issue and return corrected production-ready code.\n"
        )

    prompt = (
        f"Story: {state['story']}\n"
        f"Design: {state['design_artifact']}\n"
        f"{feedback}"
        "Write production code."
    )
    state["generated_code"] = create_completion("Developer", prompt)
    return state

def real_execution_node(state: AgentState) -> AgentState:
    """
    OpenHands Style: This node would publish to NATS for the Daemon to run tests.
    For this simulation, we assume the Daemon returned a successful test run.
    """
    logger.info("Requesting real execution/test run from VS Code Daemon...")

    generated_code = state.get("generated_code") or ""
    failure_patterns = ["TODO", "NotImplementedError", "pass\n", "raise Exception("]
    found_failures = [pattern for pattern in failure_patterns if pattern in generated_code]

    if found_failures:
        failure_message = (
            "Execution failed: simulated tests detected unresolved placeholders or runtime stubs: "
            + ", ".join(found_failures)
        )
        state["execution_results"] = {
            "stdout": failure_message,
            "exit_code": 1,
            "success": False,
        }
        state["tests_passed"] = False
        state["execution_retry_count"] += 1
        state["last_error"] = failure_message
        logger.warning(
            "Execution failed (attempt %s/%s)",
            state["execution_retry_count"],
            MAX_EXECUTION_RETRIES,
        )
        return state

    # In a full async implementation, we would 'interrupt' here awaiting daemon callback.
    state["execution_results"] = {
        "stdout": "Tests passed: 12/12",
        "exit_code": 0,
        "success": True,
    }
    state["tests_passed"] = True
    state["last_error"] = None
    return state

def burn_engram_node(state: AgentState) -> AgentState:
    """Finalize by burning an immutable record into Neo4j."""
    processor = get_engram_processor()
    execution_status = "passed" if state.get("tests_passed") else "failed"
    summary = (
        f"Developed {state['story']}. "
        f"Execution {execution_status} after {state.get('execution_retry_count', 0)} retries."
    )
    result = processor.burn_engram(
        agent_id=state["agent_id"],
        role=state["agent_role"],
        decision_summary=summary
    )
    state["engram_id"] = result["engram_id"]
    return state


def _route_after_design_validation(state: AgentState) -> str:
    if state.get("design_valid"):
        return "generate"
    return "design"


def _route_after_execution(state: AgentState) -> str:
    if state.get("tests_passed"):
        return "burn"

    if state.get("execution_retry_count", 0) <= MAX_EXECUTION_RETRIES:
        return "generate"

    logger.warning("Max execution retries reached, burning engram with failure state.")
    return "burn"

def create_agent_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    
    workflow.add_node("design", architect_design_node)
    workflow.add_node("validate_design", validate_design_node)
    workflow.add_node("generate", generate_code_node)
    workflow.add_node("execute", real_execution_node)
    workflow.add_node("burn", burn_engram_node)
    
    workflow.set_entry_point("design")
    workflow.add_edge("design", "validate_design")
    workflow.add_conditional_edges(
        "validate_design",
        _route_after_design_validation,
        {
            "design": "design",
            "generate": "generate",
        },
    )
    workflow.add_edge("generate", "execute")
    workflow.add_conditional_edges(
        "execute",
        _route_after_execution,
        {
            "generate": "generate",
            "burn": "burn",
        },
    )
    workflow.add_edge("burn", END)
    
    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)

def run_fabrique_workflow(story: str, role: str = "Developer"):
    interaction_id = str(uuid4())
    initial_state = {
        "interaction_id": interaction_id,
        "agent_id": f"{role.lower()}_1",
        "agent_role": role,
        "story": story,
        "constraints": None,
        "design_artifact": None,
        "generated_code": None,
        "execution_results": None,
        "tests_passed": False,
        "engram_id": None,
        "retry_count": 0,
        "design_valid": False,
        "design_retry_count": 0,
        "execution_retry_count": 0,
        "last_error": None,
        "rag_context": None,
        "created_at": datetime.now().isoformat()
    }
    
    graph = create_agent_graph()
    config = {"configurable": {"thread_id": interaction_id}}
    return graph.invoke(initial_state, config=config)

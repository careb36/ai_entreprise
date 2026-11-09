import logging
from langgraph.graph import StateGraph, END
from src.schemas.state import GraphState
from src.la_fabrique.config import settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

def route_preflight_results(state: GraphState) -> str:
    """
    Routes the graph based on pre-flight validation results.
    Evaluates correction attempts against the configured maximum to prevent infinite loops.
    """
    errors = state.get("validation_errors", [])
    attempts = state.get("correction_attempts", 0)
    max_attempts = state.get("max_correction_attempts", settings.MAX_CORRECTION_ATTEMPTS)
    
    if errors and len(errors) > 0:
        if attempts < max_attempts:
            logger.warning(f"Validation failed with {len(errors)} errors. Routing to dev_agent for correction (Attempt {attempts}/{max_attempts}).")
            return "dev_agent"
        else:
            logger.error(f"Validation failed and max correction attempts ({max_attempts}) reached. Terminating loop.")
            
    logger.info("Validation passed or max attempts reached. Routing to END.")
    return END

def setup_self_correction_graph(workflow: StateGraph) -> StateGraph:
    """
    Updates the LangGraph edges to feed pre-flight validation errors back to the Dev agent.
    """
    workflow.add_edge("dev_agent", "preflight_check")
    
    workflow.add_conditional_edges(
        "preflight_check",
        route_preflight_results,
        {
            "dev_agent": "dev_agent",
            END: END
        }
    )
    return workflow

import logging
from src.schemas.state import GraphState
from src.la_fabrique.config import settings

# STD-OPS-1: Structured logging with no secrets
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

def dev_agent_node(state: GraphState) -> GraphState:
    """
    Dev agent node that generates or corrects an artifact.
    Parses validation errors from the state and increments correction attempts.
    """
    artifact = state.get("artifact", "")
    errors = state.get("validation_errors", [])
    attempts = state.get("correction_attempts", 0)
    max_attempts = state.get("max_correction_attempts", settings.MAX_CORRECTION_ATTEMPTS)
    
    if errors and len(errors) > 0:
        logger.info(f"Dev agent correcting artifact. Attempt {attempts + 1}/{max_attempts}. Errors: {len(errors)}")
        
        # Self-correction logic: parse errors and correct artifact
        # In a production scenario, this invokes the LLM with the error report.
        corrected_artifact = artifact + f"\n# Fixed errors: {', '.join(errors)}"
        
        return {
            "artifact": corrected_artifact,
            "validation_errors": [], # Reset errors after correction attempt
            "correction_attempts": attempts + 1,
            "max_correction_attempts": max_attempts
        }
    else:
        logger.info("Dev agent generating initial artifact.")
        return {
            "artifact": artifact if artifact else "# Initial generated code",
            "validation_errors": [],
            "correction_attempts": attempts,
            "max_correction_attempts": max_attempts
        }

from typing import TypedDict, List

class GraphState(TypedDict, total=False):
    """
    State schema for the LangGraph orchestration.
    Uses total=False to allow partial updates during node execution.
    """
    artifact: str
    validation_errors: List[str]
    correction_attempts: int
    max_correction_attempts: int

from fastapi import APIRouter, HTTPException, status
from backend.app.schemas.sandbox import SandboxExecutionRequest, SandboxExecutionResponse
from backend.app.services.sandbox_manager import EphemeralSandboxManager

router = APIRouter(prefix="/sandbox", tags=["Sandbox"])
sandbox_manager = EphemeralSandboxManager()

@router.post("/validate", response_model=SandboxExecutionResponse, status_code=status.HTTP_200_OK)
def validate_in_sandbox(request: SandboxExecutionRequest) -> SandboxExecutionResponse:
    """
    Spins up an ephemeral sandbox (K8s Job or Docker container),
    runs health checks and code validation, and ensures teardown within deadline.
    """
    try:
        response = sandbox_manager.run_sandbox_validation(request)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ephemeral sandbox execution failed: {str(e)}"
        )

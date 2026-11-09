from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class SandboxExecutionRequest(BaseModel):
    code: str = Field(..., description="Source code payload to execute and validate in sandbox")
    filename: str = Field(default="script.py", description="Target filename inside sandbox")
    timeout_seconds: int = Field(default=30, ge=1, le=120, description="Strict sandbox execution timeout in seconds")
    mode: str = Field(default="k8s", description="Execution mode: 'k8s' or 'docker'")

class SandboxHealthResult(BaseModel):
    status: str = Field(..., description="Status of the sandbox endpoint (ok, error)")
    sandbox: str = Field(..., description="Sandbox operational status")
    pid: Optional[int] = Field(default=None, description="Process ID inside sandbox")

class SandboxExecutionResponse(BaseModel):
    sandbox_id: str = Field(..., description="Unique identifier of the ephemeral sandbox instance")
    success: bool = Field(..., description="Whether execution and health check succeeded")
    execution_time_seconds: float = Field(..., description="Total time taken from spin up to teardown")
    stdout: str = Field(default="", description="Output logs from sandbox execution")
    stderr: str = Field(default="", description="Error logs from sandbox execution")
    health: Optional[Dict[str, Any]] = Field(default=None, description="Health check result from sandbox validation endpoint")
    error: Optional[str] = Field(default=None, description="Error message if sandbox execution or teardown failed")
    timed_out: bool = Field(default=False, description="Flag indicating if execution hit strict timeout")

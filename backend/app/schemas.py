from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class CompanyInitRequest(BaseModel):
    company_name: str
    default_sprint_length_days: int = 14
    autonomy_level: int = 2
    agents: List[Dict[str, str]] = []
    permissions: Optional[Dict[str, str]] = None

class SprintStartRequest(BaseModel):
    company_id: int
    sprint_name: str
    start_date: str
    duration_days: int
    backlog_items: List[Dict[str, str]]

class GenerateRequest(BaseModel):
    company_id: int
    story: str
    constraints: Optional[str] = None
    interaction_id: Optional[str] = None

class AuditRequest(BaseModel):
    interaction_id: Optional[str] = None
    agent_id: Optional[str] = None
    action: str
    details: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    decision_signature: Optional[str] = None
    cost_tokens: int = 0

class DiscoveryRequest(BaseModel):
    company_id: int
    project_path: str

class ApprovalRequest(BaseModel):
    task_id: int
    approver_name: str
    decision: str  # 'approved' or 'rejected'

class BugReportRequest(BaseModel):
    user_email: str
    description: str
    severity: str = "medium"
    context_data: Optional[Dict[str, Any]] = None

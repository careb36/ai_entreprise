from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


# ---------------------------------------------------------------------------
# Request schemas (Pydantic — not persisted directly, used for API validation)
# ---------------------------------------------------------------------------

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


class UserCreateRequest(BaseModel):
    email: str
    full_name: str
    password: str


class InviteMemberRequest(BaseModel):
    company_id: int
    email: str
    human_role: str  # 'Owner', 'Manager', 'Viewer'


class BacklogItemUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    comment: Optional[str] = None  # appends a new comment


class BugReportRequest(BaseModel):
    title: str
    description: str
    steps: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    severity: str = "medium"        # low | medium | high | critical
    category: str = "bug"           # bug | feature | question | incident
    reporter_email: Optional[str] = None
    browser_info: Optional[Dict[str, Any]] = None


class BugStatusUpdateRequest(BaseModel):
    status: str          # open | in_progress | waiting | resolved | closed
    resolution: Optional[str] = None
    assigned_to: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class SupportChatRequest(BaseModel):
    messages: List[ChatMessage]


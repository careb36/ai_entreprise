"""
Pydantic request/response schemas for the Fabrique Enterprise API.

These models are used **exclusively for API validation and serialisation**.
They are intentionally separate from the SQLModel ORM models in ``models/``
to avoid coupling the persistence layer to the HTTP contract.

Naming convention:
- ``*Request``  — inbound request bodies (POST / PATCH payloads).
- ``*Update``   — partial update bodies (PATCH payloads).
- ``Chat*``     — support-chat specific schemas.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


# ---------------------------------------------------------------------------
# Request schemas (Pydantic — not persisted directly, used for API validation)
# ---------------------------------------------------------------------------

class CompanyInitRequest(BaseModel):
    """Payload for ``POST /v1/init_company`` — bootstraps a new tenant."""
    company_name: str
    description: Optional[str] = None              # Human-readable factory description
    default_sprint_length_days: int = 14
    autonomy_level: int = 2                         # 1 = human-led, 3 = fully autonomous
    timezone: str = "UTC"                           # IANA timezone (e.g. "America/Sao_Paulo")
    agents: List[Dict[str, Any]] = []               # List of agent role/persona/budget definitions
    permissions: Optional[Dict[str, str]] = None    # credential_type → access_level
    governance: Optional[Dict[str, bool]] = None    # Governance gate flags (e.g. require_approval_deploy)
    project_path: Optional[str] = None              # Filesystem path scanned during discovery
    detected_stack: Optional[Dict[str, Any]] = None # Stack analysis from discovery phase


class SprintStartRequest(BaseModel):
    """Payload for ``POST /v1/start_sprint`` — creates a sprint with initial backlog."""
    company_id: int
    sprint_name: str
    start_date: str   # ISO-8601 date string (e.g. "2026-04-01")
    duration_days: int
    backlog_items: List[Dict[str, str]]  # Each item has at least "title" and "description"


class GenerateRequest(BaseModel):
    """Payload for ``POST /v1/generate`` — triggers the LangGraph agent workflow."""
    company_id: int
    business_unit_id: Optional[int] = None  # Impute costs to this unit (default if None)
    story: str                           # Natural-language user story
    constraints: Optional[str] = None   # Optional technical / business constraints
    interaction_id: Optional[str] = None  # Client-supplied UUID for idempotency


class AuditRequest(BaseModel):
    """Payload for ``POST /v1/audit`` — records an agent decision to the audit log."""
    interaction_id: Optional[str] = None
    agent_id: Optional[str] = None
    action: str                                  # Short action label (e.g. "generate_code")
    details: Optional[Dict[str, Any]] = None     # Arbitrary context for the action
    result: Optional[Dict[str, Any]] = None      # Outcome data
    decision_signature: Optional[str] = None     # Cryptographic hash for non-repudiation
    cost_tokens: int = 0                         # Token cost billed to the business unit


class DiscoveryRequest(BaseModel):
    """Payload for ``POST /v1/discover_team`` — analyses a project path."""
    project_path: str  # Absolute or relative path to the project root
    company_id: Optional[int] = None  # Optional for pre-creation discovery


class ApprovalRequest(BaseModel):
    """Payload for ``POST /v1/approve_task`` — human governance action."""
    task_id: int
    approver_name: str
    decision: str  # "approved" | "rejected"
    feedback: Optional[str] = None



class UserCreateRequest(BaseModel):
    """Payload for ``POST /v1/auth/register`` — creates a human platform user."""
    email: str
    full_name: str
    password: str  # Plain-text; hashed with bcrypt before storage


class InviteMemberRequest(BaseModel):
    """Payload for ``POST /v1/company/invite`` — adds a user to a company."""
    company_id: int
    email: str
    human_role: str  # "Owner" | "Manager" | "Viewer"


class BacklogItemUpdate(BaseModel):
    """Payload for ``PATCH /v1/backlog/{item_id}`` — partial backlog item update."""
    status: Optional[str] = None      # See VALID_STATUSES in sprint router
    priority: Optional[str] = None    # See VALID_PRIORITIES in sprint router
    assignee: Optional[str] = None
    comment: Optional[str] = None     # When provided, a new comment is appended


class BugReportRequest(BaseModel):
    """Payload for ``POST /v1/support/report_bug`` — creates a support ticket."""
    title: str
    description: str
    steps: Optional[str] = None       # Reproduction steps
    expected: Optional[str] = None    # Expected behaviour
    actual: Optional[str] = None      # Actual (buggy) behaviour
    severity: str = "medium"          # "low" | "medium" | "high" | "critical"
    category: str = "bug"             # "bug" | "feature" | "question" | "incident"
    reporter_email: Optional[str] = None
    browser_info: Optional[Dict[str, Any]] = None  # User-agent, viewport, etc.


class BugStatusUpdateRequest(BaseModel):
    """Payload for ``PATCH /v1/support/tickets/{ticket_num}`` — updates ticket state."""
    status: str                        # "open" | "in_progress" | "waiting" | "resolved" | "closed"
    resolution: Optional[str] = None  # Mandatory when status is "resolved" or "closed"
    assigned_to: Optional[str] = None


class ChatMessage(BaseModel):
    """A single turn in a support-chat conversation."""
    role: str     # "user" | "assistant" | "system"
    content: str  # Message text


class SupportChatRequest(BaseModel):
    """Payload for ``POST /v1/support/chat`` — multi-turn LLM support conversation."""
    messages: List[ChatMessage]  # Full conversation history (oldest first)


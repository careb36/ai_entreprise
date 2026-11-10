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

from datetime import datetime
from typing import Any

from passwords import BCRYPT_MAX_BYTES
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Request schemas (Pydantic — not persisted directly, used for API validation)
# ---------------------------------------------------------------------------


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    company_id: int
    external_ref: str
    producer: str
    model: str | None = None
    agent_role: str | None = None
    subject: dict[str, Any] | None = None
    claims: dict[str, Any] | None = None
    artifact: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentRunVerificationRequest(BaseModel):
    company_id: int
    evidence: dict[str, Any] | None = None


class CompanyInitRequest(BaseModel):
    """Payload for ``POST /v1/init_company`` — bootstraps a new tenant."""

    company_name: str
    description: str | None = None  # Human-readable factory description
    default_scope_size: str = "M"  # "S" | "M" | "L" — default scope for new sprints
    default_sprint_length_days: int | None = None  # deprecated
    autonomy_level: int = 2  # 1 = human-led, 3 = fully autonomous
    timezone: str = "UTC"  # IANA timezone (e.g. "America/Sao_Paulo")
    agents: list[dict[str, Any]] = []  # List of agent role/persona/budget definitions
    permissions: dict[str, str] | None = None  # credential_type → access_level
    governance: dict[str, bool] | None = (
        None  # Governance gate flags (e.g. require_approval_deploy)
    )
    project_path: str | None = None  # Filesystem path scanned during discovery
    detected_stack: dict[str, Any] | None = None  # Stack analysis from discovery phase
    # Start Sprint 0 at once; ``False`` leaves it planned until someone starts it.
    start_now: bool = True
    # The Git workflow: on only when the daemon observes a repository at
    # project_path (services/release/git_setup.py); ``False`` turns it off anyway.
    git_workflow: bool | None = None
    # Where task branches start; empty: what the repository has checked out.
    git_base_branch: str | None = Field(default=None, max_length=200)


# How many tasks a sprint of each scope may hold, and the ceiling for any.
SCOPE_LIMITS: dict[str, int] = {"S": 4, "M": 8, "L": 12}
SCOPE_HARD_MAX = 12


class BacklogItemIn(BaseModel):
    """One task of ``POST /v1/start_sprint``; fields it does not know are kept."""

    model_config = ConfigDict(extra="allow")

    title: str
    description: str = ""
    priority: str = "medium"
    assignee: str = "PO"
    covers: list[str] = []
    depends_on: list[int] = []
    # ``acceptance_tests`` for a QA task that writes a requirement's acceptance
    # tests (``services.acceptance``); anything else is an ordinary task.
    kind: str | None = None


class SprintStartRequest(BaseModel):
    """Payload for ``POST /v1/start_sprint`` — creates a sprint with initial backlog."""

    company_id: int
    sprint_name: str
    start_date: str  # ISO-8601 date string (e.g. "2026-04-01")
    scope_size: str = "M"  # "S" (≤4 items) | "M" (≤8 items) | "L" (≤12 items)
    goal: str | None = None
    duration_days: int | None = None  # deprecated
    # With a specification, "covers" lists the criteria ids (``REQ-1.2``)
    # the task covers; "depends_on" lists the positions, in this list, of
    # the tasks that must be done first (``services.dependencies``).
    backlog_items: list[BacklogItemIn]
    # Blocked tasks of earlier sprints to try again in this one (they count
    # towards the scope). Each gets a fresh task carrying what blocked it.
    carry_over_ids: list[int] = []
    # The approved specification the sprint is planned from; its criteria
    # are the only ones a task may cover.
    specification_id: int | None = None
    # The roles this sprint's tasks may go to, a subset of the company's
    # hired roles (``services/planning/sprint_team.py``); None: every hired role.
    team: list[str] | None = None
    # The PO's suggestion the plan carried, sent back unchanged: the team in
    # automatic mode, and what tells a confirmed team from a person's change.
    suggested_team: list[str] | None = None


class SprintPlanRequest(BaseModel):
    """Payload for ``POST /v1/plan_sprint`` — turns a strategic prompt into a sprint backlog."""

    company_id: int
    prompt: str = ""
    scope_size: str = "M"
    # Plan from an approved specification instead of a prompt: the PO splits
    # it into tasks that name the criteria they cover.
    specification_id: int | None = None
    # Plan only with these roles (the sprint's team a Manager chose).
    team: list[str] | None = None


class SelfImprovementSprintRequest(BaseModel):
    """Payload for ``POST /v1/plan_self_improvement_sprint`` — plans an autonomous improvement sprint."""

    company_id: int
    scope_size: str = "M"
    # Plan only with these roles (the sprint's team a Manager chose).
    team: list[str] | None = None


class GenerateRequest(BaseModel):
    """Payload for ``POST /v1/generate`` — triggers the LangGraph agent workflow."""

    company_id: int
    business_unit_id: int | None = None  # Impute costs to this unit (default if None)
    story: str  # Natural-language user story
    constraints: str | None = None  # Optional technical / business constraints
    interaction_id: str | None = None  # Client-supplied UUID for idempotency


class AuditRequest(BaseModel):
    """Payload for ``POST /v1/audit`` — records an agent decision to the audit log."""

    interaction_id: str | None = None
    agent_id: str | None = None
    action: str  # Short action label (e.g. "generate_code")
    details: dict[str, Any] | None = None  # Arbitrary context for the action
    result: dict[str, Any] | None = None  # Outcome data
    decision_signature: str | None = None  # Deprecated; caller values are ignored
    cost_tokens: int = 0  # Token cost billed to the business unit


class DiscoveryRequest(BaseModel):
    """Payload for ``POST /v1/discover_team`` — analyses a project path."""

    project_path: str  # Absolute or relative path to the project root
    company_id: int | None = None  # Optional for pre-creation discovery


class ReviewCommentCreate(BaseModel):
    """Payload for ``POST /v1/agent_runs/{id}/comments``: a note on a line of a changed file."""

    company_id: int
    path: str | None = Field(default=None, max_length=1000)
    line: int | None = Field(default=None, ge=1)
    side: str = "new"
    body: str = Field(min_length=1, max_length=4000)
    # Only when authentication is off: who wrote it.
    author_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def side_and_body(self) -> "ReviewCommentCreate":
        if self.side not in {"new", "old"}:
            raise ValueError("side must be 'new' or 'old'")
        if self.line is not None and not self.path:
            raise ValueError("a line needs the path of its file")
        self.body = self.body.strip()
        if not self.body:
            raise ValueError("body must not be blank")
        return self


class RunReviewCreate(BaseModel):
    """Payload for ``POST /v1/agent_runs/{id}/reviews``: a submitted review."""

    company_id: int
    event: str
    body: str | None = Field(default=None, max_length=8000)
    author_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def known_event(self) -> "RunReviewCreate":
        if self.event not in {"comment", "approve", "request_changes"}:
            raise ValueError("event must be 'comment', 'approve' or 'request_changes'")
        self.body = (self.body or "").strip() or None
        return self


class AgentRunApprovalRequest(BaseModel):
    """Payload for ``POST /v1/agent_runs/{id}/approve`` — human governance action."""

    company_id: int
    approver_name: str
    decision: str  # "approved" | "rejected"
    feedback: str | None = None
    rejection_category: str | None = None  # "scope" | "quality" | "security" | "other"

    @model_validator(mode="after")
    def feedback_required_on_rejection(self) -> "AgentRunApprovalRequest":
        if self.decision not in {"approved", "rejected"}:
            raise ValueError("decision must be 'approved' or 'rejected'")
        self.approver_name = self.approver_name.strip()
        if not self.approver_name:
            raise ValueError("approver_name must not be empty")
        if self.decision == "rejected" and (
            not self.feedback or not self.feedback.strip()
        ):
            raise ValueError("feedback is required when decision is 'rejected'")
        if self.rejection_category is not None:
            if self.rejection_category not in {"scope", "quality", "security", "other"}:
                raise ValueError("invalid rejection_category")
            if self.decision != "rejected":
                raise ValueError("rejection_category is only allowed with 'rejected'")
        return self


class UserCreateRequest(BaseModel):
    """Payload for ``POST /v1/auth/register`` — creates a human platform user."""

    email: str
    full_name: str
    password: str  # Plain-text; hashed with bcrypt before storage

    @field_validator("password")
    @classmethod
    def _reject_password_beyond_bcrypt_limit(cls, value: str) -> str:
        # bcrypt ignores everything past 72 bytes. Counting bytes, not
        # characters: "ñ" is two. Refused here so a long password is rejected
        # outright instead of being silently shortened to a different one.
        length = len(value.encode("utf-8"))
        if length > BCRYPT_MAX_BYTES:
            raise ValueError(
                f"password must be at most {BCRYPT_MAX_BYTES} bytes in UTF-8 "
                f"(got {length})"
            )
        return value


class LoginRequest(BaseModel):
    """Payload for ``POST /v1/auth/login`` — authenticates a human user."""

    email: str
    password: str


class InviteMemberRequest(BaseModel):
    """Payload for ``POST /v1/company/invite`` — adds a user to a company."""

    company_id: int
    email: str
    human_role: str  # "Owner" | "Manager" | "Viewer"


class BacklogItemUpdate(BaseModel):
    """Payload for ``PATCH /v1/backlog/{item_id}`` — partial backlog item update."""

    status: str | None = None  # See VALID_STATUSES in sprint router
    priority: str | None = None  # See VALID_PRIORITIES in sprint router
    assignee: str | None = None
    comment: str | None = None  # When provided, a new comment is appended


class BugReportRequest(BaseModel):
    """Payload for ``POST /v1/support/report_bug`` — creates a support ticket."""

    title: str
    description: str
    steps: str | None = None  # Reproduction steps
    expected: str | None = None  # Expected behaviour
    actual: str | None = None  # Actual (buggy) behaviour
    # "low" | "medium" | "high" | "critical" and "bug" | "feature" | "question"
    # | "incident": what the reporter chose, or nothing. A default would pass
    # for the reporter's choice.
    severity: str | None = None
    category: str | None = None
    reporter_email: str | None = None
    browser_info: dict[str, Any] | None = None  # User-agent, viewport, etc.


class BugStatusUpdateRequest(BaseModel):
    """Payload for ``PATCH /v1/support/tickets/{ticket_num}`` — updates ticket state."""

    status: str  # "open" | "in_progress" | "waiting" | "resolved" | "closed"
    resolution: str | None = None  # Mandatory when status is "resolved" or "closed"
    assigned_to: str | None = None
    # The support admin's decision; stored apart from the reporter's choice.
    # Omitted keeps the earlier decision; "" clears it.
    severity: str | None = None  # "low" | "medium" | "high" | "critical"
    category: str | None = None  # "bug" | "feature" | "question" | "incident"


class ChatMessage(BaseModel):
    """A single turn in a support-chat conversation."""

    role: str  # "user" | "assistant" | "system"
    content: str  # Message text


class SupportChatRequest(BaseModel):
    """Payload for ``POST /v1/support/chat`` — multi-turn LLM support conversation."""

    messages: list[ChatMessage]  # Full conversation history (oldest first)


# ---------------------------------------------------------------------------
# Orchestration health analytics (GET /v1/analytics/orchestration_health)
# ---------------------------------------------------------------------------


class OrchestrationHealthWeek(BaseModel):
    """Per-week KPI snapshot for the orchestration health endpoint."""

    week: str  # ISO week label, e.g. "2026-W18"
    qa_needs_work_rate: float  # Fraction of QA decisions marked NEEDS_WORK
    avg_developer_tokens: float  # Mean tokens consumed by Developer agent
    architect_consult_count: int  # Number of ARCHITECT_CONSULTED comments
    task_count: int  # Total QA audit rows in the week
    # Developer turns whose token usage the proxy did not report: left out of
    # avg_developer_tokens rather than averaged in as zero.
    developer_turns_without_tokens: int = 0
    post_batch1: bool  # True when the week falls after 2026-05-02


class OrchestrationHealthSummary(BaseModel):
    """Aggregated pre/post Batch-1 averages."""

    qa_needs_work_rate: float
    avg_developer_tokens: float


class OrchestrationHealthSummaries(BaseModel):
    pre_batch1: OrchestrationHealthSummary
    post_batch1: OrchestrationHealthSummary


class OrchestrationHealthResponse(BaseModel):
    """Response shape for ``GET /v1/analytics/orchestration_health``."""

    anchor_commit: str
    anchor_date: str  # ISO-8601 date of the Batch-1 anchor
    weeks: list[OrchestrationHealthWeek]
    summary: OrchestrationHealthSummaries


class AgentCreateRequest(BaseModel):
    """Payload for POST /v1/agents — create an agent for a company."""

    company_id: int
    name: str = ""
    role: str
    persona: str | None = None
    seniority: str = "Senior"
    specialty: str = ""
    model: str = ""
    system_prompt: str | None = None
    token_budget: int = Field(default=500000, ge=0)


class AgentUpdateRequest(BaseModel):
    """Payload for PATCH /v1/agents/{id} — partial agent update."""

    name: str | None = None
    role: str | None = None
    persona: str | None = None
    seniority: str | None = None
    specialty: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    token_budget: int | None = Field(default=None, ge=0)


class AgentPromptRequest(BaseModel):
    """Payload for POST /v1/agents/generate_prompt — auto-generate a system prompt."""

    role: str
    seniority: str = "Senior"
    specialty: str = ""
    persona: str = ""

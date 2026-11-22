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
from typing import Any, Literal

from passwords import check_new_password
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from services.contracts.emails import normalize_email

# Fields that decide who may do what or whose data it is. A request body may
# carry one only where its model declares it (``company_id`` on a route that
# checks the caller's role in that company, ``role`` on an API token); an
# undeclared one is refused with 422 rather than silently dropped, so a client
# that tries to grant itself something learns it cannot (issue #541,
# WSTG-INJT-20 at latest@87cf4a4, mass assignment).
PRIVILEGED_FIELDS = frozenset(
    {
        "company_id",
        "owner_id",
        "user_id",
        "role",
        "human_role",
        "roles",
        "is_admin",
        "admin",
        "support_admin",
        "is_superuser",
        "permissions",
        "created_by",
        "password_hash",
        "api_token_id",
        "member_id",
    }
)


class RequestModel(BaseModel):
    """The base of every JSON request body.

    Unknown fields are ignored, as before, except those in
    ``PRIVILEGED_FIELDS``, which are refused unless the model declares them.
    A model whose clients are known to send exactly its fields (the sign-in
    and membership routes) sets ``extra="forbid"`` and refuses every unknown
    field.
    """

    @model_validator(mode="before")
    @classmethod
    def _refuse_undeclared_privileged_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            declared = set(cls.model_fields)
            declared.update(
                f.alias for f in cls.model_fields.values() if f.alias is not None
            )
            refused = sorted(
                k for k in data if k in PRIVILEGED_FIELDS and k not in declared
            )
            if refused:
                raise ValueError(f"field(s) not accepted here: {', '.join(refused)}")
        return data


STRICT = ConfigDict(extra="forbid")

# ---------------------------------------------------------------------------
# Request schemas (Pydantic — not persisted directly, used for API validation)
# ---------------------------------------------------------------------------


class AgentRunCreate(RequestModel):
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


class AgentRunVerificationRequest(RequestModel):
    company_id: int
    evidence: dict[str, Any] | None = None


class CompanyInitRequest(RequestModel):
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
    # Sprint 0 — Foundations: ``True`` plans it, ``False`` skips it, and
    # ``None`` (default) leaves the decision to the workspace daemon's
    # observation of project_path: a repository with code gets none
    # (retrospective F8; services/loop/foundation_setup.py). Without an
    # observation the sprint is planned, as before. A claim about the folder
    # in this request decides nothing: only the daemon's answer does.
    foundation_sprint: bool | None = None
    # The Git workflow: on only when the daemon observes a repository at
    # project_path (services/release/git_setup.py); ``False`` turns it off anyway.
    git_workflow: bool | None = None
    # Where task branches start; empty: what the repository has checked out.
    git_base_branch: str | None = Field(default=None, max_length=200)
    # The language of the project's documentation packs, from the creator's
    # interface language; empty: English (services/planning/project_docs/texts.py).
    doc_language: Literal["en", "es"] | None = None
    # The Owner's forge policy (audited; empty: ``none``) and whether the daemon
    # fast-forwards the local base to its remote first (issue #745, base_sync.py).
    forge_policy: Literal["none", "fetch", "publish"] | None = None
    sync_base: bool = False


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
    # tests (``services.evidence.acceptance``); anything else is an ordinary task.
    kind: str | None = None


class SprintStartRequest(RequestModel):
    """Payload for ``POST /v1/start_sprint`` — creates a sprint with initial backlog."""

    company_id: int
    sprint_name: str
    start_date: str  # ISO-8601 date string (e.g. "2026-04-01")
    scope_size: str = "M"  # "S" (≤4 items) | "M" (≤8 items) | "L" (≤12 items)
    goal: str | None = None
    objective: str | None = None  # the person's, whole (``Sprint.objective``)
    # A fallback plan's ``plan_id``: the backend reads its own record of it
    # (``services/planning/planner_fallback.py``).
    plan_id: str | None = None
    duration_days: int | None = None  # deprecated
    # With a specification, "covers" lists the criteria ids (``REQ-1.2``)
    # the task covers; "depends_on" lists the positions, in this list, of
    # the tasks that must be done first (``services.tasks.dependencies``).
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
    # Bypass the company's run window (issue #736): starts at once, audited.
    urgent: bool = False


class SprintPlanRequest(RequestModel):
    """Payload for ``POST /v1/plan_sprint`` — turns a strategic prompt into a sprint backlog."""

    company_id: int
    prompt: str = ""
    scope_size: str = "M"
    # Plan from an approved specification instead of a prompt: the PO splits
    # it into tasks that name the criteria they cover.
    specification_id: int | None = None
    # Plan only with these roles (the sprint's team a Manager chose).
    team: list[str] | None = None


class SelfImprovementSprintRequest(RequestModel):
    """Payload for ``POST /v1/plan_self_improvement_sprint`` — plans an autonomous improvement sprint."""

    company_id: int
    scope_size: str = "M"
    # Plan only with these roles (the sprint's team a Manager chose).
    team: list[str] | None = None


class GenerateRequest(RequestModel):
    """Payload for ``POST /v1/generate`` — triggers the LangGraph agent workflow."""

    company_id: int
    business_unit_id: int | None = None  # Impute costs to this unit (default if None)
    story: str  # Natural-language user story
    constraints: str | None = None  # Optional technical / business constraints
    interaction_id: str | None = None  # Client-supplied UUID for idempotency


class AuditRequest(RequestModel):
    """Payload for ``POST /v1/audit`` — records an agent decision to the audit log."""

    interaction_id: str | None = None
    agent_id: str | None = None
    action: str  # Short action label (e.g. "generate_code")
    details: dict[str, Any] | None = None  # Arbitrary context for the action
    result: dict[str, Any] | None = None  # Outcome data
    decision_signature: str | None = None  # Deprecated; caller values are ignored
    cost_tokens: int = 0  # Token cost billed to the business unit


class DiscoveryRequest(RequestModel):
    """Payload for ``POST /v1/discover_team`` — analyses a project path."""

    project_path: str  # Absolute or relative path to the project root
    company_id: int | None = None  # Optional for pre-creation discovery


class ReviewCommentCreate(RequestModel):
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


class RunReviewCreate(RequestModel):
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


class IntegrationCommentCreate(ReviewCommentCreate):
    """Payload for ``POST /v1/sprints/{id}/integration/comments``: a note on a
    line of the integration's diff. The company is the sprint's; the diff and
    its hashes are the daemon's (``services/release/integration_review.py``)."""

    company_id: int | None = None
    # The integration the person read; refused when a newer one replaced it.
    integration_id: int | None = None


class IntegrationReviewCreate(RunReviewCreate):
    """Payload for ``POST /v1/sprints/{id}/integration/reviews``: a submitted review."""

    company_id: int | None = None
    integration_id: int | None = None
    # With ``approve``, for a company that hosts the factory: the protected
    # paths of the diff a signed-in Owner acknowledges by name
    # (``services/release/protected_merge.py``).
    # Up to 600: the daemon shows 300 files and a rename names two paths.
    protected_paths_ack: list[str] | None = Field(default=None, max_length=600)


class IntegrationReviewSetting(RequestModel):
    """Payload for ``PUT /v1/companies/{id}/integration_review`` (Owner)."""

    required: bool


class ForgePolicySetting(RequestModel):
    """Payload for ``PUT /v1/companies/{id}/forge_policy`` (an Owner)."""

    model_config = STRICT

    forge_policy: Literal["none", "fetch", "publish"]


class ProtectedPathsSetting(RequestModel):
    """Payload for ``PUT /v1/companies/{id}/protected_paths`` (a signed-in Owner)."""

    model_config = STRICT

    enabled: bool


class AgentRunApprovalRequest(RequestModel):
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


class UserCreateRequest(RequestModel):
    """Payload for ``POST /v1/auth/register`` — creates a human platform user."""

    model_config = STRICT

    email: str
    full_name: str
    password: str  # Plain-text; hashed with bcrypt before storage

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def _check_password_rule(cls, value: str) -> str:
        # PASSWORD_MIN_LENGTH characters at least, bcrypt's 72 bytes at most
        # (passwords.check_new_password).
        return check_new_password(value)


class LoginRequest(RequestModel):
    """Payload for ``POST /v1/auth/login`` — authenticates a human user."""

    model_config = STRICT

    email: str
    password: str
    # "cookie" (the browser: HttpOnly session cookies, no token in the body)
    # or "bearer" (a script: the access token in the body, no cookies, no
    # refresh; docs/archive/design/SESSIONS.md). Automation should prefer API tokens.
    token_delivery: Literal["cookie", "bearer"] = "cookie"

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return normalize_email(value)


class InviteMemberRequest(RequestModel):
    """Payload for ``POST /v1/company/invite`` — a pending invitation."""

    model_config = STRICT

    company_id: int
    email: str = Field(min_length=3, max_length=320)
    human_role: Literal["Owner", "Manager", "Viewer"]

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        value = normalize_email(value)
        if "@" not in value:
            raise ValueError("email must be an address")
        return value


class InvitationTokenRequest(RequestModel):
    """Payload for ``POST /v1/invitations/accept`` — the token of an invitation."""

    model_config = STRICT

    token: str = Field(min_length=16, max_length=128)


class BacklogItemUpdate(RequestModel):
    """Payload for ``PATCH /v1/backlog/{item_id}`` — partial backlog item update."""

    status: str | None = None  # See VALID_STATUSES in sprint router
    priority: str | None = None  # See VALID_PRIORITIES in sprint router
    assignee: str | None = None
    comment: str | None = None  # When provided, a new comment is appended


class BugReportRequest(RequestModel):
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
    # The company the ticket is about: given, the ticket also enters that
    # company's intake queue (issue #532); the sender must be a member.
    company_id: int | None = None


class BugStatusUpdateRequest(RequestModel):
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


class SupportChatRequest(RequestModel):
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


class GateRefutationWeek(BaseModel):
    week: str  # ISO week, e.g. 2026-W46
    by_problem: dict[str, int]


class GateRefutationsResponse(BaseModel):
    """Response shape for ``GET /v1/analytics/gate_refutations``."""

    company_id: int
    sprint_id: int | None
    since: str  # ISO-8601 date, inclusive
    until: str  # ISO-8601 date, inclusive
    by_problem: dict[str, int]
    refuted: int  # every send-back but ``delivery unconfirmed``
    unconfirmed: int
    weeks: list[GateRefutationWeek]


class MissingModulePeriod(BaseModel):
    """One side of the cutoff. Counts are by source (gate, person, agent_reviewer);
    the text counted is a reviewer's claim, not an observation."""

    since: str  # ISO-8601, inclusive
    until: str  # ISO-8601, exclusive
    days: float
    rework_requests: dict[str, int]
    requests_whose_text_names_missing_module: dict[str, int]


class MissingModuleWeek(BaseModel):
    week: str  # ISO week, e.g. 2026-W46
    period: str  # "before" or "after" the cutoff; a week across it appears twice
    rework_requests: dict[str, int]
    requests_whose_text_names_missing_module: dict[str, int]


class MissingModuleMentionsResponse(BaseModel):
    """Response shape for ``GET /v1/analytics/missing_module_mentions``."""

    company_id: int
    sprint_id: int | None
    counts_what: str  # what the counts mean, and what they do not
    cutoff: str  # ISO-8601 datetime, naive UTC
    cutoff_is_default: bool  # the moment the grounded check (#503) merged
    before: MissingModulePeriod
    after: MissingModulePeriod
    weeks: list[MissingModuleWeek]


class AgentCreateRequest(RequestModel):
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


class AgentUpdateRequest(RequestModel):
    """Payload for PATCH /v1/agents/{id} — partial agent update."""

    name: str | None = None
    role: str | None = None
    persona: str | None = None
    seniority: str | None = None
    specialty: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    token_budget: int | None = Field(default=None, ge=0)


class AgentPromptRequest(RequestModel):
    """Payload for POST /v1/agents/generate_prompt — auto-generate a system prompt."""

    role: str
    seniority: str = "Senior"
    specialty: str = ""
    persona: str = ""

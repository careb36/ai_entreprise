"""
SQLModel ORM models for tenant-level entities.

Tables defined here represent the multi-tenancy core of Fabrique:

- ``Company``          — top-level tenant; owns agents and sprints.
- ``Agent``            — an AI agent belonging to a company.
- ``BusinessUnit``     — cost centre with a token quota per company.
- ``User``             — human platform user (Product Owner, Manager, etc.).
- ``CompanyMember``    — many-to-many join of Users ↔ Companies with a human role.
- ``ProjectCredential``— encrypted API tokens / SSH keys owned by a company.
- ``ProjectDiscovery`` — result of the automatic stack-detection scan.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    """Top-level tenant entity; all sprints and agents belong to a company."""

    __tablename__ = "companies"

    id: int | None = Field(default=None, primary_key=True)
    company_name: str
    default_scope_size: str = "M"  # "S" | "M" | "L" — default scope for new sprints
    default_sprint_length_days: int | None = None  # deprecated
    autonomy_level: int = 2  # 1 = human-led  2 = assisted  3 = autonomous
    # How a sprint's team is chosen (``services/planning/sprint_team.py``): "suggested"
    # (the PO suggests, a Manager confirms) or "automatic" (the PO's suggestion).
    team_selection: str | None = "suggested"
    # Each task's deliveries as commits on its own branch, in a worktree, never
    # in the person's checkout (services/workspace/git_workflow.py); the branch starts
    # from git_base_branch, or from what the repository has checked out.
    git_workflow: bool = True
    git_base_branch: str | None = None
    # Hosts the research tools may read beside the defaults
    # (services/research_tools.py); a Manager writes it.
    research_hosts: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    # Where agent-written code runs for this company (services/sandbox.py):
    # ``host``, ``container`` or ``bwrap``; empty: the deployment's
    # DELIVERY_SANDBOX. The environment variables a sandboxed run may see, by
    # name (the values stay on the daemon's machine); none by default.
    delivery_sandbox: str | None = None
    sandbox_env: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    # The company's overrides of the detected stack profile
    # (services/stack_profiles.py): install, test, lint, start, e2e, port,
    # probe_path; an empty command turns that step off. A Manager writes it.
    stack_profile: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    # The dependency manifests as the last successful install saw them, per
    # folder ({root: {path: sha256}}, workspace/dependency_install.py): written only
    # from the daemon's report, so an install runs when one changed.
    dependency_installs: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    # The models the company's agents run when their own is empty
    # (services/models/company_models.py): one per role ({"QA": "..."}), then one
    # default; empty: the deployment's AGENT_MODEL settings. A Manager writes them.
    default_model: str | None = None
    role_models: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    # The routing policy by task difficulty ({"bands": {"low": ..., "high": ...}},
    # services/models/routing_policy.py); empty: the deployment's
    # AGENT_MODEL_POLICY, else the role defaults. A Manager writes it.
    model_policy: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    # The terminal coding agent that produces a role's tasks instead of the
    # LLM call ({"Developer": "claude"}, services/producers/console_producer.py); it
    # needs the Git workflow. Empty: every role is produced by its model.
    console_producers: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    # The environment variable names only the console producers' runs see
    # (the provider's key, e.g. ANTHROPIC_API_KEY), beside sandbox_env; never
    # the delivery tests, installs or application checks. A Manager writes it.
    console_env: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    # The language the project's documentation packs are written in
    # (``services/planning/project_docs/texts.py``): "en" or "es". Set from the
    # creator's interface language in "New company"; an Owner or Manager changes it.
    doc_language: str | None = None
    # A sprint's integration is merged into the base branch only once a person
    # approved it in a review (services/release/integration_review.py); off by
    # default, so nothing changes for a company that does not ask. An Owner writes it.
    integration_review_required: bool = False


class Agent(SQLModel, table=True):
    """
    AI agent instance assigned to a company.

    Each agent has a role, an LLM model assignment, a persona system-prompt,
    and a monthly token budget.  The ``name`` field is a human-friendly alias
    (e.g. "Atlas", "Nova") displayed in the dashboard.
    """

    __tablename__ = "agents"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    name: str = ""  # Display alias (e.g. "Atlas")
    role: str | None = None  # e.g. "Architect", "Developer", "QA"
    persona: str | None = None  # Free-text system-prompt personality description
    seniority: str = "Senior"  # e.g. "Junior", "Mid", "Senior", "Staff"
    specialty: str = ""  # Domain speciality (e.g. "Cloud/DB", "Frontend/React")
    model: str = ""  # LLM model ID (e.g. "deepseek-coder", "gemini-2.5-pro")
    system_prompt: str | None = None  # Full system prompt for the agent's LLM calls
    token_budget: int = 500000  # Monthly token budget (soft limit)


class BusinessUnit(SQLModel, table=True):
    """
    Cost centre within a company that tracks LLM token consumption.

    When ``token_quota_used >= token_quota_limit``, the LLM Proxy will
    refuse further requests for that business unit (HTTP 429).
    A limit of 0 means unlimited.
    """

    __tablename__ = "business_units"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    unit_name: str
    token_quota_limit: int = 0  # Maximum tokens allowed (0 = unlimited)
    token_quota_used: int = 0  # Running total of tokens consumed this period


class User(SQLModel, table=True):
    """
    Human platform user (Product Owner, Engineering Manager, or Viewer).

    Passwords are stored as bcrypt hashes — never in plain text.
    """

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True)  # Used as the login identifier
    full_name: str
    password_hash: str  # bcrypt-hashed; raw password is never stored


class CompanyMember(SQLModel, table=True):
    """
    Associates a human user with a company and assigns their governance role.

    ``human_role`` controls which UI sections and API endpoints the user
    can access:  "Owner" > "Manager" > "Viewer".
    """

    __tablename__ = "company_members"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    user_id: int = Field(foreign_key="users.id")
    human_role: str  # "Owner" | "Manager" | "Viewer"


class ProjectCredential(SQLModel, table=True):
    """
    Encrypted API token or SSH key that agents use to access external services.

    In production the ``encrypted_value`` field should store a Vault/KMS
    reference rather than a directly encrypted secret.
    """

    __tablename__ = "project_credentials"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    credential_type: str  # e.g. "github_token", "openproject_token"
    access_level: str  # e.g. "read", "write", "admin"
    encrypted_value: str  # Vault path or envelope-encrypted ciphertext


class ProjectDiscovery(SQLModel, table=True):
    """
    Persisted result of the automatic project-stack discovery scan.

    ``detected_stack`` and ``suggested_team`` are JSON strings produced by
    ``DiscoveryService.discover_project_team()``.
    """

    __tablename__ = "project_discovery"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    project_path: str
    detected_stack: str | None = None  # JSON: language, framework, database, complexity
    suggested_team: str | None = None  # JSON: list of {role, seniority, specialty}
    status: str = "completed"
    created_at: datetime | None = Field(default_factory=datetime.utcnow)

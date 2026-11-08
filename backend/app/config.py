"""
Application settings for Fabrique Enterprise Backend.

All values can be overridden by environment variables or a local ``.env`` file.
pydantic-settings automatically reads env vars that match the field names
(case-sensitive by default, see the inner ``Config`` class).

Usage::

    from config import settings
    db_url = settings.POSTGRES_HOST
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ---------------------------------------------------------------------------
    # PostgreSQL — primary relational store (companies, sprints, backlog, audit)
    # ---------------------------------------------------------------------------
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "factory"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "factory"

    # ---------------------------------------------------------------------------
    # Neo4j — Temporal Knowledge Graph for episodic memory (Engrams)
    # ---------------------------------------------------------------------------
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""

    # ---------------------------------------------------------------------------
    # NATS — asynchronous event bus for agent orchestration and Glassroom SSE
    # ---------------------------------------------------------------------------
    NATS_URL: str = "nats://localhost:4222"

    # ---------------------------------------------------------------------------
    # OpenProject — external project-management tool used by PO / Dev agents
    # In production, tokens should be retrieved from Vault/KMS instead.
    # ---------------------------------------------------------------------------
    OPENPROJECT_URL: str = "http://localhost:8080"
    OPENPROJECT_TOKEN_PO: str | None = None  # Product Owner API token
    OPENPROJECT_TOKEN_DEV: str | None = None  # Developer API token

    # ---------------------------------------------------------------------------
    # LLM Proxy — internal gateway that routes requests to Gemini / DeepSeek
    # ---------------------------------------------------------------------------
    LLM_PROXY_URL: str = "http://localhost:5100"
    # How long the task worker waits for the workspace daemon to confirm file writes.
    WORKSPACE_WRITE_TIMEOUT_SECONDS: float = 30.0
    # After a verified delivery, run the project's tests through the daemon and
    # record the exit status as evidence.  Off by default: it executes
    # agent-written code on the machine running the daemon.
    # Run the Alembic migrations to head at startup (a local database); the
    # container's entrypoint and the e2e run `alembic upgrade head` instead.
    DB_AUTO_UPGRADE: bool = False
    DELIVERY_TESTS_ENABLED: bool = False
    # Test command to run; empty means detect it (npm test / pytest).
    DELIVERY_TEST_COMMAND: str = ""
    # The daemon itself stops a command after 120 s; wait slightly longer.
    DELIVERY_TEST_TIMEOUT_SECONDS: float = 130.0
    # Where agent-written code runs (the delivery test run and the
    # Developer's own test runs): ``host`` (the daemon's user, files and
    # network, as before), ``container`` (an ephemeral container per run:
    # the worktree mounted, network off, CPU/memory/process limits, every
    # capability dropped) or ``bwrap`` (bubblewrap namespaces). A company
    # may set its own (``Company.delivery_sandbox``). The daemon reports what
    # it applied, and the Verifier records it (``sandbox``).
    DELIVERY_SANDBOX: str = "host"
    DELIVERY_SANDBOX_IMAGE: str = "python:3.12-slim"
    DELIVERY_SANDBOX_NETWORK: bool = False
    DELIVERY_SANDBOX_CPUS: float = 2.0
    DELIVERY_SANDBOX_MEMORY_MB: int = 2048
    DELIVERY_SANDBOX_PIDS: int = 512
    # Stack profiles (services/stack_profiles.py): the commands detected per
    # stack, overridden here for the deployment and per company
    # (Company.stack_profile). Empty means detect.
    DELIVERY_INSTALL_COMMAND: str = ""
    DELIVERY_LINT_COMMAND: str = ""
    DELIVERY_START_COMMAND: str = ""
    DELIVERY_E2E_COMMAND: str = ""
    DELIVERY_APP_PORT: int = 0
    DELIVERY_APP_PROBE_PATH: str = "/"
    # With DELIVERY_TESTS_ENABLED: install the dependencies before the tests
    # when a manifest the delivery wrote differs from the last install's
    # (services/app_check.py), in the same sandbox, with the network open
    # (a registry is needed) unless DELIVERY_INSTALL_NETWORK is false.
    DELIVERY_INSTALL_ENABLED: bool = True
    DELIVERY_INSTALL_TIMEOUT_SECONDS: float = 600.0
    DELIVERY_INSTALL_NETWORK: bool = True
    # With DELIVERY_TESTS_ENABLED: start the application in the sandbox, wait
    # for its port, send a smoke request (and run the e2e command when the
    # profile has one), stop it. Off by default: it runs agent-written code.
    DELIVERY_APP_CHECK_ENABLED: bool = False
    DELIVERY_APP_READY_TIMEOUT_SECONDS: float = 60.0
    DELIVERY_E2E_TIMEOUT_SECONDS: float = 300.0

    # ---------------------------------------------------------------------------
    # Autonomous improvement & console assistants
    # ---------------------------------------------------------------------------
    AUTO_SELF_IMPROVEMENT_ENABLED: bool = True
    CONSOLE_ASSISTANTS_ENABLED: bool = True
    # The Support agent triages new help-desk tickets (suggested severity and
    # category, a first reply to the reporter). Off by default: reporting a
    # bug is public, and each new ticket costs an LLM call.
    SUPPORT_AGENT_ENABLED: bool = False
    SUPPORT_AGENT_INTERVAL_SECONDS: float = 60.0
    # The Reviewer agent reads the evidence of other producers' runs waiting
    # for a person and suggests the decision; a Manager still decides. Off by
    # default: each run costs an LLM call.
    REVIEWER_AGENT_ENABLED: bool = False
    REVIEWER_AGENT_INTERVAL_SECONDS: float = 60.0
    # A run with no verification yet waits this long, so a producer that
    # verifies right after ingesting is judged with the Verifier's verdict.
    REVIEWER_VERIFY_GRACE_SECONDS: float = 60.0
    # Per-call timeout for a console assistant run requested from the daemon.
    CONSOLE_ASSISTANT_TIMEOUT_SECONDS: float = 90.0
    # MCP tools (``services/mcp_tools.py``): how many rounds of tool calls a
    # task may make before the model must answer, how long one call and one
    # connection may take, and how much of a result the model is shown.
    MCP_MAX_TOOL_ROUNDS: int = 6
    MCP_TOOL_TIMEOUT_SECONDS: float = 60.0
    MCP_CONNECT_TIMEOUT_SECONDS: float = 20.0
    MCP_RESULT_MAX_CHARS: int = 4000
    # The Developer's workspace tools (``services/developer_tools.py``): read
    # the project through the daemon (list, read, search) and run its tests
    # before delivering, for at most this many rounds. ``run_tests`` is offered
    # only with ``DELIVERY_TESTS_ENABLED``.
    DEVELOPER_TOOLS_ENABLED: bool = True
    DEVELOPER_TOOL_ROUNDS: int = 8
    # The repository map in the Developer's, QA's and the Architect's prompts
    # (``services/repo_map.py``): at most this many characters of ranked
    # definitions beside the tree; 0 leaves them out.
    REPO_MAP_MAX_CHARS: int = 3000
    # After a ``workspace__edit``, the stack profile's lint command for that
    # file, its errors in the edit's result (``services/edit_lint.py``).
    EDIT_LINT_ENABLED: bool = True
    EDIT_LINT_TIMEOUT_SECONDS: float = 60.0
    # The tool loop (``services/tool_history.py``): results older than the
    # last this many rounds go to the model as one line each; the evidence
    # keeps them whole. 0 keeps every result whole.
    TOOL_HISTORY_KEEP_ROUNDS: int = 3
    # The research tools (``services/research_tools.py``): the roles that
    # decide read official sources (documentation, registries, OSV) through
    # the backend's own egress, from the default hosts plus these; every
    # source is evidence. The registries' URLs can point at a mirror.
    # The model catalogue (issue #356): models.dev, refreshed in the
    # background at most every MODELS_CATALOG_REFRESH_HOURS (0 keeps the
    # snapshot shipped in app/data/models_dev.json and never calls out).
    # Shared with the LLM proxy: it accepts a company's own provider
    # connection (issue #356) only with this token. Empty disables them.
    LLM_PROXY_INTERNAL_TOKEN: str = ""
    MODELS_CATALOG_URL: str = "https://models.dev/api.json"
    MODELS_CATALOG_REFRESH_HOURS: float = 24.0
    RESEARCH_TOOLS_ENABLED: bool = True
    RESEARCH_ROLES: str = "Architect,PO,SecOps,TechWriter"
    RESEARCH_ALLOWED_HOSTS: str = ""
    RESEARCH_TOOL_ROUNDS: int = 6
    RESEARCH_TIMEOUT_SECONDS: float = 20.0
    RESEARCH_FETCH_MAX_BYTES: int = 512 * 1024
    RESEARCH_PYPI_URL: str = "https://pypi.org/pypi"
    RESEARCH_NPM_URL: str = "https://registry.npmjs.org"
    RESEARCH_CRATES_URL: str = "https://crates.io/api/v1/crates"
    RESEARCH_GO_URL: str = "https://proxy.golang.org"
    RESEARCH_OSV_URL: str = "https://api.osv.dev"
    # Consult the Architect agent before Developers start high-priority, design-sensitive
    # or QA-reworked tasks. Emits ``ARCHITECT_CONSULTED:`` comments for analytics.
    ARCHITECT_CONSULT_ENABLED: bool = True
    # Enable the internal LangGraph producer; externally-produced runs use AgentRun ingestion.
    AGENT_GRAPH_ENABLED: bool = False
    COPILOT_CLI_COMMAND_TEMPLATE: str = "copilot -p {prompt} --output-format json"
    CLAUDE_CLI_COMMAND_TEMPLATE: str | None = (
        "claude --print --output-format json --add-dir {workspace_root} --allowedTools {allowed_tools} --append-system-prompt {system_prompt} {prompt}"
    )
    CLAUDE_CLI_ALLOWED_TOOLS: str = "Read,Grep,Glob,LS,Bash(git *)"
    CLAUDE_CLI_APPEND_SYSTEM_PROMPT: str = "You are being used as a bounded external coding advisor. Stay read-mostly, prefer incremental changes, preserve healthy behavior, and return concise actionable guidance for the current repository task."
    CONSOLE_ASSISTANT_STRATEGY: str = "copilot_first"
    CLAUDE_CLI_HIGH_VALUE_ROLES: str = "Architect,PO,QA,ScrumMaster"
    CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES: int = 10
    CONSOLE_ASSISTANT_ALLOWED_ROLES: str = "Developer,Architect,QA,PO,ScrumMaster"

    # ---------------------------------------------------------------------------
    # Audit — filesystem path for append-only JSONL audit logs
    # ---------------------------------------------------------------------------
    AUDIT_LOG_PATH: str = "/app/.factory/logs"

    # ---------------------------------------------------------------------------
    # Credential encryption — Fernet key for ProjectCredential.encrypted_value.
    # Unset means values are stored with a "plaintext:" prefix and a warning.
    # Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # ---------------------------------------------------------------------------
    FERNET_SECRET_KEY: str | None = None

    # ---------------------------------------------------------------------------
    # Human-user authentication — HS256 signing key for access tokens.
    # ---------------------------------------------------------------------------
    JWT_SECRET_KEY: str | None = None
    AUTH_REQUIRED: bool = True
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Audit signing — separate HMAC key for decision_signature values.
    # ---------------------------------------------------------------------------
    AUDIT_SIGNING_KEY: str | None = None

    # ---------------------------------------------------------------------------
    # GitHub integration — optional token for API rate-limit headroom
    # ---------------------------------------------------------------------------
    GITHUB_TOKEN: str | None = None
    # Address GitHub uses to reach this backend (webhook Payload URL), e.g. a
    # tunnel or the public host. Reported by /v1/capabilities for the frontend.
    PUBLIC_API_URL: str | None = None

    # ---------------------------------------------------------------------------
    # Support desk — accounts (comma-separated emails) that triage every ticket
    # and download the support logs. Other users see only the tickets they
    # reported with their account email.
    # ---------------------------------------------------------------------------
    SUPPORT_ADMIN_EMAILS: str = ""

    # ---------------------------------------------------------------------------
    # LLM API Keys
    # ---------------------------------------------------------------------------
    GEMINI_API_KEY: str | None = None
    DEEPSEEK_API_KEY: str | None = None
    QWEN_API_KEY: str | None = None

    # ---------------------------------------------------------------------------
    # Security — rate limiting and CORS
    # ---------------------------------------------------------------------------
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_STORAGE_URI: str = "memory://"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"
    SECURITY_HEADERS_ENABLED: bool = True
    # The knowledge graph is optional (issue #328): off, nothing is recorded
    # in it and its readers answer "not recorded"; memory search runs on
    # pgvector alone.
    NEO4J_ENABLED: bool = True
    AGENT_MODEL_POLICY: str = ""
    # The Manager agent (services/manager_agent.py): suggests each inbox
    # decision and takes alone only what the company's policy allows.
    MANAGER_AGENT_ENABLED: bool = False
    MANAGER_AGENT_INTERVAL_SECONDS: float = 60.0
    # The client's brief by mail (services/brief_mail.py): nothing is sent
    # unless SMTP_HOST is set.
    MANAGER_BRIEF_INTERVAL_HOURS: float = 24.0
    MANAGER_BRIEF_CHECK_SECONDS: float = 300.0
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_STARTTLS: bool = True
    SMTP_SSL: bool = False
    SMTP_TIMEOUT_SECONDS: float = 20.0

    class Config:
        env_file = ".env"
        case_sensitive = True


# Module-level singleton; import this object rather than instantiating Settings directly.
settings = Settings()

"""
Application settings for Fabrique Enterprise Backend.

All values can be overridden by environment variables or a local ``.env`` file.
pydantic-settings automatically reads env vars that match the field names
(case-sensitive by default, see the inner ``Config`` class).

Usage::

    from config import settings
    db_url = settings.POSTGRES_HOST
"""

from typing import Any

from pydantic import ValidationInfo, field_validator
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
    # Never read; kept declared so a .env that still sets it does not stop the
    # backend (the settings refuse unknown keys from that file).
    OPENPROJECT_TOKEN_DEV: str | None = None

    # ---------------------------------------------------------------------------
    # LLM Proxy — internal gateway that routes requests to DeepSeek (the
    # default team) and to Gemini / Qwen / Nebius when their keys are set
    # ---------------------------------------------------------------------------
    LLM_PROXY_URL: str = "http://localhost:5100"
    # One chat request's budget at the proxy (llm-proxy/timeouts.py reads the
    # same variable); the backend waits it plus a margin (llm_proxy_client.py).
    LLM_UPSTREAM_TIMEOUT_SECONDS: float = 300.0
    # Thinking: auto (each role's policy), on or off for every call; the tokens
    # a thinking call's cap gets for its reasoning (services/models/thinking.py).
    LLM_THINKING: str = "auto"
    LLM_THINKING_RESERVE_TOKENS: int = 16384
    # How long the task worker waits for the workspace daemon to confirm file writes.
    WORKSPACE_WRITE_TIMEOUT_SECONDS: float = 30.0
    # Run the Alembic migrations to head at startup (a local database); the
    # container's entrypoint and the e2e run `alembic upgrade head` instead.
    DB_AUTO_UPGRADE: bool = False
    # After a delivery, run the project's tests through the daemon, in the
    # company's sandbox (a container by default), and record the exit status
    # as evidence. On by default (issue #766): without it no code delivery can
    # be verified, and a company runs only with an Owner's ``tests_off``
    # acknowledgement (services/platform/acknowledgements.py).
    DELIVERY_TESTS_ENABLED: bool = True
    # Test command to run; empty means detect it (npm test / pytest).
    DELIVERY_TEST_COMMAND: str = ""
    # How long the daemon lets one test run (the root's tests, a folder's
    # suite, the Developer's run_tests) go on before it stops it; the daemon
    # caps it at 900 (issue #766: a real suite takes minutes).
    DELIVERY_TEST_RUN_SECONDS: int = 600
    # How long the backend waits for a test run's answer, at least the run's
    # own limit plus 30 s (``sandbox.test_wait``).
    DELIVERY_TEST_TIMEOUT_SECONDS: float = 130.0
    # The daemon's heartbeat (workspace.heartbeat, every <=25 s): with none
    # observed for this long, on the backend's clock, the daemon is ``stale``,
    # then ``offline`` (services/workspace/daemon_liveness.py).
    DAEMON_HEARTBEAT_STALE_SECONDS: float = 60.0
    DAEMON_HEARTBEAT_OFFLINE_SECONDS: float = 90.0
    # Where agent-written code runs (the delivery test run and the
    # Developer's own test runs): ``host`` (the daemon's user, files and
    # network, as before), ``container`` (an ephemeral container per run:
    # the worktree mounted, network off, CPU/memory/process limits, every
    # capability dropped) or ``bwrap`` (bubblewrap namespaces). A company
    # may set its own (``Company.delivery_sandbox``). The daemon reports what
    # it applied, and the Verifier records it (``sandbox``).
    # Container by default (issue #766): agent-written code never runs on
    # the daemon's computer unless a company chose ``host`` and an Owner
    # acknowledged it (services/platform/acknowledgements.py).
    DELIVERY_SANDBOX: str = "container"
    # The highest forge policy any company may have (services/contracts/
    # forge_policy.py): ``none``, ``fetch`` or ``publish``; an air-gapped
    # install sets ``none``. An unknown value reads as ``none``.
    FORGE_POLICY_MAX: str = "publish"
    # The longest the run-window worker sleeps between two looks at the
    # sprints it holds (services/loop/run_window_gate.py, issue #736); it
    # wakes at the next window boundary when that comes sooner, and never
    # looks more often than once a minute.
    RUN_WINDOW_RECHECK_SECONDS: int = 300
    # Empty: the daemon builds the project's own image from its stacks
    # (services/workspace/stack_image.py, issue #766). A value names one image
    # for every project instead, as before.
    DELIVERY_SANDBOX_IMAGE: str = ""
    # How long the backend waits for the daemon to build that image (the
    # daemon's own limit is FABRIQUE_STACK_BUILD_SECONDS, 1800).
    STACK_IMAGE_BUILD_TIMEOUT_SECONDS: float = 1900.0
    DELIVERY_SANDBOX_NETWORK: bool = False
    DELIVERY_SANDBOX_CPUS: float = 2.0
    DELIVERY_SANDBOX_MEMORY_MB: int = 2048
    DELIVERY_SANDBOX_PIDS: int = 512
    # Container mode: a named, labelled volume per project for the package
    # managers' caches (vscode-daemon/sandbox_cache.py), kept after a run,
    # removed after a failed install, quarantined after three in a row.
    DELIVERY_SANDBOX_CACHE: bool = False
    # Container mode: a volume per project, company, manifests and image for
    # what an install leaves outside the worktree (Python's user site, Poetry
    # and Pipenv virtualenvs, Go's GOPATH and caches), writable only by an
    # install, with the cache's reset and quarantine (issue #315). On by
    # default (issue #766): a Poetry project's virtualenv lives there, so
    # without it the tests find none of what the install installed.
    DELIVERY_SANDBOX_ENV: bool = True
    # Container mode: the image the application check runs in when its e2e
    # command is a browser test (QA's smoke tests, or a ``web`` profile's
    # e2e), e.g. one built from vscode-daemon/images/browser. Empty keeps
    # DELIVERY_SANDBOX_IMAGE for it, as before.
    DELIVERY_BROWSER_IMAGE: str = ""
    # Container mode: the browser image of each Playwright release, as
    # "1.63.0=ghcr.io/…@sha256:…,1.64.1=ghcr.io/…@sha256:…" (the digests
    # images.yml prints). A browser e2e command runs in the image of the
    # project's pinned release (read by the daemon from its lockfile), falls
    # back to DELIVERY_BROWSER_IMAGE with the mismatch recorded, and is not
    # run (unverifiable) when the version cannot be told. An entry without a
    # digest is refused. Empty keeps one image for every project, as before.
    DELIVERY_BROWSER_IMAGES: str = ""
    # The whole output of the install, the tests and the application check,
    # uploaded by the daemon to the JetStream object store FABRIQUE_RUN_LOGS
    # and hashed here on receipt (services/workspace/run_logs.py). Objects
    # expire after FULL_LOG_RETENTION_DAYS (0 keeps them) and the oldest go
    # first beyond FULL_LOG_BUCKET_MAX_MB; the evidence keeps the sha256.
    DELIVERY_FULL_LOGS: bool = True
    DELIVERY_FULL_LOG_MAX_MB: int = 50
    FULL_LOG_RETENTION_DAYS: int = 30
    FULL_LOG_BUCKET_MAX_MB: int = 4096
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
    # (services/workspace/dependency_install.py), in the same sandbox, with
    # the network open (a registry is needed) unless DELIVERY_INSTALL_NETWORK
    # is false.
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
    # Autonomous improvement
    # ---------------------------------------------------------------------------
    # Off by default (docs/design/rsi-loop.md, decision 8): the model-planned
    # follow-up sprint ran with no protected-path guard; a company turns it on.
    AUTO_SELF_IMPROVEMENT_ENABLED: bool = False
    # The self-improvement cycle (services/management/rsi_cycle.py, decisions
    # 4 and 7 of docs/design/rsi-loop.md): off, no cycle starts or advances.
    RSI_ENABLED: bool = False
    RSI_CYCLES_PER_DAY: int = 1
    RSI_STOP_AFTER_CYCLES_WITHOUT_KEEP: int = 3
    RSI_MAX_DIFF_FILES: int = 20
    RSI_MAX_DIFF_LINES: int = 600
    # Reported tokens of the first manual iteration's sprint; 0 is unknown and
    # no cycle starts. A cycle's budget is at most twice it.
    RSI_FIRST_SPRINT_TOKENS: int = 0
    RSI_CYCLE_TOKEN_BUDGET: int = 0  # 0: the cap, twice the first sprint
    # Where the backend reads a cycle record's kept files to hash them itself.
    RSI_FILES_ROOT: str = ""
    # The checkout whose bench/scorecards the /bench page reads (read-only);
    # empty: the backend's own checkout, when it has one.
    BENCH_REPOSITORY_DIR: str = ""
    # The checkout whose scripts/self_host an owner action imports (read-only);
    # empty: the backend's own checkout, else AGENT_WORKSPACE_ROOT's.
    SELF_HOST_REPOSITORY_DIR: str = ""
    # Where a company without a discovered project folder writes (compose:
    # /workspace, the repository); the owner action looks for the harness there.
    AGENT_WORKSPACE_ROOT: str = ""
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
    # A console producer's run (services/producers/console_producer.py): the CLI's wall clock
    # on the daemon (which caps a request at 900 s).
    CONSOLE_PRODUCER_TIMEOUT_SECONDS: float = 600.0
    # MCP tools (``services/mcp_tools.py``): how many rounds of tool calls a
    # task may make before the model must answer, how long one call and one
    # connection may take, and how much of a result the model is shown.
    MCP_MAX_TOOL_ROUNDS: int = 6
    MCP_TOOL_TIMEOUT_SECONDS: float = 60.0
    MCP_CONNECT_TIMEOUT_SECONDS: float = 20.0
    MCP_RESULT_MAX_CHARS: int = 4000
    # Who may run what (``services/tools/mcp_policy.py``), set by the operator
    # in the environment and never through the API: the absolute paths of the
    # commands a stdio server may spawn (comma-separated; empty disables stdio
    # servers), and the hosts an HTTP server may reach over plain http.
    MCP_STDIO_ALLOWED_COMMANDS: str = ""
    MCP_HTTP_ALLOWED_HOSTS: str = ""
    # The Developer's workspace tools (``services/developer_tools.py``): read
    # the project through the daemon (list, read, search) and run its tests
    # before delivering, for at most this many rounds. ``run_tests`` is offered
    # only with ``DELIVERY_TESTS_ENABLED``.
    DEVELOPER_TOOLS_ENABLED: bool = True
    # 5, measured in phase D (issue #766): with 8, a third of the runs used
    # every round, they were verified no more often, and the bench's specs 01
    # and 02 did not fit a million tokens; with 5 both did, every criterion
    # passed, and spec 01 cost a third less (DeepSeek) to three fifths less (Qwen).
    DEVELOPER_TOOL_ROUNDS: int = 5
    QA_REVIEW_TOOL_ROUNDS: int = 3  # QA's read-only rounds before its verdict
    # The read-only rounds of the steps that only read (contracts/tool_steps):
    # the PO's sprint planning (and its specification drafts, beside research)
    # and the ScrumMaster's decision on a blocked task. 0: no reads, as before.
    PLANNING_TOOL_ROUNDS: int = 3
    SCRUM_TRIAGE_TOOL_ROUNDS: int = 3
    # The record tools (services/tools/board_tools.py): board__task and
    # board__evidence, read-only, for the QA review, the Architect's brief, a
    # rework, the ScrumMaster's triage (the sprint) and the PO's plan (the board).
    BOARD_TOOLS_ENABLED: bool = True
    # The cap on the Developer's answer in tokens; 0 sends none, and the
    # provider's own default applies (issue #766). Never above the model's
    # output limit (services/models/model_profile.py).
    DEVELOPER_MAX_OUTPUT_TOKENS: int = 0
    # Per-model overrides of the profile (services/models/model_profile.py), a
    # JSON object by model name: context, output, tool_call, reasoning and the
    # Developer's tool_rounds, result_max_chars and max_output_tokens, e.g.
    # {"qwen4-max": {"context": 262144, "output": 65536, "tool_rounds": 12}}.
    MODEL_PROFILES: dict[str, dict[str, Any]] = {}
    # The repository map in the Developer's, QA's and the Architect's prompts
    # (``services/repo_map.py``): at most this many characters of ranked
    # definitions beside the tree; 0 leaves them out.
    REPO_MAP_MAX_CHARS: int = 3000
    # The related code in the Developer's prompt (``services/tools/code_graph.py``):
    # the files around the task's modules and earlier writes, from the
    # daemon's parse, and the recent runs that wrote them; 0 leaves it out.
    CODE_GRAPH_MAX_CHARS: int = 2500
    # Where the code graph the daemon reports is kept (issue #438, second slice;
    # ``services/tools/code_graph_store.py``): "postgres" (the tables), "neo4j"
    # (the knowledge graph, only while NEO4J_ENABLED; else Postgres) or "off"
    # (nothing stored; the section is computed on demand, as before).
    CODE_GRAPH_STORE: str = "postgres"
    # The sprint board in every task prompt (``services/loop/worker/sprint_notes.py``,
    # issue #439): the notes of the sprint relevant to the task, newest first,
    # within this many characters; 0 leaves the section out.
    SPRINT_BOARD_MAX_CHARS: int = 2000
    # The project wiki (issue #445, docs/archive/design/PROJECT_WIKI.md). Off: no wiki
    # section in prompts (vector memory alone, the bench's baseline) and no
    # compilation at sprint close; the pages stay and the API still serves them.
    WIKI_ENABLED: bool = True
    # The pages of a task's modules and concepts in its prompt, within this many
    # characters, before the vector memory; 0 leaves the section out.
    WIKI_MAX_CHARS: int = 3000
    # At sprint close: at most this many pages compiled by a model, each answer
    # at most this many characters (a longer one is rejected and recorded).
    WIKI_COMPILE_MAX_PAGES: int = 5
    WIKI_PAGE_MAX_CHARS: int = 6000
    # After a ``workspace__edit``, the stack profile's lint command for that
    # file, its errors in the edit's result (``services/edit_lint.py``).
    EDIT_LINT_ENABLED: bool = True
    EDIT_LINT_TIMEOUT_SECONDS: float = 60.0
    # ``workspace__run_program`` (``tools/program_runs.py``): the runs of a
    # declared program one task attempt may make, and the scored evaluation
    # runs among them (the owner's decision 3: three; only the Owner raises it).
    PROGRAM_RUNS_PER_TASK: int = 5
    PROGRAM_EVAL_RUNS_PER_TASK: int = 3
    # The tool loop (``tools/tool_history.py``): results older than the last KEEP
    # rounds go to the model as one line (0: none); an old read that is still the
    # latest view stays whole while all results fit MAX_CHARS (0: no bound).
    TOOL_HISTORY_KEEP_ROUNDS: int = 3
    TOOL_HISTORY_MAX_CHARS: int = 120000
    # Named sections of the Developer's task prompt left out of every prompt,
    # comma-separated (``services/loop/worker/prompt_budget.py``, issue #443):
    # a with/without comparison for the bench. Empty: nothing is off.
    PROMPT_SECTIONS_OFF: str = ""
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
    RESEARCH_ROLES: str = "Architect,PO,SecOps,TechWriter,Designer"
    # The Verifier's ``grounded`` check (services/evidence/grounding.py):
    # look each new dependency up at its registry after delivery, through the
    # research registries, and record the answer on the evidence. A package
    # no registry has refutes the delivery.
    GROUNDING_LOOKUPS_ENABLED: bool = True
    GROUNDING_MAX_LOOKUPS: int = 20
    RESEARCH_ALLOWED_HOSTS: str = ""
    RESEARCH_TOOL_ROUNDS: int = 6
    RESEARCH_TIMEOUT_SECONDS: float = 20.0
    RESEARCH_FETCH_MAX_BYTES: int = 512 * 1024
    RESEARCH_PYPI_URL: str = "https://pypi.org/pypi"
    RESEARCH_NPM_URL: str = "https://registry.npmjs.org"
    RESEARCH_CRATES_URL: str = "https://crates.io/api/v1/crates"
    RESEARCH_GO_URL: str = "https://proxy.golang.org"
    RESEARCH_OSV_URL: str = "https://api.osv.dev"
    # Consult the Architect, on the team's own model, before a high-priority,
    # design-sensitive or reworked task of a coding role, QA, PO or ScrumMaster
    # (issue #750; ``ARCHITECT_CONSULTED:`` comments). It may read the project
    # first with the read-only workspace tools for at most ARCHITECT_TOOL_ROUNDS
    # rounds (services/loop/worker/architect_brief.py), and while it drafts an
    # architecture (services/planning/planning_reads.py); 0: no reads.
    ARCHITECT_CONSULT_ENABLED: bool = True
    ARCHITECT_TOOL_ROUNDS: int = 6
    # Enable the internal LangGraph producer; externally-produced runs use AgentRun ingestion.
    AGENT_GRAPH_ENABLED: bool = False
    # How long a console producer's CLI cools down after a quota stop whose
    # message names no reset time (services/producers/cli_cooldowns.py).
    CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES: int = 10

    # Removed with the console assistants (issue #750): the team's Architect
    # gives the second opinion. Never read; declared so a .env that still sets
    # one does not stop the backend (the settings refuse unknown keys from that
    # file), and named once at startup (``removed_settings.py``).
    CONSOLE_ASSISTANTS_ENABLED: str | None = None
    CONSOLE_ASSISTANT_TIMEOUT_SECONDS: str | None = None
    CONSOLE_ASSISTANT_STRATEGY: str | None = None
    CONSOLE_ASSISTANT_ALLOWED_ROLES: str | None = None
    COPILOT_CLI_COMMAND_TEMPLATE: str | None = None
    CLAUDE_CLI_COMMAND_TEMPLATE: str | None = None
    CLAUDE_CLI_ALLOWED_TOOLS: str | None = None
    CLAUDE_CLI_APPEND_SYSTEM_PROMPT: str | None = None
    CLAUDE_CLI_HIGH_VALUE_ROLES: str | None = None

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
    # An HMAC algorithm only (HS256, HS384, HS512): anything else refuses to
    # sign or verify (issue #541).
    JWT_ALGORITHM: str = "HS256"
    # Every access token names its issuer and audience, and validation
    # requires both (issue #541); the Glassroom stream tickets use
    # "<JWT_AUDIENCE>:glassroom". Two deployments that share a
    # JWT_SECRET_KEY must not share these values.
    JWT_ISSUER: str = "la-fabrique"
    JWT_AUDIENCE: str = "la-fabrique-api"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    # Password sign-in and registration (issue #541, WSTG-v42-ATHN-03): per
    # client address, and per account with a backoff that doubles after
    # LOGIN_FAILURES_BEFORE_BACKOFF failures in LOGIN_FAILURE_WINDOW_SECONDS,
    # up to LOGIN_BACKOFF_MAX_SECONDS.
    LOGIN_RATE_LIMIT: str = "10/minute"
    REGISTER_RATE_LIMIT: str = "5/minute"
    LOGIN_FAILURES_BEFORE_BACKOFF: int = 5
    LOGIN_FAILURE_WINDOW_SECONDS: int = 900
    LOGIN_BACKOFF_MAX_SECONDS: int = 900
    # The shortest password a person may choose, in characters (WSTG-v42-
    # ATHN-07). Checked when a password is set, never at sign-in, so an
    # account made before the rule is not locked out.
    PASSWORD_MIN_LENGTH: int = 12
    # How long an invitation to a company stays acceptable.
    INVITATION_TTL_HOURS: int = 168
    # The reverse proxies whose X-Forwarded-For is believed: comma-separated
    # addresses or networks (10.0.0.5, 172.18.0.0/16). Empty trusts none, and
    # the client is the address the connection comes from
    # (security.TrustedProxyMiddleware; docs/OPERATIONS.md).
    TRUSTED_PROXY_IPS: str = ""
    # Browser sessions (issue #524; docs/archive/design/SESSIONS.md): a refresh-token
    # family lives at most this many days from sign-in, however often it is
    # rotated; a reuse within the grace (two tabs refreshing at once) is
    # answered 409 without revoking; the refresh route's rate limit.
    REFRESH_TOKEN_ABSOLUTE_DAYS: int = 7
    REFRESH_REUSE_GRACE_SECONDS: int = 10
    REFRESH_RATE_LIMIT: str = "30/minute"
    # The session cookies are Secure; false only for development over plain
    # http:// (a browser treats http://localhost as secure anyway).
    SESSION_COOKIE_SECURE: bool = True

    # Single sign-on over OpenID Connect (issue #512; docs/archive/design/SSO.md).
    # The redirect URI registered at each company's identity provider, and
    # where the browser lands afterwards (the frontend's /login/sso).
    SSO_CALLBACK_URL: str = "http://localhost:8000/v1/auth/sso/callback"
    SSO_FRONTEND_URL: str = "http://localhost:5173"
    # Applied to the start, callback and exchange routes, per client address.
    SSO_RATE_LIMIT: str = "20/minute"
    # POST /v1/workspace/git_check (the New company form), per account (issue #745).
    GIT_CHECK_RATE_LIMIT: str = "10/minute"
    # Owners (comma-separated emails) who may still sign in with a password
    # when their company requires SSO: the operator's break-glass list.
    SSO_BREAK_GLASS_EMAILS: str = ""
    # Allow an issuer on a private or loopback address (a Keycloak on the LAN).
    SSO_ALLOW_PRIVATE_ISSUERS: bool = False
    SSO_HTTP_TIMEOUT_SECONDS: float = 10.0
    SSO_CLOCK_SKEW_SECONDS: int = 60
    # DNS verification of the allowed domains (docs/archive/design/SSO.md). Domains
    # listed before verification existed keep creating and linking accounts
    # for SSO_LEGACY_DOMAIN_GRACE_DAYS after the upgrade while the grace is on.
    SSO_LEGACY_DOMAIN_GRACE: bool = True
    SSO_LEGACY_DOMAIN_GRACE_DAYS: int = 30
    # How often each domain's TXT record is looked up again; 0 turns it off.
    SSO_DOMAIN_RECHECK_HOURS: int = 24
    SSO_DNS_TIMEOUT_SECONDS: float = 5.0
    # Resolvers to ask (comma-separated IP addresses); empty: the system's.
    SSO_DNS_NAMESERVERS: str = ""

    # Approving from Slack or Teams (issue #512, phase 4; docs/archive/design/CHAT_APPROVALS.md).
    # The frontend address the chat messages link to (the run's evidence, the
    # task), and the rate limit of the public inbound endpoints per address.
    CHAT_APP_URL: str = "http://localhost:5173"
    CHAT_RATE_LIMIT: str = "120/minute"

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
    # The interactive API documentation (/docs, /redoc, /openapi.json). Off
    # unless turned on (WSTG-v42-CONF-05); docker-compose turns it on for
    # local development.
    API_DOCS_ENABLED: bool = False
    # An optional notice of each unhandled error (issue #541): the error id,
    # the exception class and the route template, never a traceback, message,
    # path or body. Off unless set; https only. ERROR_REPORT_SOURCE names this
    # deployment in the report.
    ERROR_REPORT_URL: str = ""
    ERROR_REPORT_SOURCE: str = ""
    # The knowledge graph is optional (issue #328): off, nothing is recorded
    # in it and its readers answer "not recorded"; memory search runs on
    # pgvector alone.
    NEO4J_ENABLED: bool = True
    AGENT_MODEL_POLICY: str = ""
    # The edit format per model (services/models/edit_format.py, issue #689):
    # JSON {"<model>": "auto" | "whole" | "search_replace", "*": ...}. Empty:
    # every model runs on auto, the prompt as before.
    EDIT_FORMAT_BY_MODEL: str = ""
    # Architect and editor mode (services/loop/edit_plan.py, from Aider): off, the
    # Architect's brief stays free text. On, a Developer task at or above
    # ARCHITECT_EDITOR_MIN_DIFFICULTY gets a numbered edit plan from the
    # Architect and runs on the policy's cheapest band model.
    ARCHITECT_EDITOR_MODE: bool = False
    ARCHITECT_EDITOR_MIN_DIFFICULTY: int = 4
    # The Manager agent (services/management/manager_agent.py): suggests each inbox
    # decision and takes alone only what the company's policy allows.
    MANAGER_AGENT_ENABLED: bool = False
    MANAGER_AGENT_INTERVAL_SECONDS: float = 60.0
    # The client's brief by mail (services/management/brief_mail.py): nothing is sent
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

    # ---------------------------------------------------------------------------
    # Operational values that were read with os.getenv where they are used
    # (issue #766, phase A of the configuration work): the same names and
    # defaults, now in one place; the modules keep their floors.
    # ---------------------------------------------------------------------------
    # The task worker re-dispatches a To do item no sooner than this.
    DISPATCH_REQUEUE_COOLDOWN_SECONDS: int = 90
    # QA's "needs rework" sends the task back to the Developer on its own.
    AUTOPILOT_QA_REWORK_ENABLED: bool = True
    # A task's one rework budget, every cause together (issue #766); a company
    # may set its own within 0..10 (services/contracts/company_limits.py).
    # Unset: AUTOPILOT_MAX_REWORK_CYCLES (its old name, deprecated), else 3.
    MAX_REWORKS: int | None = None
    AUTOPILOT_MAX_REWORK_CYCLES: int | None = None
    # A close's integration the daemon could not take is retried this often;
    # this many run at once per process (at least 1).
    INTEGRATION_RETRY_SECONDS: float = 60.0
    INTEGRATION_MAX_CONCURRENT: int = 2
    # The backend's retries when the LLM proxy cannot be reached.
    LLM_PROXY_MAX_RETRIES: int = 2
    LLM_PROXY_RETRY_BACKOFF_SEC: float = 1.0
    # How long a coding CLI missing from the sandbox is not offered again.
    ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES: int = 30
    # The workspace snapshot in the Developer's prompt
    # (services/workspace/workspace_snapshot.py); each has a floor there.
    WORKSPACE_SNAPSHOT_ENABLED: bool = True
    WORKSPACE_SNAPSHOT_CACHE_TTL: int = 60
    WORKSPACE_SNAPSHOT_MAX_FILES: int = 4000
    WORKSPACE_SNAPSHOT_TOP_RELEVANT: int = 5
    WORKSPACE_SNAPSHOT_LINES_PER_FILE: int = 60
    WORKSPACE_SNAPSHOT_LAYOUT_ENTRIES: int = 30
    WORKSPACE_EVIDENCE_LINES_PER_FILE: int = 80
    WORKSPACE_EVIDENCE_MAX_FILE_BYTES: int = 65536

    @field_validator("MAX_REWORKS", "AUTOPILOT_MAX_REWORK_CYCLES", mode="before")
    @classmethod
    def _count_or_unset(cls, value: Any) -> Any:
        """Not a whole number: unset, so the default applies (as with os.getenv)."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @field_validator(
        "LLM_UPSTREAM_TIMEOUT_SECONDS", "LLM_THINKING_RESERVE_TOKENS", mode="before"
    )
    @classmethod
    def _positive_or_default(cls, value: Any, info: ValidationInfo) -> Any:
        """A value that is not a positive number keeps the default, as these
        did when read with os.getenv: a typo never lifts or zeroes a budget."""
        default = cls.model_fields[info.field_name or ""].default
        try:
            return value if float(value) > 0 else default
        except (TypeError, ValueError):
            return default

    class Config:
        env_file = ".env"
        case_sensitive = True


# Module-level singleton; import this object rather than instantiating Settings directly.
settings = Settings()

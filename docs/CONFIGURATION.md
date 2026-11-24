# Configuration

Every environment variable of the backend, the LLM proxy, the broker and the workspace daemon.

**Where settings are read.** The backend's settings are the fields of `Settings` in [`backend/app/config.py`](../backend/app/config.py): each has a typed default and is read from the environment or from `.env`. Under `backend/app`, only `config.py` reads the environment; `scripts/check_config.py` enforces that and fails when a field of `Settings` is not named here. `docker-compose.yml` forwards a subset of `.env` to each container and sets a few values of its own (noted below as "compose"). The LLM proxy (`llm-proxy/`) and the workspace daemon (`vscode-daemon/`) read their own variables directly.

Variables of the bench and the e2e run are in [`bench/README.md`](../bench/README.md) and [`docs/TESTING.md`](TESTING.md).

## Minimal configuration: DeepSeek only

```bash
cp .env.example .env
# Set DEEPSEEK_API_KEY, and the platform's own secrets: POSTGRES_PASSWORD,
# NEO4J_PASSWORD (or NEO4J_ENABLED=false), JWT_SECRET_KEY.
docker compose up -d
```

With nothing else set, every role runs on DeepSeek Pro or Flash. A request that fails moves to DeepSeek's other model, never to another provider. Memory uses a local embedding model in the proxy. The knowledge graph's absence, mail, error reports and the optional agents are off with a named reason, and nothing waits for them. What is on, off or degraded is logged at startup, served by `/health/deep` (`capabilities`) and shown on the Models page. Design: [`docs/archive/design/DEEPSEEK_ONLY.md`](archive/design/DEEPSEEK_ONLY.md).

**Where the data goes.** DeepSeek's API processes requests in China. If prompts carry personal data, that is a transfer to a third country under GDPR (arts. 44–49), and a DPIA (art. 35) likely applies. The alternative is to host the open-weight models yourself and add a provider connection on the Models page.

## Secrets

Set secrets in `.env` or a secret store, never in `docker-compose.yml`.

| Variable | Default | What it does |
|---|---|---|
| `DEEPSEEK_API_KEY` | unset | DeepSeek key, read by the proxy only. Required: every default role runs on DeepSeek. |
| `QWEN_API_KEY` | unset | Qwen key, read by the proxy. Serves `qwen*`, `qwq-*` and `qvq-*` model ids an agent, role variable or policy names. |
| `POSTGRES_PASSWORD` | `""` | Postgres password. |
| `NEO4J_PASSWORD` | `""` | Neo4j password. `NEO4J_AUTH=neo4j/<password>` (compose, for the Neo4j container) must match. |
| `JWT_SECRET_KEY` | unset | Signs access tokens. Unset or too short, login answers 503. |
| `FERNET_SECRET_KEY` | unset | Encrypts stored credentials, SSO client secrets and chat connections. Unset, credentials are stored with a `plaintext:` prefix and chat connections are refused. |
| `AUDIT_SIGNING_KEY` | unset | HMAC key for audit rows and each company's audit hash chain. Unset, the chain is unkeyed and says so. |
| `LLM_PROXY_INTERNAL_TOKEN` | `""` | Shared by backend and proxy. The proxy accepts a company's own provider connection (`conn:<id>:<model>`) only with it. Empty disables company connections. |
| `SMTP_PASSWORD` | `""` | Mail server password. |
| `GITHUB_TOKEN` | unset | Bearer token for the GitHub adapter's API reads, for rate-limit headroom. The owner action `close_issue` needs it, with permission to write issues on the repository the daemon's `origin` names (fine-grained: Issues read and write); unset, that action is unavailable. |
| `OPENPROJECT_TOKEN_PO` | unset | The PO's OpenProject token. Without it, tickets are simulated locally. |

How to generate them:

```bash
# JWT_SECRET_KEY: at least the algorithm's hash size (32 bytes for HS256, 48 for HS384, 64 for HS512)
python -c "import secrets; print(secrets.token_urlsafe(48))"
# FERNET_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# AUDIT_SIGNING_KEY
python -c "import secrets; print(secrets.token_hex(32))"
```

The factory holds `AUDIT_SIGNING_KEY`, so anchor the chain's head outside it: an Owner sets audit anchors per company ([`docs/archive/design/AUDIT_ANCHORS.md`](archive/design/AUDIT_ANCHORS.md)).

## Databases and broker

| Variable | Default | What it does |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | Postgres host, for the backend, the janitor and the proxy's quota store. Compose: `postgres`. |
| `POSTGRES_PORT` | `5432` | Postgres port. |
| `POSTGRES_USER` | `factory` | Postgres user. |
| `POSTGRES_DB` | `factory` | Postgres database. |
| `DB_AUTO_UPGRADE` | `false` | Run Alembic migrations to head at startup, for a local database. The backend refuses to start on a database behind head. |
| `NEO4J_ENABLED` | `true` | The optional knowledge graph. Off, memory runs on pgvector alone and graph readers answer "not recorded". See [platform](contexts/platform.md). |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j address. `.env.example`: `bolt://neo4j:7687`. |
| `NEO4J_USER` | `neo4j` | Neo4j user. |
| `NATS_URL` | `nats://localhost:4222` | Broker address, for backend and daemon. Compose: `nats://nats:4222`. Never put credentials in it. |
| `NATS_NAMESPACE` | `""` | Prefix for subjects and streams, for a namespaced deployment. Backend and daemon. |

### Securing the bus

Read by `nats/start.sh`, the backend (`app/nats_auth.py`) and the daemon (`vscode-daemon/nats_connect.py`). Setup: [`docs/OPERATIONS.md`](OPERATIONS.md), "Securing the bus".

| Variable | Default | What it does |
|---|---|---|
| `NATS_BIND` | `127.0.0.1` | Host address NATS listens on. `0.0.0.0` lets other machines ask the daemon to run commands; the backend warns when it is not loopback and the bus has no credentials. |
| `NATS_BACKEND_PASSWORD`, `NATS_DAEMON_PASSWORD` | unset | Set both or neither. With both, the broker runs one user per component with the permissions in `nats/nats-server.conf` (written by `scripts/nats_config.py`). One alone stops the broker. |
| `NATS_USER`, `NATS_PASSWORD` | unset | User and password a component connects with. Compose sets `NATS_USER=backend` / `daemon`. |
| `NATS_CREDS_FILE` | unset | A `.creds` file. Wins over the others. |
| `NATS_NKEY_SEED` | unset | Path of an nkey seed file. |
| `NATS_TLS` | unset | `true`: connect with TLS; on the broker, require the certificates in `nats/certs`. |
| `NATS_TLS_CA` | unset | CA file that signed the server certificate. |
| `NATS_TLS_CERT`, `NATS_TLS_KEY` | unset | Client certificate and key. Keep the key in `nats/certs`, never in `.env`. |
| `NATS_TLS_VERIFY` | unset | On the broker: require client certificates (needs `ca.crt`). |
| `NATS_BACKEND_TLS_CERT`, `NATS_BACKEND_TLS_KEY`, `NATS_DAEMON_TLS_CERT`, `NATS_DAEMON_TLS_KEY` | empty | Compose only: client certificate file names under `nats/certs`. |

## Authentication and sessions

| Variable | Default | What it does |
|---|---|---|
| `AUTH_REQUIRED` | `true` | Enforce authentication on sensitive routes. |
| `JWT_ALGORITHM` | `HS256` | Allowed: `HS256`, `HS384`, `HS512`. Anything else refuses to sign or verify. |
| `JWT_ISSUER` | `la-fabrique` | Issuer in every token. Two deployments that share a key must not share it. |
| `JWT_AUDIENCE` | `la-fabrique-api` | Audience in every token; Glassroom tickets use `<JWT_AUDIENCE>:glassroom`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token life. |
| `REFRESH_TOKEN_ABSOLUTE_DAYS` | `7` | A session ends this long after sign-in, however often it is refreshed. |
| `REFRESH_REUSE_GRACE_SECONDS` | `10` | A refresh token reused this soon after rotation (two tabs) gets 409 without revoking the session. |
| `SESSION_COOKIE_SECURE` | `true` | Session cookies are `Secure`. Set `false` only to develop over plain http on a host other than localhost. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000` | Allowed frontend origins. With `*`, no credentials are allowed, so sign-in from another origin fails. |
| `PASSWORD_MIN_LENGTH` | `12` | Shortest password a person may set. Not checked at sign-in. |
| `LOGIN_FAILURES_BEFORE_BACKOFF` | `5` | Failures within the window before the per-account backoff starts. |
| `LOGIN_FAILURE_WINDOW_SECONDS` | `900` | Window for counting failures. |
| `LOGIN_BACKOFF_MAX_SECONDS` | `900` | Longest lock; each further failure doubles it up to this. |
| `INVITATION_TTL_HOURS` | `168` | How long an invitation to a company stays valid. |
| `SUPPORT_ADMIN_EMAILS` | `""` | Comma-separated accounts that triage every support ticket and download support logs. |

Design: [`docs/archive/design/SESSIONS.md`](archive/design/SESSIONS.md).

### Single sign-on (OIDC)

Each company's connection is stored by its Owner, not in the environment. Design: [`docs/archive/design/SSO.md`](archive/design/SSO.md); setup: [`docs/OPERATIONS.md`](OPERATIONS.md), Single sign-on.

| Variable | Default | What it does |
|---|---|---|
| `SSO_CALLBACK_URL` | `http://localhost:8000/v1/auth/sso/callback` | Redirect URI every company registers at its provider. |
| `SSO_FRONTEND_URL` | `http://localhost:5173` | Where the callback sends the browser afterwards. |
| `SSO_BREAK_GLASS_EMAILS` | `""` | Owners who may still sign in with a password when SSO is required. Each such sign-in is audited. |
| `SSO_ALLOW_PRIVATE_ISSUERS` | `false` | Allow an issuer on a private or loopback address. |
| `SSO_HTTP_TIMEOUT_SECONDS` | `10` | Timeout of calls to the provider. |
| `SSO_CLOCK_SKEW_SECONDS` | `60` | Leeway on the ID token's `exp` and `iat`. |
| `SSO_LEGACY_DOMAIN_GRACE` | `true` | Domains listed before DNS verification existed keep working unverified during the grace. |
| `SSO_LEGACY_DOMAIN_GRACE_DAYS` | `30` | Length of that grace, from the upgrade. |
| `SSO_DOMAIN_RECHECK_HOURS` | `24` | How often each domain's TXT record is checked again. `0` turns it off. |
| `SSO_DNS_TIMEOUT_SECONDS` | `5` | Deadline of one lookup. |
| `SSO_DNS_NAMESERVERS` | `""` | Comma-separated resolver IPs. Empty: the system's. |

### Rate limits

slowapi limits, per client address unless noted, stored in `RATE_LIMIT_STORAGE_URI`.

| Variable | Default | What it does |
|---|---|---|
| `RATE_LIMIT_DEFAULT` | `100/minute` | Every route without a limit of its own. |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Where counters are kept. |
| `LOGIN_RATE_LIMIT` | `10/minute` | `POST /v1/auth/login`. |
| `REGISTER_RATE_LIMIT` | `5/minute` | `POST /v1/auth/register`. |
| `REFRESH_RATE_LIMIT` | `30/minute` | `POST /v1/auth/refresh`. |
| `SSO_RATE_LIMIT` | `20/minute` | SSO start, callback and exchange. |
| `CHAT_RATE_LIMIT` | `120/minute` | Public Slack and Teams endpoints. |
| `GIT_CHECK_RATE_LIMIT` | `10/minute` | `POST /v1/workspace/git_check`, per account. |
| `TRUSTED_PROXY_IPS` | `""` | Reverse proxies whose `X-Forwarded-For` is believed (addresses or CIDR). Empty trusts none, so behind a proxy every limit keys on the proxy. A malformed entry stops startup. See [`docs/OPERATIONS.md`](OPERATIONS.md), Behind a reverse proxy. |

## HTTP surface and integrations

| Variable | Default | What it does |
|---|---|---|
| `API_DOCS_ENABLED` | `false` | Serve `/docs`, `/redoc` and `/openapi.json`. Compose: `true`; set `false` where the stack faces users. |
| `SECURITY_HEADERS_ENABLED` | `true` | Security header baseline on every answer. `false` only behind a proxy that sets them. |
| `ERROR_REPORT_URL` | `""` | https endpoint that receives one POST per unhandled error: id, class, method, route. Never a traceback or body. |
| `ERROR_REPORT_SOURCE` | `""` | Name of this deployment in those reports. |
| `PUBLIC_API_URL` | unset | Public address of the backend: GitHub's webhook Payload URL and the base of Slack/Teams request URLs. |
| `CHAT_APP_URL` | `http://localhost:5173` | Frontend address chat approval messages link to. See [`docs/archive/design/CHAT_APPROVALS.md`](archive/design/CHAT_APPROVALS.md). |
| `WEBHOOK_ALLOW_PRIVATE_HOSTS` | `false` | Allow a company webhook or timestamping authority on a private address. |
| `OPENPROJECT_URL` | `http://localhost:8080` | OpenProject server for the PO's backlog tool. |
| `AUDIT_LOG_PATH` | `/app/.factory/logs` | Folder of the append-only `audit.jsonl`. The hash chain lives in Postgres. |
| `BENCH_REPOSITORY_DIR` | `""` | Checkout whose `bench/scorecards` the /bench page reads. Empty: the backend's own checkout, if any. |
| `SELF_HOST_REPOSITORY_DIR` | `""` | Checkout whose `scripts/self_host` the owner action `self_host_container_run` imports (read-only; `docs/design/owner-actions.md`). Empty: the backend's own checkout, else the repository at `AGENT_WORKSPACE_ROOT` (the compose file mounts it there). Neither found: the action is unavailable. |

## Models and the LLM proxy

| Variable | Default | What it does |
|---|---|---|
| `LLM_PROXY_URL` | `http://localhost:5100` | Where every model call goes. Compose: `http://llm-proxy:5100`. |
| `LLM_UPSTREAM_TIMEOUT_SECONDS` | `300` | Budget of one chat request at the proxy, fallbacks included. The backend waits this plus 15 s. Backend and proxy. |
| `LLM_THINKING` | `auto` | DeepSeek's thinking mode: `auto`, `on` or `off`. Policy in [models](contexts/models.md). Backend and proxy. |
| `LLM_THINKING_RESERVE_TOKENS` | `16384` | Added to a thinking call's `max_tokens` for the reasoning. |
| `LLM_PROXY_MAX_RETRIES` | `2` | Backend retries when the proxy cannot be reached. |
| `LLM_PROXY_RETRY_BACKOFF_SEC` | `1.0` | Backoff between those retries. |
| `AGENT_MODEL` | `""` | One model id for every role instead of the DeepSeek defaults. Use ids from the proxy's `GET /v1/models`. |
| `AGENT_MODEL_<ROLE>` | `""` | One role's model: `PO`, `ARCHITECT`, `SCRUM_MASTER`, `DEVELOPER`, `QA`, `SECOPS`, `TECH_WRITER`, `BACKEND_DEVELOPER`, `FRONTEND_DEVELOPER`, `DEVOPS`, `DESIGNER`, `DATA_ENGINEER`, `ENGRAM_PROCESSOR`. Coding roles fall back to `AGENT_MODEL_DEVELOPER`; the Reviewer reads `AGENT_MODEL_QA`. Compose forwards only the first seven and `ENGRAM_PROCESSOR`. |
| `AGENT_MODEL_POLICY` | `""` | Routing by task difficulty, JSON: `{"bands": {"low": "<model>", "high": "<model>"}, "label": "..."}`. A company's own policy replaces it. |
| `EDIT_FORMAT_BY_MODEL` | `""` | Edit format per model, JSON: `{"<model>": "auto" \| "whole" \| "search_replace", "*": ...}`. Empty: `auto`. |
| `MODEL_PROFILES` | `{}` | Per-model overrides of the profile, JSON by model name (`context`, `output`, `tool_call`, `reasoning`, `tool_rounds`, `result_max_chars`, `max_output_tokens`). Invalid JSON stops startup. Passed to the backend by `docker-compose.yml`. See [models](contexts/models.md#a-models-profile). |
| `MODELS_CATALOG_URL` | `https://models.dev/api.json` | Model catalogue source. |
| `MODELS_CATALOG_REFRESH_HOURS` | `24` | Catalogue refresh interval. `0` keeps the shipped snapshot and never calls out. |

Model choice, most specific first: an agent's own model, the company's model for the role, the company's default, `AGENT_MODEL_POLICY`, `AGENT_MODEL_<ROLE>`, `AGENT_MODEL`, the role default.

### Proxy only

| Variable | Default | What it does |
|---|---|---|
| `LLM_FALLBACK_PROVIDERS` | `""` | Comma-separated providers (`deepseek`, `qwen`) a failed request may move to. Empty: a request never leaves its provider. |
| `LLM_PROXY_WORKERS` | `2` | gunicorn workers. |
| `LLM_PROXY_THREADS` | `8` | Threads per worker (`gthread`). |
| `EMBEDDING_PROVIDER` | `local` | `local`: `multilingual-e5-small` on CPU, no outside call; falls back to the hash vector, reported degraded. `local-hash`: the hash vector on purpose. |
| `EMBEDDING_MODEL_DIR` | `models/multilingual-e5-small` next to `local_model.py` (image: `/app/models/multilingual-e5-small`) | Where `model.onnx` and `tokenizer.json` are. Outside the image, run `python llm-proxy/fetch_embedding_model.py` once. |
| `EMBEDDING_THREADS` | `2` | ONNX Runtime threads per worker. |
| `PROXY_PORT` | `5100` | Read only by `python app.py`; the image's gunicorn ignores it. |

### Telemetry

| Variable | Default | What it does |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | unset | OTLP/HTTP collector for spans (proxy and backend). Compose: `http://jaeger:4318`. |
| `OTEL_EXPORTER_OTLP_ENABLED` | unset | Truthy with no endpoint: export to `http://localhost:4318`. |
| `OTEL_SDK_DISABLED` | unset | Truthy: no export. |
| `OTEL_SERVICE_NAME` | the component's name | Service name on the backend's spans. |

## Agents and the sprint loop

| Variable | Default | What it does |
|---|---|---|
| `MAX_REWORKS` | unset (3) | Reworks a task gets, every cause together, before it is blocked. A company may set 0–10. |
| `AUTOPILOT_MAX_REWORK_CYCLES` | unset | Deprecated old name of `MAX_REWORKS`, read with a warning when that is unset. |
| `AUTOPILOT_QA_REWORK_ENABLED` | `true` | QA's "needs rework" sends the task back on its own. |
| `DISPATCH_REQUEUE_COOLDOWN_SECONDS` | `90` | Minimum delay before a To do item is dispatched again. |
| `INTEGRATION_RETRY_SECONDS` | `60` | How often a sprint's integration the daemon could not take is retried. |
| `INTEGRATION_MAX_CONCURRENT` | `2` | Integrations at once per process (at least 1). |
| `RUN_WINDOW_RECHECK_SECONDS` | `300` | Longest sleep of the run-window worker (at least 60). The window itself is a company setting. |
| `WORKSPACE_WRITE_TIMEOUT_SECONDS` | `30` | How long the task worker waits for the daemon to confirm writes. |
| `ARCHITECT_CONSULT_ENABLED` | `true` | Consult the Architect before high-priority, design-sensitive or reworked tasks. |
| `ARCHITECT_TOOL_ROUNDS` | `6` | Rounds of read-only workspace tools (list, read, search, find_file; read-only Git and a document's text when announced) the Architect may use before its brief, with the research tools (when `RESEARCH_TOOLS_ENABLED`) and the task's record tools beside them. `0` asks in one call without tools. Also the reading rounds of the Architect writing or revising an architecture (step `arch_author`), beside its research tools, which keep their own `RESEARCH_TOOL_ROUNDS`; `0`, or no daemon or project path: the research tools alone. |
| `ARCHITECT_EDITOR_MODE` | `false` | A hard Developer task gets a numbered edit plan from the Architect. Needs `ARCHITECT_CONSULT_ENABLED`. |
| `ARCHITECT_EDITOR_MIN_DIFFICULTY` | `4` | Lowest difficulty (1–5) for editor mode. |
| `AGENT_GRAPH_ENABLED` | `false` | The internal LangGraph producer. Off, `POST /v1/generate` answers 409. Compose default `false`. |
| `CONSOLE_PRODUCER_TIMEOUT_SECONDS` | `600` | A console producer's wall clock (daemon caps at 900). |
| `CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES` | `10` | How long a console producer skips the Claude CLI after a quota stop with no reset time. |
| `ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES` | `30` | How long a coding CLI missing from the sandbox is not offered again. |

A company's own limits (`tool_rounds`, `result_max_chars`, `max_output_tokens`, `max_reworks`, `sprint_budget_cents`, `budget_warn_percent`) are set by an Owner on the Models page and win over the deployment's values; ranges in `services/contracts/company_limits.py`.

### Management agents

All off by default: each item costs an LLM call. See [management](contexts/management.md).

| Variable | Default | What it does |
|---|---|---|
| `MANAGER_AGENT_ENABLED` | `false` | The Manager agent suggests inbox decisions and acts alone only where the company's policy allows. |
| `MANAGER_AGENT_INTERVAL_SECONDS` | `60` | How often it reads the inbox. |
| `REVIEWER_AGENT_ENABLED` | `false` | The Reviewer agent suggests a decision on other producers' runs. |
| `REVIEWER_AGENT_INTERVAL_SECONDS` | `60` | How often it looks. |
| `REVIEWER_VERIFY_GRACE_SECONDS` | `60` | How long an unverified run waits for the Verifier first. |
| `SUPPORT_AGENT_ENABLED` | `false` | The Support agent triages new help-desk tickets. |
| `SUPPORT_AGENT_INTERVAL_SECONDS` | `60` | How often it looks. |
| `MANAGER_BRIEF_INTERVAL_HOURS` | `24` | How often the client's brief is mailed (needs the Manager agent and `SMTP_HOST`). |
| `MANAGER_BRIEF_CHECK_SECONDS` | `300` | How often the brief loop checks for a due brief. |

### Mail

Nothing is sent unless `SMTP_HOST` is set. Also used for email copies of notifications.

| Variable | Default | What it does |
|---|---|---|
| `SMTP_HOST` | `""` | Mail server. |
| `SMTP_PORT` | `587` | Port. |
| `SMTP_USER` | `""` | User. |
| `SMTP_FROM` | `""` | Sender address. |
| `SMTP_STARTTLS` | `true` | Use STARTTLS. |
| `SMTP_SSL` | `false` | Use implicit TLS. |
| `SMTP_TIMEOUT_SECONDS` | `20` | Timeout of one connection. |

### Self-improvement

Design: [`docs/design/rsi-loop.md`](design/rsi-loop.md). Both need the company to have protected paths on (an Owner setting).

| Variable | Default | What it does |
|---|---|---|
| `AUTO_SELF_IMPROVEMENT_ENABLED` | `false` | At autonomy 3, a closed sprint gets a model-planned follow-up. Never runs for a company with protected paths on. |
| `RSI_ENABLED` | `false` | The self-improvement cycle. Off, no cycle starts and a running one stops. |
| `RSI_CYCLES_PER_DAY` | `1` | Cycles per company and UTC day. |
| `RSI_STOP_AFTER_CYCLES_WITHOUT_KEEP` | `3` | Cycles in a row without a keep before an Owner must resume. |
| `RSI_MAX_DIFF_FILES` | `20` | Diff size cap in files; above it a person decides. |
| `RSI_MAX_DIFF_LINES` | `600` | Diff size cap in lines. |
| `RSI_FIRST_SPRINT_TOKENS` | `0` | Reported tokens of the first manual sprint. `0` (unknown): no cycle starts. |
| `RSI_CYCLE_TOKEN_BUDGET` | `0` | A cycle's token budget, at most twice `RSI_FIRST_SPRINT_TOKENS`. `0`: that cap. |
| `RSI_FILES_ROOT` | `""` | Folder of the cycles' work folders, where the backend hashes kept files itself. Empty: hashes stay claims. |

## Prompts and tools

| Variable | Default | What it does |
|---|---|---|
| `DEVELOPER_TOOLS_ENABLED` | `true` | The Developer's workspace tools (list, read, search, run tests). |
| `DEVELOPER_TOOL_ROUNDS` | `5` | Tool rounds before delivering. Ceiling 50. |
| `QA_REVIEW_TOOL_ROUNDS` | `3` | QA's read-only tool rounds (list, read, search, find_file) on the delivery before its verdict. `0`: one call without tools. |
| `PLANNING_TOOL_ROUNDS` | `3` | The PO's read-only tool rounds (list, read, search, find_file) on the project before its sprint plan (`contracts/tool_steps`, step `plan`), the plan from an approved specification included. `0`, or no daemon or project path: one call without tools. Also the reading rounds of the PO writing a specification (step `spec_author`), beside its research tools, which keep their own `RESEARCH_TOOL_ROUNDS`; `0`, or no daemon or project path: the research tools alone. |
| `SCRUM_TRIAGE_TOOL_ROUNDS` | `3` | The ScrumMaster's read-only tool rounds (list, read, search, find_file) on the blocked task's root before its decision (`contracts/tool_steps`, step `scrum_decision`). `0`, or no daemon or project path: one call without tools. |
| `BOARD_TOOLS_ENABLED` | `true` | The record tools `board__task` (a task's history) and `board__evidence` (a run's checks, files and tools offered), read-only and scoped to the turn's task and its dependencies (the QA review, the Architect's brief, a rework), the blocked task's sprint (the ScrumMaster's triage) or the company's board (the PO's sprint plan). They share the turn's rounds and are never offered to a turn without its reads. |
| `DEVELOPER_MAX_OUTPUT_TOKENS` | `0` | Cap on the Developer's answer. `0`: the provider's default. Never above the model's limit. |
| `MCP_MAX_TOOL_ROUNDS` | `6` | MCP tool rounds before the model must answer. |
| `MCP_TOOL_TIMEOUT_SECONDS` | `60` | One MCP call. |
| `MCP_CONNECT_TIMEOUT_SECONDS` | `20` | One MCP connection. |
| `MCP_RESULT_MAX_CHARS` | `4000` | How much of a tool result the model sees. Ceiling 100000. |
| `MCP_STDIO_ALLOWED_COMMANDS` | `""` | Absolute paths a company's stdio MCP server may spawn. Empty disables stdio servers. |
| `MCP_HTTP_ALLOWED_HOSTS` | `""` | Hosts an MCP server may reach over plain http. |
| `TOOL_HISTORY_KEEP_ROUNDS` | `3` | Older tool results go to the model as one line each. `0` keeps all whole. |
| `TOOL_HISTORY_MAX_CHARS` | `120000` | An old `workspace__read` still the latest view of its lines stays whole while all tool results in the history fit this many characters; past it the oldest reads become one line. `0`: no bound. |
| `EDIT_LINT_ENABLED` | `true` | Lint a file after `workspace__edit`, and offer `workspace__lint` in execution turns. |
| `EDIT_LINT_TIMEOUT_SECONDS` | `60` | Timeout of that lint. |
| `REPO_MAP_MAX_CHARS` | `3000` | Repository map in prompts. `0` leaves it out. |
| `CODE_GRAPH_MAX_CHARS` | `2500` | "Related code" section. `0` leaves it out. |
| `CODE_GRAPH_STORE` | `postgres` | Where the code graph is kept: `postgres`, `neo4j` (only with `NEO4J_ENABLED`) or `off`. |
| `SPRINT_BOARD_MAX_CHARS` | `2000` | "Sprint board" section. `0` leaves it out. |
| `WIKI_ENABLED` | `true` | The project wiki section and its compilation at sprint close. See [`docs/archive/design/PROJECT_WIKI.md`](archive/design/PROJECT_WIKI.md). |
| `WIKI_MAX_CHARS` | `3000` | Wiki section size. `0` leaves it out. |
| `WIKI_COMPILE_MAX_PAGES` | `5` | Pages compiled per sprint close. |
| `WIKI_PAGE_MAX_CHARS` | `6000` | Longest accepted compiled page. |
| `PROMPT_BUDGET`, `PROMPT_BUDGET_<ROLE>` | unset | Developer prompt budget in estimated tokens; over it, the least needed sections are dropped. Unset or `0`: nothing is cut. |
| `PROMPT_SECTIONS_OFF` | `""` | Comma-separated prompt sections to leave out, for bench comparisons (for example `changelog_diff`, the changelog writer's "read the diff first" section). |
| `WORKSPACE_SNAPSHOT_ENABLED` | `true` | The workspace snapshot in the Developer's prompt. |
| `WORKSPACE_SNAPSHOT_CACHE_TTL` | `60` | Snapshot cache life in seconds (at least 10). |
| `WORKSPACE_SNAPSHOT_MAX_FILES` | `4000` | Files scanned (at least 20). |
| `WORKSPACE_SNAPSHOT_TOP_RELEVANT` | `5` | Most relevant files shown (at least 3). |
| `WORKSPACE_SNAPSHOT_LINES_PER_FILE` | `60` | Lines per file (at least 20). |
| `WORKSPACE_SNAPSHOT_LAYOUT_ENTRIES` | `30` | Layout entries (at least 15). |
| `WORKSPACE_EVIDENCE_LINES_PER_FILE` | `80` | Lines per file in evidence (at least 20). |
| `WORKSPACE_EVIDENCE_MAX_FILE_BYTES` | `65536` | Largest file read for evidence (at least 2048). |

### Research and grounding

| Variable | Default | What it does |
|---|---|---|
| `RESEARCH_TOOLS_ENABLED` | `true` | Research tools: documentation, registries and OSV, fetched by the backend. |
| `RESEARCH_ROLES` | `Architect,PO,SecOps,TechWriter,Designer` | Roles that get them, by catalogue name: a stored alias counts (`Security` is SecOps). |
| `RESEARCH_ALLOWED_HOSTS` | `""` | Extra hosts; only these may resolve to a private address (a mirror). |
| `RESEARCH_TOOL_ROUNDS` | `6` | Research rounds per task. |
| `RESEARCH_TIMEOUT_SECONDS` | `20` | One fetch. |
| `RESEARCH_FETCH_MAX_BYTES` | `524288` | A page is cut here. |
| `RESEARCH_PYPI_URL` | `https://pypi.org/pypi` | PyPI or a mirror. |
| `RESEARCH_NPM_URL` | `https://registry.npmjs.org` | npm or a mirror. |
| `RESEARCH_CRATES_URL` | `https://crates.io/api/v1/crates` | crates.io or a mirror. |
| `RESEARCH_GO_URL` | `https://proxy.golang.org` | Go proxy or a mirror. |
| `RESEARCH_OSV_URL` | `https://api.osv.dev` | OSV or a mirror. |
| `GROUNDING_LOOKUPS_ENABLED` | `true` | Look up each new dependency at its registry for the Verifier's `grounded` check. |
| `GROUNDING_MAX_LOOKUPS` | `20` | Lookups per delivery. |

## Delivery: tests, sandbox and application check

Agent-written code runs on the daemon's machine. What each check records: [evidence](contexts/evidence.md) and [workspace](contexts/workspace.md).

**Sandbox floor.** `DELIVERY_SANDBOX` defaults to `container`. A company may choose `host` only with an Owner's acknowledgement. Set `FABRIQUE_SANDBOX_REQUIRED=container` on the daemon's machine to refuse the host for every request, whatever the backend asks. A company's `command` standards activate only under `container` or `bwrap`.

| Variable | Default | What it does |
|---|---|---|
| `DELIVERY_TESTS_ENABLED` | `true` | Run the project's tests after each delivery. Off, no code delivery can be verified; a company runs so only with an Owner's acknowledgement. |
| `DELIVERY_TEST_COMMAND` | `""` | Test command. Empty: detected from the stack profile. |
| `DELIVERY_TEST_RUN_SECONDS` | `600` | One test run. Daemon ceiling 900. |
| `DELIVERY_TEST_TIMEOUT_SECONDS` | `130` | Backend wait; the larger of this and the run limit plus 30 s. |
| `DELIVERY_SANDBOX` | `container` | `container`, `bwrap` or `host`. A company may set its own. |
| `DELIVERY_SANDBOX_IMAGE` | `""` | One image for every project. Empty: the daemon builds each project's image from its stacks. |
| `STACK_IMAGE_BUILD_TIMEOUT_SECONDS` | `1900` | Backend wait for that build. |
| `DELIVERY_SANDBOX_NETWORK` | `false` | Network in the sandbox. |
| `DELIVERY_SANDBOX_CPUS` | `2.0` | CPU limit. |
| `DELIVERY_SANDBOX_MEMORY_MB` | `2048` | Memory limit. |
| `DELIVERY_SANDBOX_PIDS` | `512` | Process limit. |
| `DELIVERY_SANDBOX_CACHE` | `false` | Per-project package cache volume in container mode. |
| `DELIVERY_SANDBOX_ENV` | `true` | Per-project environment volume (virtualenvs, `GOPATH`), writable only by an install. |
| `DELIVERY_INSTALL_ENABLED` | `true` | Install dependencies when a manifest changed. |
| `DELIVERY_INSTALL_TIMEOUT_SECONDS` | `600` | One install. Daemon ceiling 900. |
| `DELIVERY_INSTALL_NETWORK` | `true` | Network for the install, whatever `DELIVERY_SANDBOX_NETWORK` says. |
| `DELIVERY_INSTALL_COMMAND` | `""` | Overrides the profile's install command. |
| `DELIVERY_LINT_COMMAND` | `""` | Overrides the lint command. |
| `DELIVERY_START_COMMAND` | `""` | Overrides the start command. |
| `DELIVERY_E2E_COMMAND` | `""` | Overrides the e2e command. |
| `DELIVERY_APP_PORT` | `0` | Application port. `0`: the profile's. |
| `DELIVERY_APP_PROBE_PATH` | `/` | Path probed once the app is up. |
| `DELIVERY_APP_CHECK_ENABLED` | `false` | Start the app in the sandbox, probe it, run the security probe and e2e. Off: it runs agent code. |
| `DELIVERY_APP_READY_TIMEOUT_SECONDS` | `60` | Wait for the app's port. |
| `DELIVERY_E2E_TIMEOUT_SECONDS` | `300` | One e2e run. |
| `DELIVERY_BROWSER_IMAGE` | `""` | Image for browser e2e runs; pin by digest. Built from `vscode-daemon/images/browser` or published by `images.yml`. |
| `DELIVERY_BROWSER_IMAGES` | `""` | Browser image per Playwright release: `1.64.1=<image>@sha256:…,…`. An entry without a digest is refused. |
| `DELIVERY_FULL_LOGS` | `true` | Upload whole outputs to the JetStream object store `FABRIQUE_RUN_LOGS` and record their sha256. |
| `DELIVERY_FULL_LOG_MAX_MB` | `50` | Largest object the backend reads back. |
| `FULL_LOG_RETENTION_DAYS` | `30` | Object life. `0` keeps them. |
| `FULL_LOG_BUCKET_MAX_MB` | `4096` | Bucket size; oldest go first. `0`: no limit. |
| `DAEMON_HEARTBEAT_STALE_SECONDS` | `60` | No heartbeat this long: the daemon is `stale`. |
| `DAEMON_HEARTBEAT_OFFLINE_SECONDS` | `90` | No heartbeat this long: `offline`. |
| `FORGE_POLICY_MAX` | `publish` | Highest forge policy any company may have: `none`, `fetch` or `publish`. Unknown reads as `none`. Also read by the daemon; an air-gapped install sets `none` on both. |
| `AGENT_WORKSPACE_ROOT` | unset | Where a company without a discovered project folder writes. Compose: `${CONTAINER_WORKSPACE_ROOT:-/workspace}`. |

A company overrides the stack profile, sandbox and allowed variables through its own settings (`/v1/companies/{id}/stack_profile`, `/sandbox`).

## Workspace daemon

Read by `vscode-daemon/` on the computer that holds the projects. Protocol: [`vscode-daemon/protocol/PROTOCOL.md`](../vscode-daemon/protocol/PROTOCOL.md).

| Variable | Default | What it does |
|---|---|---|
| `WORKSPACE_ROOT` | `~/factory-workspaces` | Folder for a request that names no workspace root. |
| `FABRIQUE_HEARTBEAT_SECONDS` | `20` | Heartbeat interval. Range 1–25. |
| `FABRIQUE_SANDBOX_REQUIRED` | `""` | `container`: refuse the host sandbox for every request. |
| `FABRIQUE_SANDBOX_RUNTIME` | unset | Container runtime. Unset: first of `docker`, `podman` on the `PATH`. |
| `FABRIQUE_STACK_BUILD_SECONDS` | `1800` | One stack image build (at least 60). |
| `FABRIQUE_SANDBOX_CACHE` | `on` | `off` refuses the package cache. |
| `FABRIQUE_SANDBOX_ENV` | `on` | `off` refuses the environment volume. |
| `FABRIQUE_SANDBOX_CACHE_STATE` | `~/.fabrique-daemon/sandbox-cache` | Failed installs and quarantine per project. |
| `FABRIQUE_FULL_LOG_MAX_MB` | `50` | Largest uploaded output per stream. Range 1–1024. |
| `FABRIQUE_COMMAND_DENY` | unset | Comma-separated command patterns the daemon always refuses; a request cannot relax it. |
| `FABRIQUE_SELF_HOST_SOURCE` | the La Fabrique repository the daemon runs from | The repository `workspace.self_host.checkout` copies for an owner action (PROTOCOL.md §5.9). The request never names it. |
| `FABRIQUE_SELF_HOST_ROOT` | `~/fabrique-self-host` | The folder the self-hosting snapshots go under; the request names only a folder inside it. |
| `FABRIQUE_SELF_HOST_LAND_TIMEOUT` | `5400` | Seconds the owner action `land_branch` lets `scripts/land.sh` run on `FABRIQUE_SELF_HOST_SOURCE` (its `check.sh full` included) before the daemon kills its process group (PROTOCOL.md §5.11). land.sh runs without this daemon's own `LAND_*` variables, with `LAND_OWNER=1` only when the request asks, and with `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` unless the daemon's environment sets another, so the proxy's Poetry install in `check.sh full` never waits on a desktop keyring. `push_main` uses this machine's own git credentials for `origin` (an ssh key without a passphrase, an agent or a credential helper; nothing prompts). |
| `FABRIQUE_EXEC_SHELL` | unset | Shell for host commands. Unset: `/bin/zsh` if present, else `/bin/sh`; on Windows `COMSPEC`. |
| `FABRIQUE_DAEMON_STATE` | `~/.fabrique-daemon` (journal in `deliveries/`) | Queued delivery results, replayed on redelivery. |
| `FABRIQUE_SNAPSHOT_STATE` | `~/.fabrique-daemon/snapshots` | Pre-delivery snapshots used to undo a delivery. |
| `FABRIQUE_EDIT_STATE` | `~/.fabrique-daemon/edits` | Original bytes of files edited in a tool loop. |
| `FABRIQUE_MAP_STATE` | `~/.fabrique-daemon/maps` | Repository map cache. |
| `CONSOLE_ASSISTANT_COMMANDS` | `copilot,claude` | CLIs reported on `workspace.capabilities`, for older backends. |

The daemon also reads `NATS_URL`, `NATS_NAMESPACE`, the bus credentials and TLS variables, and `FORGE_POLICY_MAX` (above).

## Retention and backups

| Variable | Default | What it does |
|---|---|---|
| `RETENTION_OUTBOX_DAYS` | `7` | Janitor: delivered outbox events. |
| `RETENTION_AGENT_RUNS_DAYS` | `0` | Janitor: superseded run versions no person touched. `0` keeps all. |
| `RETENTION_KPI_TURNS_DAYS` | `0` | Janitor: KPI turn rows outside the hash chain. `0` keeps all. |
| `RETENTION_NOTIFICATIONS_DAYS` | `90` | Janitor: personal notifications. Range 1–365; invalid keeps 90. |
| `JANITOR_INTERVAL_SECONDS` | `86400` | Janitor run interval. |
| `BACKUP_DIR` | `./backups` | `scripts/backup.sh` output folder. |
| `BACKUP_KEEP_DAYS` | `14` | Older backups are removed. `0` keeps all. |
| `PG_CONTAINER` | `factory-postgres` if running | Container to run `pg_dump` in. |

Task events and audit rows are never deleted. Archiving: [`docs/OPERATIONS.md`](OPERATIONS.md).

## Compose only

| Variable | Default | What it does |
|---|---|---|
| `CONTAINER_WORKSPACE_ROOT` | `/workspace` | Where compose mounts this repository in the backend and daemon containers. |
| `PROJECTS_ROOT` | `$HOME` (or `/tmp`) | Host folder the optional Linux daemon container (`--profile docker-daemon`) mounts at the same path. |
| `NEO4J_AUTH` | `neo4j/factory` | Neo4j container credentials; must match `NEO4J_PASSWORD`. |

Fixed in `docker-compose.yml` for third-party containers, not meant for `.env`: `COLLECTOR_OTLP_ENABLED` (Jaeger), `NEO4J_ACCEPT_LICENSE_AGREEMENT` and `NEO4J_PLUGINS` (Neo4j), `HOME`.

## The desktop app

Read by the Electron main process (`frontend/electron/`).

| Variable | Default | What it does |
|---|---|---|
| `BACKEND_URL` | `http://localhost:8000` | Backend the app's local server forwards `/v1/*` to. Must be https, or http on localhost. |
| `ELECTRON_DEV_URL` | `http://localhost:5173` | Vite dev server for `npm run electron:dev`. |

## Removed, ignored if set

Still declared so an old `.env` loads; the backend logs each one set at startup. Remove them: `CONSOLE_ASSISTANTS_ENABLED`, `CONSOLE_ASSISTANT_TIMEOUT_SECONDS`, `CONSOLE_ASSISTANT_STRATEGY`, `CONSOLE_ASSISTANT_ALLOWED_ROLES`, `COPILOT_CLI_COMMAND_TEMPLATE`, `CLAUDE_CLI_COMMAND_TEMPLATE`, `CLAUDE_CLI_ALLOWED_TOOLS`, `CLAUDE_CLI_APPEND_SYSTEM_PROMPT`, `CLAUDE_CLI_HIGH_VALUE_ROLES`, `OPENPROJECT_TOKEN_DEV`. The janitor warns about `RETENTION_GLASSROOM_DAYS`.

## Legacy, unused

| Variable | Default |
|---|---|
| `GEMINI_API_KEY` | unset |

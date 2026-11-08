# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**La Fabrique** is an AI-powered autonomous software development platform. It orchestrates a multi-agent team (PO, Architect, ScrumMaster, Developer, QA, SecOps, TechWriter) using LangGraph to execute software sprints — from backlog to code generation and execution — inside a user's VS Code workspace.

> **Direction adopted (2026-09-19).** The repositioning in
> [`docs/POSITIONING.md`](docs/POSITIONING.md), tracked in issue #38, is the
> project's direction: the agents become interchangeable producers, and the
> product becomes the layer that verifies their work and records evidence. The
> description above still reflects what the code does today, not where it is
> going. Read the positioning document before planning new work, and note the
> design rules in it — in particular that a
> claim and an observation must never share a field, that an agent's report of
> its own test run is a claim rather than an observation, and that hashes and
> signatures are never accepted from the party being recorded.
>
> **Amendment (2026-10-18).** The factory manages itself as a company of AI
> agents that follow procedures, talk to each other and learn from
> retrospectives. The evidence keeps the last word: messages between agents are
> claims, and procedure changes are kept or reverted by deterministic metrics.
> See the amendment at the end of `docs/POSITIONING.md` for the design and the
> order of work.
>
> **Reference implementations (2026-11-07).** Before building a piece of the
> factory, study how the agents that already ship it did it and take as much as
> can be taken, design first and code where the licence allows: GitHub Copilot
> coding agent (CI as the evidence of a run), OpenAI Codex cloud (sandboxed
> runs with the commands and logs cited in the pull request), OpenHands (an
> open-source, self-hostable Docker runtime per session and the trajectory as
> an event stream; the MCP loop here already follows its `software-agent-sdk`),
> Devin (session replay, parallel agents), Kiro by Amazon (spec-driven:
> requirements with EARS criteria, design and tasks before code) and Factory
> (Droids: enterprise execution, local or cloud, with compliance). Name what
> was taken, and from where, in the pull request.

## Running the System

```bash
# Full stack (preferred)
docker compose up -d

# Individual services
docker compose up backend -d
docker compose up frontend -d
docker compose up llm-proxy -d

# Workspace daemon: runs natively on the computer that holds your projects
./scripts/start-daemon.sh        # macOS / Linux
.\scripts\start-daemon.ps1       # Windows (PowerShell)
```

The daemon is the only component that touches the user's files, so the folder
chosen in "New company" is a path on that computer (`/Users/ana/code/app`,
`/home/ana/src/app`, `C:\Users\ana\source\repos\app`). The backend never needs
to see the user's disk; `docker compose --profile docker-daemon up` is an
opt-in Linux-only alternative that mounts `PROJECTS_ROOT` instead.

- Backend API: `http://localhost:8000` — OpenAPI docs at `/docs`
- Frontend: `http://localhost:5173` (Vite dev) or `http://localhost:3000` (production)
- LLM Proxy: `http://localhost:5100`

**Frontend dev server (hot reload):**
```bash
cd frontend && npm install && npm run dev
```

**Frontend production build:**
```bash
cd frontend && npm run build
```

## Required Environment Variables

Copy `.env.example` to `.env` and fill in:
- `GEMINI_API_KEY` — Google AI Studio key (used by Architect/PO agents)
- `DEEPSEEK_API_KEY` — DeepSeek key (used by Developer/QA agents)
- `POSTGRES_PASSWORD`, `NEO4J_PASSWORD` — database credentials
- `NEO4J_AUTH=neo4j/<password>` must match `NEO4J_PASSWORD`
- `LLM_PROXY_INTERNAL_TOKEN` — optional, shared by the backend and the LLM proxy.
  An agent whose model is `conn:<id>:<model>` uses that company provider
  connection: the backend sends its URL and key in `X-Provider-Connection`, and
  the proxy (`llm-proxy/connections.py`) honours it only with this token, with
  no fallback to the deployment's own keys. Empty disables company connections.
- `MODELS_CATALOG_URL`, `MODELS_CATALOG_REFRESH_HOURS` — optional. The model
  catalogue (models.dev, `services/model_catalog.py`) starts from the snapshot in
  `backend/app/data/models_dev.json` (`scripts/update_models_snapshot.py`) and
  refreshes in the background every 24 hours by default; `0` never calls out.
- `FERNET_SECRET_KEY` — optional. Encrypts `ProjectCredential.encrypted_value`
  at rest. Without it those values are stored with a `plaintext:` prefix and the
  backend warns at startup. Generate one with:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `JWT_SECRET_KEY` — required for authentication. Login returns 503 until configured.
  Generate with: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `AUTH_REQUIRED` — set to `true` to enforce authentication on sensitive routes
- `SUPPORT_ADMIN_EMAILS` — optional, comma-separated account emails. Those users
  see and triage every support ticket and download the support logs (`/support`
  in the frontend, flagged by `support_admin` in `/v1/auth/me`); everyone else
  sees only the tickets reported with their account email.
- `SUPPORT_AGENT_ENABLED` — optional, default `false`. The Support agent
  (`services/support_agent.py`) triages new help-desk tickets every
  `SUPPORT_AGENT_INTERVAL_SECONDS` (60): a suggested severity and category,
  stored beside what the reporter chose, and a first reply the reporter sees.
  Off by default because reporting a bug is public and each ticket costs an
  LLM call. A support admin decides a ticket's severity and category in the
  help desk; the decision is stored apart from the reporter's choice and the
  agent's suggestion (`decided_*`, stamped with who changed it only when it
  changes; an empty value clears it), `GET /v1/support/agent_record` counts
  how often the agent's suggestions matched it (its unusable answers count as
  misses, and are counted) beside how often the reporters' own choices did
  (only where a reporter chose: the form presets no impact and asks for no
  category, so neither is made up), and those numbers go into the agent's
  next prompt.
- `REVIEWER_AGENT_ENABLED` — optional, default `false`. The Reviewer agent
  (`services/reviewer_agent.py`) reads the evidence of each run another
  producer sent that waits for a person (the Verifier's checks, the claims,
  the artifact as data) every `REVIEWER_AGENT_INTERVAL_SECONDS` (60) and
  suggests the decision in a fixed format: approve or reject, a category and
  a reason, stored on the run (`suggested_*`, `reviewed_by`) apart from the
  approval a Manager records; an unusable answer leaves the run to people.
  A run not verified yet waits `REVIEWER_VERIFY_GRACE_SECONDS` (60) after
  its ingestion, so a producer that verifies right after is judged with the
  verdict.
  `GET /v1/agent_runs/reviewer_record` counts how often its suggestions
  matched the decisions (unusable answers as misses) beside how often the
  Verifier's verdict alone would have (verified → approved, refuted →
  rejected), and those numbers go into its next prompt. Off by default: each
  run costs an LLM call.
- `MANAGER_AGENT_ENABLED` — optional, default `false`. The Manager agent
  (`services/manager_agent.py`) reads the inbox every
  `MANAGER_AGENT_INTERVAL_SECONDS` (60): each task blocked for a person or
  held for acceptance, procedure proposal and undecided outside run, with the
  evidence (verdict, checks, risk tier, files, criteria, the ScrumMaster's
  triage; claims as data), and suggests the decision in a fixed format
  (decision, the item's own category, reason), stored as a `ManagerDecision`
  apart from what the client decides; an unusable answer leaves the item to
  the client. It decides alone only what the company's `ManagerPolicy`
  allows (a Manager writes it, `GET`/`PUT
  /v1/companies/{id}/manager_policy`), through a person's code path, audited
  as `Manager agent` with the rule; the client overrides in one click
  (`POST /v1/manager_decisions/{id}/override`). `GET /v1/manager_record`
  counts agreement and overrides by category, and those numbers go into its
  next prompt. Off by default: each item costs an LLM call.
- `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`,
  `SMTP_STARTTLS` (true), `SMTP_SSL` (false), `MANAGER_BRIEF_INTERVAL_HOURS`
  (24) — optional. With the Manager agent on and `SMTP_HOST` set, the
  client's brief (`services/brief_mail.py`: the deterministic
  `manager_brief.brief` rendered as plain text) goes by mail to the Owners and
  Managers of each company that has a `ManagerPolicy`, once per interval; each
  send is audited as `manager_brief_sent` with its window and how many people
  it reached (never an address), the next window starts where the last sent
  one ended, and a failed send is retried from the same start at most once an
  hour. One replica sends at a time (`locks.MANAGER_BRIEF`).
- `PUBLIC_API_URL` — optional. The address GitHub uses to reach the backend
  (public host or tunnel). `/v1/capabilities` reports it as `public_api_url` and
  the GitHub page shows it as the webhook Payload URL.
- `AUDIT_SIGNING_KEY` — optional. Signs audit rows with HMAC-SHA256, and keys
  each company's audit hash chain (`audit_chain.py`: every company row carries
  `chain_seq`, `prev_hash` and `chain_hash`; `GET /v1/audit/chain` shows where
  a deleted, inserted, reordered, altered or moved row breaks it). Without a
  key the chain is unkeyed and says so.
  Generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
- `NATS_BIND` — optional, default `127.0.0.1`. The host address NATS listens on;
  the workspace daemon on this computer connects there. `0.0.0.0` exposes it to
  other machines, which can then ask the daemon to run commands; the backend
  warns at startup when it is not loopback and the bus has no credentials.
- `NATS_BACKEND_PASSWORD`, `NATS_DAEMON_PASSWORD` — optional, both or neither
  (issue #327). With both, `nats/start.sh` starts the broker with
  `nats/nats-server.conf`, one user per component, written by
  `scripts/nats_config.py` (`--check` fails when the committed file is stale,
  `--namespace X` for a namespaced deployment): `backend` publishes
  `workspace.>`, `task.>`, `glassroom.>`, `audit.>`, `system.>` and the
  JetStream API, its replies under `_INBOX.backend`; `daemon` subscribes to
  `workspace.>` and `_INBOX.daemon.>`, answers under `_INBOX.backend.>`
  (never `allow_responses`, which would let it reply to a `workspace.*`
  subject it set itself) and publishes only `workspace.delivered`, `glassroom.*`
  and its own consumer's JetStream calls on the deliveries stream, so it
  cannot send a request (`workspace.execute`…), assign a task, read the
  backend's replies or create, change or delete a stream (it waits for the
  backend's). Only one set stops the broker; neither keeps the old
  unauthenticated bus. The passwords stay in the environment
  (`$NATS_BACKEND_PASSWORD` in the file). Compose gives the backend
  `NATS_USER=backend`; `scripts/start-daemon.*` turn `NATS_DAEMON_PASSWORD`
  into the daemon's user.
- `NATS_USER` / `NATS_PASSWORD`, `NATS_CREDS_FILE` (wins), `NATS_NKEY_SEED`
  (a seed file) — how the backend (`app/nats_auth.py`) and the daemon
  (`vscode-daemon/nats_connect.py`, the same rules) authenticate;
  `start-daemon.sh --creds FILE` / `.ps1 -Creds FILE`, and `nkeys` is installed
  only for a creds file or a seed. Unset, no authentication as before. No log
  line or `/health` field carries a secret.
- `NATS_TLS`, `NATS_TLS_CA`, `NATS_TLS_CERT`, `NATS_TLS_KEY`, `NATS_TLS_VERIFY`
  — optional. The broker requires TLS once `nats/certs` holds `server.crt` and
  `server.key` (`ca.crt` plus `NATS_TLS_VERIFY=true` also requires client
  certificates); `NATS_TLS=true` or `NATS_TLS_VERIFY=true` on the broker make
  those files required (`nats/start.sh` refuses to start without one, never
  falling back to plain text); `NATS_TLS=true` makes the backend and the daemon connect with
  TLS, `NATS_TLS_CA` pins the CA (the backend container reads
  `nats/certs/ca.crt`), `NATS_TLS_CERT`/`_KEY` present a client certificate.
  In the containers `nats/certs` is `/etc/fabrique-nats/certs`, and
  `NATS_BACKEND_TLS_CERT`/`_KEY` and `NATS_DAEMON_TLS_CERT`/`_KEY` name a
  client certificate's files there (compose turns them into those paths).
  `docs/OPERATIONS.md`, "Securing the bus", generates a self-signed CA.
- `PROJECTS_ROOT` — only for the optional Linux daemon container (`--profile
  docker-daemon`): the host folder holding your projects, mounted at the same path.
- `DELIVERY_TESTS_ENABLED` — optional, default `false`. After an agent's files are
  written, the daemon runs the project's tests (`DELIVERY_TEST_COMMAND`, or detected
  `npm test` / `pytest`) and the exit status is recorded as evidence; a failing run
  sends the task back for rework. It executes agent-written code on the daemon's machine,
  where `DELIVERY_SANDBOX` says: `host` (default, as before), `container` (an
  ephemeral container per run: worktree mounted, network off unless
  `DELIVERY_SANDBOX_NETWORK`, limits `DELIVERY_SANDBOX_CPUS` / `_MEMORY_MB` /
  `_PIDS`, image `DELIVERY_SANDBOX_IMAGE`, every capability dropped) or `bwrap`.
  A company sets its own (`Company.delivery_sandbox`, and `sandbox_env`: the
  variable names a sandboxed run may see) with `GET`/`PUT
  /v1/companies/{id}/sandbox` (Viewer / Manager). The daemon reports what it
  applied (`test_run.sandbox`) and the Verifier records it as the `sandbox`
  check (`services/sandbox.py`; `host` is a finding, never a refutation);
  `FABRIQUE_SANDBOX_REQUIRED=container` on the daemon's machine refuses the host
  for everything (`vscode-daemon/sandbox.py`).
  The daemon also applies a **command policy** before `workspace.execute`
  runs anything (`vscode-daemon/command_policy.py`, issue #357): the
  request's `command_policy` (`{"mode": "allow"|"deny", "allow": [...],
  "deny": [...]}`) and the daemon machine's own `FABRIQUE_COMMAND_DENY`
  (comma-separated patterns, which the request cannot relax). It splits a
  command into its simple commands, `$(...)`/backtick/`<(...)` substitutions
  (checked too, at most `MAX_DEPTH` deep) and redirections, honouring quotes;
  `allow` matches a pattern's words as a prefix of each command's words (never
  a substring) and refuses a redirection other than `2>&1`-style duplication
  or `/dev/null`; `deny` refuses a pattern found in the text or in the
  unquoted words; unreadable text or an unknown mode is refused. A refusal is
  `{"success": false, "refused": true, "error": <reason>}` and nothing runs.
  With neither configured every command runs as before. The backend does not
  send a company policy yet.
  `FABRIQUE_DAEMON_STATE` (daemon side, default `~/.fabrique-daemon/deliveries`)
  is where the daemon keeps each queued delivery's result until the job is
  acknowledged (`vscode-daemon/delivery_journal.py`): a redelivered job replays
  it instead of applying SEARCH/REPLACE edits a second time.
  `FABRIQUE_SNAPSHOT_STATE` (daemon side, default `~/.fabrique-daemon/snapshots`)
  holds the snapshot taken before each delivery without the Git workflow
  (`vscode-daemon/delivery_snapshot.py`, from opencode's snapshots): one bare
  Git store per project, named by the sha256 of its path, never the
  project's `.git`; a Manager undoes the delivery from it
  (`services/delivery_undo.py`). A queued delivery replays its snapshot id
  with the journalled result.
  In a sprint planned from a specification it also decides the **acceptance
  tests per criterion** (`services/acceptance.py`): the runner's per-test lines
  are read back into criterion ids (pytest gets `-v`; name a verbose command
  here for another runner, e.g. `jest --verbose`, `vitest --reporter=verbose`).
- **Stack profiles and the application check** (issue #315,
  `services/stack_profiles.py`, `services/app_check.py`,
  `vscode-daemon/app_run.py`). Per detected stack (Node, Python, Go, Rust,
  Java via Maven or Gradle, .NET, PHP, Ruby) a profile names the install,
  test, lint and start commands, the port and the probe path, from the
  daemon's snapshot; `DELIVERY_INSTALL_COMMAND`, `DELIVERY_LINT_COMMAND`,
  `DELIVERY_START_COMMAND`, `DELIVERY_E2E_COMMAND`, `DELIVERY_APP_PORT` (0:
  the profile's), `DELIVERY_APP_PROBE_PATH` (`/`) and `DELIVERY_TEST_COMMAND`
  override it for the deployment, and the company's own
  (`Company.stack_profile`, `GET`/`PUT /v1/companies/{id}/stack_profile`,
  Viewer / Manager) override both; an empty command turns that step off.
  The delivery tests run the resolved profile's test command (a company
  naming only `stack` gets that stack's runner); a detected profile has none
  without a suite (npm's placeholder `test` script, nothing for pytest to
  collect, no `phpunit.xml` or `tests/`, no `Rakefile` or `spec/`), and then
  the older detector looks for another runner's suite. Poetry and Pipenv
  projects run their test, lint and start commands through `poetry run` /
  `pipenv run`. With
  `DELIVERY_TESTS_ENABLED`: `DELIVERY_INSTALL_ENABLED` (true) installs the
  dependencies before the tests when a manifest the daemon wrote has a sha256
  other than the last successful install's in that folder
  (`Company.dependency_installs`, written only after an exit 0 and only for
  the manifests that install covers, `stack_profiles.installed_by`: a
  detected command its own stack's manifests in the project's root, a
  command the company or deployment named every one; the rest are kept as
  `not_covered`, and a delivery that changed only those runs no install and
  says why), through `workspace.execute` in the same sandbox, bounded by
  `DELIVERY_INSTALL_TIMEOUT_SECONDS` (600; the daemon caps a requested
  `timeout_seconds` at 900), with the network as `DELIVERY_INSTALL_NETWORK`
  says (true, for the registry) whatever `DELIVERY_SANDBOX_NETWORK` says:
  the Verifier's `dependencies_installed` check. `DELIVERY_APP_CHECK_ENABLED` (false: it runs agent code) has the
  daemon (`workspace.app.check`) start the application in the sandbox (host
  or container; bwrap and a port already in use are refused, a required
  sandbox is never replaced by the host, the command policy applies), wait
  for its port (`DELIVERY_APP_READY_TIMEOUT_SECONDS`, 60), GET the probe path
  (below 500 passes), run the profile's e2e command with `APP_URL`
  (`DELIVERY_E2E_TIMEOUT_SECONDS`, 300), stop it and reply with the log's
  tail: the `application_started` and `e2e_run` checks. The backend waits
  for the reply beyond those bounds for the container's launch (120 s) and
  teardown; the container is removed by name whatever happens after its
  launch; on the host the application's process group (its process tree on
  Windows, `taskkill /T /F`) is stopped, `stopped` holds only once the port
  no longer answers, and a host e2e command runs in a group of its own that
  is killed at its timeout and when it ends. It is skipped after a failed
  install or failed tests, and a sprint's integration runs the tests alone. A refuted install, start or e2e run sends
  the task back like failed tests (`delivery_gate.CODE_PROBLEMS`). In a
  container an install keeps only what it writes into the worktree.
- `MCP_MAX_TOOL_ROUNDS` (6), `MCP_TOOL_TIMEOUT_SECONDS` (60),
  `MCP_CONNECT_TIMEOUT_SECONDS` (20), `MCP_RESULT_MAX_CHARS` (4000) — the
  MCP tool loop (`services/mcp_tools.py`): how many rounds of tool calls a
  task may make before the model must answer, how long one call and one
  connection may take, and how much of a result the model is shown. Nothing
  to enable: a company with no MCP server configured runs as before.
- `DB_AUTO_UPGRADE` — optional, default `false`. Runs the Alembic migrations
  to head when the backend starts, for a local database run by hand. The
  container's entrypoint and the e2e run `alembic upgrade head` themselves,
  and the backend refuses to start on a database behind head
  (`app/migrations.py`, `CONTRIBUTING.md` §5).
- `RETENTION_OUTBOX_DAYS` (7), `RETENTION_AGENT_RUNS_DAYS`,
  `RETENTION_KPI_TURNS_DAYS`, `RETENTION_GLASSROOM_DAYS` (0, keep all) — what
  the janitor deletes (`backend/janitor/retention.py`): delivered outbox
  events; superseded run versions with their verifications (never the latest
  version, nor one a person reviewed, approved, commented on or published);
  KPI turn rows of `audit_log` outside a company's hash chain (a chained row
  is never deleted, and counted as kept); task events of closed sprints.
  There is no audit retention: `docs/OPERATIONS.md` says how to archive a
  chain with its rows. `BACKUP_DIR`, `BACKUP_KEEP_DAYS` (14) and
  `PG_CONTAINER` steer `scripts/backup.sh`.
- `DEVELOPER_TOOLS_ENABLED` (true), `DEVELOPER_TOOL_ROUNDS` (8) — the
  Developer's workspace tools (`services/developer_tools.py`): the roles that
  deliver files may list, read and search the project through the daemon, and
  run its tests with `DELIVERY_TESTS_ENABLED`, for that many rounds before
  delivering; every read is evidence.
- `WEBHOOK_ALLOW_PRIVATE_HOSTS` — optional, default `false`. A company's
  notification webhook (`services/webhooks.py`) must be an https URL whose host
  resolves to public addresses, checked when it is saved and before every post;
  `true` allows a private, loopback or link-local host (a receiver on the LAN).
- `RESEARCH_TOOLS_ENABLED` (true), `RESEARCH_ROLES` (Architect, PO, SecOps,
  TechWriter), `RESEARCH_ALLOWED_HOSTS` (empty), `RESEARCH_TOOL_ROUNDS` (6) —
  the research tools (`services/research_tools.py`): the roles that decide
  read official documentation, the package registries and OSV through the
  backend, from the default hosts plus these and the company's own; every
  source is evidence. `RESEARCH_PYPI_URL`, `RESEARCH_NPM_URL`,
  `RESEARCH_CRATES_URL`, `RESEARCH_GO_URL` and `RESEARCH_OSV_URL` may point
  at a mirror (the e2e points PyPI at its fake).

## Architecture Overview

```
React Frontend (Vite + TypeScript + TailwindCSS)
        ↓ REST / SSE
FastAPI Backend  ←→  LangGraph Agent Orchestration
        ↓                   ↓
    Postgres           NATS JetStream (pubsub)
    (pgvector)               ↓
    Neo4j             VS Code Daemon
    (knowledge graph)  (writes files, runs shell)
         ↓
    LLM Proxy (Flask, port 5100)
         ↓ ↓
    Gemini API    DeepSeek API
```

### Key Components

**`backend/app/main.py`** — The FastAPI app: lifespan (workers, JetStream, schema backfills), middleware and the routers it includes. The routes live in `backend/app/routers/` (`sprint`, `agents`, `agent_runs`, `inbox`, `messages`, `procedures`, `support`, `workspace`, `github`, `auth`, `enterprise`, `providers`, `model_settings`, `webhooks`).

**`backend/app/migrations.py`** / **`backend/alembic/`** — The schema's version. Alembic owns it: `alembic.ini` and `alembic/env.py` (the models are the target metadata; the URL is the backend's `POSTGRES_*`, or `ALEMBIC_DATABASE_URL`), `alembic/versions/0001_baseline.py` (the baseline: creates each table the models define and the database lacks, adds each column a table lacks, and repeats the old startup's `ALTER TABLE` fixes, indexes and backfills on Postgres, so an empty database, one from `db/init-db/init.sql` and one patched by an older backend all converge). `database.prepare_database` (startup) runs `require_schema_at_head`: a database behind head stops the start with both versions in the error; `DB_AUTO_UPGRADE=true` runs `upgrade_to_head` first. `schema_differences` is what `alembic check` reports (`include_object` leaves `vector_memory` and init.sql's other tables out). The container's `CMD` runs `alembic upgrade head` before uvicorn; the e2e runs `upgrade head` then `check` on Postgres; `backend-tests.yml` runs both on a SQLite file. How to write a migration: `CONTRIBUTING.md` §5.

**`backend/agents/graph.py`** — Optional LangGraph producer, disabled unless `AGENT_GRAPH_ENABLED=true`. Its runs are ingested as `AgentRun` records with `producer: "le-fabrique-graph"`.

**`backend/app/llm_proxy_client.py`** — Thin HTTP client wrapping the LLM proxy for token-budget-aware requests. `chat()` / `chat_async()` take a `model` id (default `"gemini-pro"`), a `role`, and an optional `bu_id`.

**`llm-proxy/app.py`** — Flask gateway that enforces per-company token budgets, routes by model-id prefix to the wired providers (Nebius Token Factory, Gemini, DeepSeek, Qwen), and refreshes each provider's catalogue hourly. Each answer says how its token count is known (`llm-proxy/usage.py`): `usage_status: reported` with the provider's figure in `usage`, or `estimated` with a characters/4 estimate apart in `usage_estimate` (charged to the quota, never shown as usage); the backend's `usage_status` keeps estimates out of the KPI averages.

**`vscode-daemon/daemon.py`** — The workspace daemon: a Python process that runs natively on the user's macOS, Linux or Windows machine (started by `scripts/start-daemon.*`, reconnects to NATS on its own). It answers `workspace.generate` (write files, reply with the sha256 read back from disk and the diff against the bytes that were there, `file_diff.py`; with a `git` spec, as a commit on the task's branch in a worktree, `git_workspace.py`; without one, with a `snapshot` that undoes it, `delivery_snapshot.py`), `workspace.execute` (run a command the command policy accepts, `command_policy.py`), `workspace.capabilities`, `workspace.status`, `workspace.browse` (folder picker) and `workspace.inspect` (canonical path, git remote/branch, file snapshot for stack detection), `workspace.hash` (the sha256 of named files as they are on disk, `file_hash.py`; the backend keeps QA's acceptance tests intact with it), `workspace.list` / `workspace.read` / `workspace.search` / `workspace.find` (the Developer's read-only tools on the project, bounded, `workspace_read.py`), `workspace.edit` / `workspace.edit.finish` (the Developer's checked edits, a working copy per task, `workspace_edit.py`), `workspace.git.publish` (push a `fabrique/` task branch to `origin` with the person's credentials and open a draft pull or merge request with `gh` / `glab`, else the compare page), `workspace.git.merge` (merge a `fabrique/` branch into a base branch of the local repository, no remote, `local_merge.py`), `workspace.git.reviews` (the comments people left on that request, read with `gh api` / `glab api`, `platform_reviews.py`), `workspace.app.check` (start the application in the sandbox, wait for its port, probe it, run an e2e command, stop it, `app_run.py`), `workspace.git.integrate` (a sprint's task branches merged into `fabrique/sprint-<id>` in a worktree, `sprint_integration.py`), `workspace.undo` (undo a delivery made without a `git` spec from the snapshot `workspace.generate` kept, `delivery_snapshot.py`), and consumes the JetStream queue `workspace.deliver` for files queued while it was offline, reporting on `workspace.delivered`. Its `requirements.txt` is `nats-py` only (`nkeys` is added for a creds file or an nkey seed). `nats_connect.py` connects it with the credentials and TLS the environment names, replies under `_INBOX.daemon` (passed explicitly to the pull consumer: nats-py 2.3.1, the image's pin, ignores the connection's prefix there), and waits for the backend to create the delivery streams instead of creating them.

**`backend/app/routers/workspace.py`** / **`services/workspace_daemon.py`** — `GET /v1/workspace/{status,browse,inspect}` relay the picker and project check to the daemon (503 with start instructions when none is connected). `status` reports `state`: `connected`, `absent` (NATS says nothing listens) or `unknown` (NATS unreachable, no answer in time, or an error reply), with a `reason`; unknown is never shown as stopped. `POST /v1/discover_team` inspects the folder through the daemon and runs stack detection on its file snapshot (`materialized_snapshot`), recording the host path.

**`backend/app/services/delivery_undo.py`** / **`vscode-daemon/delivery_snapshot.py`** — Undoing a task's delivery for a company without the Git workflow, taken from opencode's snapshots (https://opencode.ai/v2/docs/snapshots/): a separate Git object store in the tool's data directory, never the project's `.git`, so it works for a folder that is not a repository, and undo restores only the paths the step changed. Before writing, the daemon keeps for each target whether it existed and its bytes (a blob) with their sha256, and after writing the sha256 read back; the manifest and blobs form a tree under `refs/fabrique/snapshots/<id>` in a bare store per project under `FABRIQUE_SNAPSHOT_STATE`, git run with no shell, no hooks and `GIT_DIR` on the store. Only the files the delivery changed are kept (created, or other bytes than before; a file written with the same bytes is left out), once per file however its path was spelled (`app.py` and `./app.py`), with the hash of the last write that succeeded. A delivery, an edit of the Developer's tool and an undo of one project hold its lock (`vscode-daemon/project_lock.py`, keyed by the canonical path) from their first read to their last write. The bytes of a file that may hold secrets are never copied (that delivery goes without a snapshot), and a snapshot that cannot be taken never blocks the delivery (`snapshot_error`). The reply's `snapshot` is an observation kept on the evidence (`git_workflow.record_reply`) and returned by `GET /v1/agent_runs/{id}/changes` with `undone_at` from the audit log. `POST /v1/agent_runs/{id}/undo` (Manager, `routers/reviews.py`) asks `workspace.undo`: all or nothing, every file must still lead where it was written and hold the bytes the delivery wrote (a file changed or deleted since is a 409 naming it and nothing is touched); everything is staged first (the bytes to put back, the delivery's bytes on disk, whether each folder can be written, a folder that cannot being a 409), then each file gets its bytes back or is removed when the delivery created it (with the folders it created, once empty), a write that fails midway puts the files already undone back as the delivery left them so the snapshot can still undo it, the ref is deleted so a second undo is a 409, and the answer is audited as `run_delivery_undone`. A delivery on a Git branch is refused (discard the branch). Undo touches files only: the task's status stays, and a Manager sends it back with a review if it should be done again. The Files changed tab offers it to a Manager after a confirmation (`components/UndoDelivery.tsx`).

**`backend/app/services/review_changes.py`** — What a run changed, the way a code review shows it: `GET /v1/agent_runs/{id}/changes` reads the daemon's per-file diffs from the latest verification whose evidence carries them, or a pull request's diff (the artifact of a GitHub-ingested run), or, with neither, a diff the producer sent as its artifact (labelled `artifact`), with one `git diff` parser; the files the producer only declared come back apart (`declared_not_observed`), never as changes. The run page's **Files changed** tab (`components/ReviewChanges.tsx`, `lib/patch.ts`) renders it like a pull request.

**`backend/app/services/run_review.py`** / **`routers/reviews.py`** — Reviews as on a pull request: `ReviewComment` (a line of a changed file, new or old side, or the run) and `RunReview` (`comment`, `approve`, `request_changes`, with its `outcome`). A Manager writes them, a Viewer reads them. A submitted review carries the open comments; on a task-worker run it acts only where the loop waits for a person (`request_changes` sends a task `waits_for_a_person` back to `todo` with a send-back note and one `NEEDS WORK:` comment listing `path:line: comment`, which `board.pending_rework_feedback` hands to the Developer; `approve` accepts an `awaits_risk_acceptance` hold), and anything else is a 409 with the reason; on another producer's run it records an `AgentRunApproval` (`record_approval`, shared with `POST /approve`). Audited as `run_review_<event>`; never evidence. The Files changed tab's line threads and form are `components/LineComments.tsx`, the review form and history `components/ReviewPanel.tsx`.
**`backend/app/services/spec_check.py`** / **`spec_author.py`** / **`spec_planning.py`** — The specification (`models/specification.py`, `routers/specifications.py`): what the company's agents build from, as data. `spec_check.normalize` assigns the ids (`REQ-1`, `REQ-1.2`, `NFR-1`) in order, never a model or a request; `spec_check.check` is the deterministic gate: blocking (a requirement without a criterion, a criterion with an empty given/when/then, a quality target without a number, an unanswered question) and warnings (a promise without a number: "fast", "easy", "rápido"…). `spec_author.draft` asks the PO for a draft in a fixed JSON shape from a person's brief, with the questions it could not decide; a person answers (`POST /v1/specifications/{id}/questions/{i}`), asks for a revision (the answers go into the prompt) or edits, and only a Manager approves (`/approve`, 409 while the check blocks); an approved version is never edited, `/revise` opens a new draft that supersedes it. `spec_planning.plan` has the PO split an approved specification into tasks that name the criteria they cover (`POST /v1/plan_sprint` with `specification_id`; `uncovered` lists what no task covers; without a usable answer, one task per requirement), `POST /v1/start_sprint` keeps `specification_id` on the sprint and `covers` on each task (422 for an unknown id), the task prompt and QA's review prompt carry only the requirements the task covers (`requirements_context`), and `coverage` reads the boards: per criterion, done, blocked, planned or uncovered.

**`backend/app/services/arch_check.py`** / **`arch_author.py`** — The architecture (`models/architecture.py`, `routers/architectures.py`) of an approved specification, as data: modules (responsibility, place in the repository, `depends_on`, the requirements they `cover`), interfaces (kind, the module that provides it, a contract, consumers), decisions (ADRs) and conventions, plus the Architect's open questions. `arch_check.normalize` assigns the ids (`MOD-1`, `IF-1`, `ADR-1`) and maps a draft's names onto them; `arch_check.check` blocks approval on a module without a responsibility, a reference to a module or requirement that does not exist, a dependency cycle (`find_cycle`), a decision without its decision or an unanswered question, and warns on a requirement no module covers, a module without a path, an interface without a contract, no decision. `arch_author.draft` asks the Architect, from the specification, the detected stack and the project's root files, in a fixed JSON shape, or revises from the answers; only a Manager approves (`/approve`, 409 while the check blocks), `/revise` opens the next version. Once approved, the PO's planning prompt carries it (`planning_context`), each task's prompt and QA's review carry the modules its requirements map to with their interfaces, and the decisions and conventions every task follows (`context_for`, appended by `spec_planning.requirements_for`), and `POST /v1/architectures/{id}/write` sends it as `docs/ARCHITECTURE.md` through the daemon (`workspace.generate`), keeping the sha256 the daemon read back (`written_sha256`) as an observation; `GET .../document` is the same Markdown.

**`backend/app/services/dependencies.py`** — Dependencies between a sprint's tasks: `BacklogItem.depends_on` holds the ids of the tasks that must be done first. The PO's plan names them by title (`spec_planning._validate_tasks` maps them to positions and breaks a cycle at the edge that closes it), `POST /v1/start_sprint` takes each task's `depends_on` as positions in `backlog_items` (`check_positions`: an unknown position, self or a cycle is a 422), keeps the ids and queues only the tasks that can start, with a `Waits for task …` note on the rest. The worker follows the order instead of the queue's: `sprint_control.dispatch_todo` and `_dispatch_todo_items` queue a `todo` task only when `is_ready` (every dependency done), `_on_task_assign` asks the same before touching a task that arrived early, and each review cycle `settle_active` blocks a `todo` task waiting on one a person must act on (`Blocked: waits for task …, which waits for a person.`, an explicit block reason, so the sprint closes around it and the inbox shows what the decision unblocks) and releases a task so blocked once its dependencies are done, or back in work (`Released: …`). The hold is the System's last note on the task (a comment anyone appends does not hide it); a chain of holds follows its root, so a task sent back to `todo` behind a held one is held again while the root is stuck; `sprint_settled` reconciles the holds before a sprint counts as settled, so it never closes over a task that could run. With the Git workflow, `git_workflow.delivery_spec` names the branches of the done tasks a task depends on (`merge`) and the daemon merges them into the task's worktree before writing (`git_workspace.merge_branches`, `merged` in its report; a conflict is an error). A carried task keeps a dependency carried with it and drops the rest. The board lists `depends_on` and `waiting_for` per task; `history_check.py` refuses a task that delivered before every task it depends on had closed, comparing its first deliverable with the dependency's last approval or person's note (`worked_before_dependency`).

**`backend/app/services/git_workflow.py`** — The company's Git workflow (`Company.git_workflow`, `git_base_branch`; `GET|PUT /v1/companies/{id}/git`, Viewer/Manager): the task worker asks the daemon to deliver on `fabrique/task-<id>` (`delivery_spec`), the daemon commits in a worktree under `<git dir>/fabrique-worktrees/` and answers with the branch, commit and parent (`vscode-daemon/git_workspace.py`, real git, no shell), and `record_reply` keeps that on the evidence as `git` and points the delivery tests at the worktree. The person's checkout is never touched by a delivery. Pushing is never a step the loop requires: a company may keep its code only in a local repository. `services/local_merge.py` merges a run's branch into a base branch of that repository (`POST /v1/agent_runs/{id}/merge`, Manager; `POST /v1/sprints/{id}/integration/merge` for a sprint's integration, refused at a conflict or a failing suite): the daemon (`vscode-daemon/local_merge.py`, `workspace.git.merge`) computes the merge in a worktree of its own detached at the base's tip, aborts a conflict naming its files with nothing changed, and moves the base with `git update-ref` against the old tip, or with `git merge --ff-only` in the checkout that has it, only while that checkout has no uncommitted changes to tracked files; nothing is pushed and no remote is needed. The answer (base, tip before and after, the branch's commit) is an observation kept as a `RunPublication` with `how == "local"` (a run) or under the integration's `details["local_merge"]`, audited. Publishing is the optional other way out: `services/git_publish.py` publishes a run's branch (`POST /v1/agent_runs/{id}/publish`, Manager; `GET .../publications`, Viewer): the request's description is the evidence the Verifier observed, the producer's claims labelled apart, with a `Fabrique-Run:` trailer; the daemon's answer is kept as a `RunPublication` and audited. The Files changed tab offers it (`components/PublishBranch.tsx`). `services/review_sync.py` brings the request's comments back (`POST /v1/agent_runs/{id}/publications/{pid}/sync`, Manager): each becomes a `ReviewComment` with its `source`, `external_id`, page and, for a review, its state there; reading again updates one still waiting and never duplicates; the next review a Manager submits carries them, and its `NEEDS WORK:` names who wrote each and on which platform. A review submitted on the platform decides nothing here.

**`backend/app/services/integration.py`** / **`integration_publish.py`** / **`routers/integration.py`** — A sprint's integration (issue #314). `plan` orders the sprint's task branches by dependency. When a sprint closes with the Git workflow on, the worker starts `schedule_after_close` in the background (a Manager also can, `POST /v1/sprints/{id}/integrate`): the daemon starts `fabrique/sprint-<id>` from the company's base in a worktree and merges the done tasks' branches in that order with real git (`vscode-daemon/sprint_integration.py`, `workspace.git.integrate`, sharing `git_workspace.merge_one`; each task branch's tip is read once and that commit is checked, merged and recorded), stopping at the first merge git cannot make, which it aborts and reports with the conflicted files and hunks; a branch already in is skipped, so running it again only moves forward. With `DELIVERY_TESTS_ENABLED` the project's test command then runs on the integrated worktree through `workspace_daemon.run_delivery_tests` (the company's sandbox, the failing tests named). One integration of a sprint runs at a time (`integration.sprint_lock`: an asyncio lock in the process and, across replicas, `locks.held(…, locks.SPRINT_INTEGRATION, sprint_id)`, taken and released in a thread on a small lock pool of its own (`INTEGRATION_MAX_CONCURRENT` connections, default 2, also the number of integrations a process runs at once); a Manager's request while another replica integrates is a 409), and the suite runs in a worktree the daemon detaches at the integrated commit for that attempt only (`test_worktree`, named after the backend's `attempt` token; beyond the newest three per sprint older ones are removed, never one a `workspace.execute` is running in, `sprint_integration.while_active`, nor one younger than ten minutes), so a later integration never changes what an earlier one tested (`tested_commit`). The answer is kept as a `SprintIntegration` (migrations `0011`, `0012`), audited, with each done task's state, the commit its branch pointed at and the task worker's run and verification as they were at integration (`tasks`): `status` is `tests_passed`, `tests_failed`, `untested`, `conflict`, `incomplete` (a done task's branch missing or refused), `running` (`services/integration_jobs.py`: when the sprint closes the job is kept before the daemon is asked and the attempt fills that row in place, so a restart mid-attempt leaves it to be retried), `pending` (no daemon answered at close; `retry_loop`, a lifecycle worker, tries again every `INTEGRATION_RETRY_SECONDS`, 60, each sprint whose latest row is `pending` or a `running` one nobody holds, reading it again once it holds the lock) or `error` (the daemon refused; a Manager integrates again); `conflict`, `tests_failed`, `incomplete`, `pending` and `error` wait for a person (`GET /v1/inbox`, `integrations`, listed up to the limit and counted in full as `integrations_total`, until a later integration of the sprint supersedes it). `GET /v1/sprints/{id}/integration` (Viewer) is the plan beside the latest integration and the earlier ones; `POST /v1/sprints/{id}/integration/publish` (Manager) publishes the branch through `workspace.git.publish` as one request for the sprint, pinned to the integrated commit and refused unless the status is `tests_passed`, `tests_failed` or `untested` and the row recorded its tasks (a row from before migration `0012` asks to integrate again), its description every integrated task (merged now or already in) with the verdict, checks and files of the run and verification recorded at integration and the acceptance line as it stood then (`spec_planning.acceptance_summaries`, the coverage read once per specification) (files only as the daemon read them back; a diff the producer sent is listed as a claim), the producers' summaries apart as claims, with `Fabrique-Sprint:` and `Fabrique-Integration:` trailers. The per-task publication stays.

**`backend/app/services/standards.py`** — The quality standards every project is held to: a fixed catalogue by discipline (`STD-ARCH-1` … `STD-PO-1`, `docs/STANDARDS.md`) that no model, request or procedure edits. `prompt_section(role)` goes into every prompt by role (the task prompt before the company's procedures, QA's review, the PO's draft and plan, the Architect's design); a procedure adds to the standards, never relaxes them. `check_delivery` is the Verifier's `standards` check over the files a deliverable declares: a secret-looking value (keys, tokens, private keys, a password assigned in code; placeholders and `os.environ` references are not) refutes the delivery and the gate sends it back (`standards violated`); code without a test, a `TODO`/`FIXME` left behind and an unpinned dependency (`requirements*.txt`, `package.json`) are findings the reviewer sees. `missing_quality_areas` gives the specification check its `quality_area_missing` warnings. `GET /v1/standards` serves the catalogue; `tests/test_standards.py` keeps the document in step with it.

**`backend/app/services/board.py`** — The pure readers of a task's comment thread, shared by the task worker, `routers/inbox.py` and `services/carry_over.py`: which comment is the deliverable to review, the pending `NEEDS WORK:` requests, whether a blocked task waits for a person (`blocked_item_next_step`, `item_is_settled`) and its `block_reason`. No engine, no I/O; the gate author names (`Quality gate`, `Verifier`) live here too. The advice a task got (the worker's console-advice note, the Architect's `ARCHITECT_CONSULTED:` brief) is never a verdict, and the phrases that keep a task blocked count only from the system, a gate or a person (`BLOCK_VERDICT_AUTHORS`), never from a deliverable.

**`backend/app/services/task_events.py`** / **`task_projection.py`** / **`routers/events.py`** — A task's life as events (issue #324, after OpenHands' event stream: typed events with a source and a cause). `task_events.record(session, item, kind, actor, text=, author=, status=, run_id=, verification_id=, payload=)` is the one function a transition goes through: it appends a `TaskEvent` (`models/event.py`, company-scoped: `event_type` the kind — `claimed`, `advised`, `delivered`, `verified`, `reviewed` with `payload.verdict` `approved`/`needs_work`, `sent_back`, `blocked`, `released`, `escalated`, `decided`, `approved_by_person`, `carried_over`, `risk_hold`, `review_failed`, `retried`, `recovered`, `paused`, `moved`, `note`, `integration`, `legacy`; `actor` `system`/`gate`/a role/`person`, which must match the comment's author; the task's status after it in `task_status`), seals it in the task's own hash chain (`seq`, `prev_hash`, `chain_hash`, keyed with `AUDIT_SIGNING_KEY` like the audit chain but a separate chain, `verify_chain`) and renders it as the board comment with the event's `event_id`, setting the status; the thread is read again under the chain's lock before the comment is appended, so a writer holding a stale task never drops a comment committed meanwhile. A person's text is kept whole; text the platform writes is capped at `TEXT_LIMIT` (4000). With a key, `verify_chain` reports an unsigned event after a signed one as `downgrade`, as the audit chain does. The caller commits; `record_by_id` opens its own session. The task worker (`_update_item_status`, `_append_item_comment` and every review-loop transition), `dependencies.note` (hold, release, waiting note), `carry_over` (both tasks), `sprint_control` (stop, credit pause), `run_review` (a person's accept, send-back and feedback), `scrum_master.record_decision` (`decided`, no comment), the escalation before a triage (`escalated`) and `PATCH /v1/backlog` (`person_kind`) write through it. The flags the board's questions need (`verdict`, `explicit_block`, `needs_person`, `retryable`, `retry`, `frozen`) go into the payload when the event is written (`flags_for`, the same rules `board.py` applies). `task_projection` answers the board's questions from the events (`pending_rework_feedback`, `block_reason`, `awaits_risk_acceptance`, `blocked_item_next_step`, `waits_for_a_person`, `status_from_events`) when they render the whole thread (`covers`: every comment names one of the task's events and none is a `legacy` marker), and falls back to `board.py` otherwise; the inbox and the review form read through it (the inbox loads every candidate's events in one query, `task_events.for_tasks`, and passes them as `histories`). Migration `0014` backfills events from existing threads (marked `legacy`, unchained, each comment's whole text; a thread with a comment it cannot tell also gets a `legacy` event, so it stays read from its comments). `GET /v1/tasks/{id}/events` (Viewer) lists a task's events with its chain's state and which record the board reads; `GET /v1/sprints/{id}/events` a sprint's.

**`backend/app/services/sprint_control.py`** / **`routers/sprint_control.py`** — Who decides when a sprint's agents work. A sprint runs only while `active` (or `needs_split`); `planned` (Sprint 0 left unstarted by the New company wizard, `start_now: false` on `POST /v1/init_company`, always at autonomy 1), `paused` and `cancelled` are halted: the worker neither dispatches, reviews nor retries their tasks (`is_halted`). `POST /v1/sprints/{id}/start` starts a planned sprint, `/pause` and `/resume` pause it (resume dispatches its waiting tasks), `/stop` cancels it for good (unfinished tasks blocked with a note, no follow-up), `/dispatch` re-queues `todo` tasks of a running sprint; all Manager. When a provider refuses a call for lack of credit (402, or a 429 that names a quota or balance; `credit_failure`), the worker pauses the sprint with `pause_reason` and puts the task back to `todo` instead of blocking it, so resuming runs it again and a sprint of failures cannot loop into follow-ups; a company with a planned or paused sprint gets no follow-up on its own. `GET`/`PUT /v1/companies/{id}/autonomy` reads (Viewer) or changes (Manager) the autonomy level: at 1 every task QA approves waits for a person like a high-risk delivery (`HUMAN_LED_REASON` in the hold note), at 2 only high-risk ones, at 3 follow-ups and procedures also run on the company's own. The Sprint page shows a banner on every tab for a planned or paused sprint (Start / Resume, the reason) and the Board tab's controls (`components/SprintControls.tsx`): Dispatch, Pause, Stop, Autonomy.
**`backend/app/services/manager_agent.py`** / **`manager_inbox.py`** / **`manager_policy.py`** / **`manager_actions.py`** / **`manager_record.py`** / **`manager_brief.py`** / **`routers/manager.py`** — The Manager agent (issue #331, `MANAGER_AGENT_ENABLED`): the owner acts as a client and decides only the big things. `manager_inbox.inbox_items` gathers what waits for a person with the facts the platform observed (`run_facts`: the latest verification's verdict, the `risk_tier` check, the distinct files declared, imported (`subject.changed_files`) or written, unknown when none says, which no limit lets through; whether an operator froze the task; the criteria a task covers; whether a hold is only autonomy level 1's; the autonomy level; a procedure reverted for the role; `over_budget` from the business units' quotas) and gives each a category the platform decides (`accept_delivery`, `accept_high_risk`, `blocked_task`, `adopt_procedure`, `approve_run`); `parse_suggestion` takes only `DECISION … CATEGORY … REASON … END` with a decision the category allows and the item's own category. The prompt carries the standards (`standards.prompt_section`) and the company's active procedures for the role `Manager`; each call is billed to the company's business unit (`bu_id`) and its tokens audited as `manager_agent_call`; a cycle makes at most `ITEMS_PER_CYCLE` calls across companies, failures included. The situation key (`item_state`) holds the latest run and verification ids, so new evidence is looked at afresh. A suggestion row keeps its `action_state` (`pending`, `done`, `not_allowed`, `stale`, `unusable`): after the call `decide` reads the item, policy, record and overrides again and acts only while the situation is the one it asked about, the acceptance being one conditional update on the task as it was read (a task that moved without a new situation stays `pending` and is read again); a row a stopped replica left `pending` is decided on the next cycle without another call. Across replicas the loop runs under `locks.MANAGER_AGENT` and each item under its own `locks.MANAGER_ITEM` lock, held from the check that its situation is new through the model call to the decision, so two replicas never ask about or decide the same item; one `ManagerDecision` row per situation (`item_state`, unique) stays as the invariant behind the lock. `manager_policy` holds the rules a Manager may enable (`accept_delivery`: an autonomy-1 hold, verified, tier low or medium, within `max_files`/`max_criteria`; `adopt_procedure`: autonomy 3 and no reverted procedure for the role; `approve_run`: verified, low or medium, within `max_files`) and the confirmation set no policy unlocks (`ALWAYS_ESCALATE`: approve a specification or an architecture, accept a high-risk delivery, cancel a sprint, change autonomy, spend above budget; a policy naming one is a 422). `evaluate` allows a decision alone only when a rule holds on the facts *and* the suggestion is that rule's decision, never over budget, after an override on the item, or under a flagged rule. `manager_actions.act` takes it as a person would (the task marked done with an `Accepted by the Manager agent` note, `procedures.adopt`, `run_review.record_approval`), audited `manager_agent_<category>` with `decided_by` and the rule; `override` undoes it (the task back to waiting for a person, the procedure retired and the one it superseded restored, a rejection recorded) and is audited as the person's; it runs once (a lock on the decision and a conditional claim: a second request is a 409) and only while the agent's decision is still the latest on the item (a later acceptance, approval or retirement is a 409). `manager_record.track_record` reads the client's decision from what they did (a send-back or acceptance note after the agent looked, a carry-over, the procedure's status and who decided it, the run's latest approval by someone else) and counts suggested, decided, agreed, unusable, taken alone, overridden and the override rate by category; `flagged_rules` (two or more overrides among a rule's last five decisions since the policy was saved, at a higher rate than before) stops the agent applying a rule until a Manager saves the policy again, never widening it. `GET /v1/companies/{id}/brief` (`manager_brief`) is the client's brief, deterministic: tasks done since `since` (a day by default, dated by their closing comment, never by `updated_at`), suggestions, decisions and overrides each by its own moment, what the agent decided and why, what waits for the client (blocked tasks, holds, proposals, runs, integrations, specification and architecture drafts) and the tokens from the audit log beside the quotas. The inbox carries each item's latest `suggestion` and the agent's `agent_decisions`. `history_check.py` lets an autonomy-1 hold close on the agent's acceptance (and dates the task's closing by it for its dependants) and still refuses one on a high-risk hold. The client's request box on the Review page (`components/ClientRequestBox.tsx`, Managers) is the PO's `POST /v1/specifications/draft`, linking to the draft the client approves; the Sprints page shows the record beside the other numbers (`components/ManagerRecordCard.tsx`) once the agent has decisions.

**`backend/app/services/console_assistants.py`** — The terminal assistants the daemon can run (GitHub Copilot CLI, Claude CLI): which ones to ask for a task (`select_assistants`), the command with the prompt shell-quoted, `collect_advice` through `workspace.execute`, and the per-company cooldown persisted in `AssistantRuntimeState` when one is out of quota or missing on the host. Their advice is prompt context and a board note, never evidence; `GET /v1/sprints` counts tasks with and without it (`console_advice`: done and rework per group), so the Sprints page shows whether it helps. The Architect's briefs are counted the same way (`architect_advice`, rework from the first brief on), and `services/architect.py` tells the Architect, when it is asked again, how the company's briefed tasks went in its completed sprints against the rest. `services/glassroom.py` holds `publish_glassroom`, the best-effort publisher every worker uses for the sprint's SSE stream.

**`backend/app/services/mcp_tools.py`** / **`mcp_config.py`** / **`mcp_client.py`** / **`routers/mcp.py`** — MCP (Model Context Protocol) tools for the task worker's producers. A Manager configures a company's servers with the standard `mcpServers` map (`POST /v1/mcp/servers`: `command`/`args` for stdio, `url` for streamable HTTP or SSE, `env`/`headers` as secrets, optional `roles`); `McpServer` rows keep the secrets Fernet-encrypted and the API lists only their names (`GET /v1/mcp/servers`, Viewer; `PATCH`/`DELETE`, Manager; `GET /v1/mcp/servers/{id}/tools` connects now and lists what the server advertises). The worker loads the role's `Toolbox` (`mcp_tools.Toolbox.load`), offers the tools to the model as `<server>__<tool>` in the OpenAI `tools` shape (the proxy passes them to every provider; `llm-proxy/gemini.py` translates them for Gemini) and runs the bounded loop (`chat_with_tools`): at most `MCP_MAX_TOOL_ROUNDS` rounds, then one request without tools, so a task always ends in a deliverable. Each call is an observation the backend made (server, tool, arguments, error, duration, sha256 of the result) kept on the Verifier's evidence as `tool_calls` and shown as the `tools_used` check; the model's reading of a result is a claim. Results are masked for the server's own secrets and cut at `MCP_RESULT_MAX_CHARS`; a tool without a read-only annotation raises the delivery's risk tier to at least `medium` (`risk.raise_for_tool_calls`). A server that cannot be reached is named in the toolbox's `unavailable` and the task goes on without it. stdio servers run inside the backend container, so their runtime (`npx`, `uvx`, …) must be in that image; remote servers need only a URL. Built on the official `mcp` package.

**`backend/app/services/research_tools.py`** / **`research_registries.py`** / **`research_schemas.py`** — The research tools, offered in the same loop to the roles that decide (`RESEARCH_ROLES`; `offered_to`): `research__fetch` (one page as text, `html_to_text`, from a host on the list: the defaults `DEFAULT_HOSTS`, `RESEARCH_ALLOWED_HOSTS` and `Company.research_hosts`, `allowed_hosts`; https only unless the host was named explicitly; a redirect is followed only while it stays allowed; no credential; cut at `RESEARCH_FETCH_MAX_BYTES`), `research__package` (`research_registries.package_record`: PyPI, npm, crates.io, the Go proxy) and `research__advisories` (OSV, `advisory_rows`). `ResearchToolbox` has the `mcp_tools.Toolbox` shape; `toolbox_for` builds it for a task (the worker's `developer_tools.toolbox_for` combines it with the workspace and MCP toolboxes, `CombinedToolbox` taking any built-in boxes; `research` is a reserved server name) and `toolbox_for_request` for a draft (`spec_author.ask_with_tools` runs `chat_with_tools`; `prepare` puts the company's hosts and the `## Sources` prompt section, `prompt_section`, into the request). Every call is an observation (`url`, `host`, `allowed`, `fetched_at`, `status`, `sha256`, `bytes`, package and ecosystem); `sources_of` keeps those fields on `Specification.sources` and `Architecture.sources`, and `check_sources` is the Verifier's `sources` check (the sources, the refusals, and the dependencies the delivery's manifests name without a registry lookup, `packages_in`), present when tools were offered. `GET`/`PUT /v1/companies/{id}/research` (`routers/sprint_control.py`, Viewer / Manager) read and replace the company's hosts (`valid_host`). The frontend shows a draft's sources (`components/SourcesConsulted.tsx`). A web search is an MCP server the company configures.

**`backend/app/services/developer_tools.py`** — The Developer's workspace tools, offered beside the MCP tools in the same loop to the roles that deliver files (`TOOL_ROLES`: Developer, QA) when the company's project path is known: `workspace__list`, `workspace__read`, `workspace__search`, `workspace__find_file` (the daemon's `workspace.list` / `read` / `search` / `find`, `vscode-daemon/workspace_read.py`, bounded and never outside the project), `workspace__edit` (below) and `workspace__run_tests` (`workspace.execute` with the project's detected test command, or a narrower one that starts with the same runner; offered only with `DELIVERY_TESTS_ENABLED`). `toolbox_for` replaces `mcp_tools.Toolbox.load` in the worker and returns a `CombinedToolbox` that dispatches by the built-in tools' exact names (the server name `workspace` is reserved in `mcp_config`); `tool_root` points the tools at the task's worktree once it delivered there (Git workflow), else the project; `bind` gives the module the worker's NATS client and loop, since the LLM call runs in a thread (`_request_from_thread`). `repository_section` puts the top two levels of the tree into the task prompt (`## Repository`). Each call is an observation like an MCP call (`read_only: true`); a read also carries the file's `path`, `sha256` and `bytes` as the daemon read them, and `services/context_read.py` holds the evidence side: `check_context_read` is the Verifier's `context_read` check (which of the files the delivery modified, the daemon's `change == "modified"`, were read first and which were not; `workspace_tools_used` says the tools were in use at all, a listing or a failed read included), `redact_secrets` keeps a secret-looking line (`standards.secrets_in`) out of what the model is shown; `risk.raise_for_unread_changes` raises the tier to `medium` for a modified file never read while the tools were in use. The tool schemas live in `services/developer_tool_schemas.py`. The daemon never opens a file that may hold secrets (`workspace_read.SENSITIVE_NAMES`, `SENSITIVE_DIRS`) or a symlink that leads out of the project, hashes a file in chunks and indexes only its first `READ_SCAN_BYTES`, and runs a search in a child process killed at `SEARCH_TIMEOUT_SECONDS`. Settings: `DEVELOPER_TOOLS_ENABLED`, `DEVELOPER_TOOL_ROUNDS` (forwarded by `docker-compose.yml`).
From SWE-agent's agent-computer interface (MIT, issue #359; `services/developer_tool_replies.py` writes what the model sees): a read is a window of `READ_MAX_LINES` (100) numbered lines headed `[path: lines X–Y of N]`, with the `start_line` to page on or go back, cut at whole lines to the toolbox's result budget before the range is stated; a search over `SEARCH_MAX_MATCHES` answers how many matches there are and the count per file (`match_count`, `per_file`) and asks to refine instead of cutting silently, and `workspace__find_file` (a name or glob, `FIND_MAX_RESULTS`) does the same; an empty answer says so (`(the command finished with no output)`, `No line matches …`, an empty file or folder). **`workspace__edit`** (`services/workspace_edits.py`, daemon `vscode-daemon/workspace_edit.py`) replaces a line range or one SEARCH/REPLACE block of an existing file, and the daemon applies it only if the file still parses (`compile`, `json`, `node --check` on a copy outside the project when `node` is on PATH, and a check that cannot complete refuses; other types unchecked, and the reply says so), else refuses with the error and the file untouched; never outside the project, into VCS metadata (`_resolve_target_path`) or a file that may hold secrets, at most `EDIT_MAX_BYTES` per file and `EDIT_MAX_FILES` per task, only the file types a delivery writes. It is offered only where the task's files may be touched (`workspace_edits.allowed`): the task's own worktree, or a project without the Git workflow, never the person's checkout of a company that delivers on branches. Each applied edit is an observation (`path`, `sha256_before`, `sha256_after`, `checked`; `read_only: false`, which `risk.raise_for_tool_calls` leaves to the delivery's paths). The edits are a working copy: the first edit of a file keeps its original under `FABRIQUE_EDIT_STATE` (daemon side, default `~/.fabrique-daemon/edits`), later reads and test runs see the edit, and when the tool loop ends `WorkspaceToolbox.close` asks `workspace.edit.finish`, which answers with each edited file's final text and puts the original back; it reads and restores a file only while its path still leads to it (`_saved_target`: inside the project, no VCS metadata, no symlink along the way, not a secret) and it holds the bytes the session last wrote (`sha256_written`, also checked before a later edit), and otherwise leaves the file as it is, keeps its original in the journal and reports it (`error`, `conflict`), never delivered. The worker delivers the texts beside the answer's files (`with_tool_edits`, `source: workspace_edit`; a file the answer delivers itself keeps its block), so the daemon's diff, the commit and the Verifier see them as any delivery's; `observe_tools` keeps them on the evidence as `tool_edit_files` (and the reports as `tool_edit_conflicts`), apart from the claims, where the `standards` check scans them with the answer's files (on re-verification too), `acceptance.test_sources_of` restores QA's tests from them and the minimum-deliverable gate counts them; `claimed_files_present` does not look for them in the answer's text (`context_read.files_edited`). An edit never delivered never stays on disk.

**`backend/app/services/webhooks.py`** / **`routers/webhooks.py`** — The company's notification webhook (issue #357): one per company (`CompanyWebhook`: an https URL, a Fernet-encrypted secret never returned, the events among `task_done`, `task_blocked`, `sprint_closed`, `run_refuted`, on or off; `GET`/`PUT`/`DELETE /v1/companies/{id}/webhook`, Viewer / Manager, `POST .../webhook/ping`). A `before_flush` listener over every session (`install`, called by `main.py`) sees a task's status change to done or blocked (with `block_reason` and `waits_for_a_person`), a sprint's to completed and a new refuted `Verification`, and adds an `OutboxEvent` (`webhook_post`) in the same transaction, so a change that rolls back sends nothing and no caller has to know about webhooks. The outbox processor posts it (`deliver`) with `X-Fabrique-Event`, `X-Fabrique-Delivery` and `X-Fabrique-Signature-256: sha256=<HMAC of the body>` (GitHub's shape); a non-2xx answer or an error is recorded on the webhook (`last_status`, `last_error`) and retried with backoff (the outbox waits `retry_count² × 10 s` between attempts, five at most). A webhook that is down never holds up a sprint. The URL's host must resolve to public addresses unless `WEBHOOK_ALLOW_PRIVATE_HOSTS`.

**`backend/app/nats_client.py`** — `enable_jetstream()` declares the work-queue streams in `DURABLE_STREAMS` (`task.assign`, `workspace.deliver`, `workspace.delivered`); `publish()` stores durable subjects with a `Nats-Msg-Id`, `consume()` is a durable pull consumer with explicit acks. Without JetStream both fall back to core NATS with a warning. `connect()` takes its credentials and TLS from `nats_auth.connect_options` (`app/nats_auth.py`) and keeps `nats_auth.describe` (method and TLS, no secret); `/health` reports `nats` as `{state, connected, jetstream, authenticated, tls}` (and `jetstream` at the top level, as before).

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — sprint task tracking with drag-and-drop (`@dnd-kit`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat

**`backend/janitor/main.py`** — Background maintenance container (runs daily, `JANITOR_INTERVAL_SECONDS`). Applies the retention settings (`janitor/retention.py`, plain SQL over psycopg2, tested on `sqlite3`) in transactions (each batch of superseded run versions locked `FOR UPDATE`, its guards checked again, and deleted with its verifications or rolled back whole), and rebuilds pgvector indices with `REINDEX CONCURRENTLY`, the one step in autocommit. `scripts/backup.sh` backs Postgres up under `umask 077` (`pg_dump -Fc`, checked, with a checksum; Neo4j through APOC only when enabled, decoded into plain Cypher; the outputs are in `.gitignore`) and restores it (`restore FILE --yes`: into a new database in one transaction, then swapped in by rename, the previous one kept as `<db>_pre_restore_<time>`); CI backs up and restores the e2e database.

**`backend/app/services/locks.py`** / **`replicas.py`** — Several backend replicas may run the workers against one database (issue #328, `docs/OPERATIONS.md`). `locks.held(engine, name, ident)` is a Postgres session-level advisory lock taken with `pg_try_advisory_lock` on a connection held for the work (never waits; a replica that does not get it skips; a dead replica's lock goes with its connection), a process-local table on SQLite. `replicas.claim_todo` claims a task with a conditional `UPDATE … WHERE status = 'todo'` (only the replica whose update matched runs it; the claim is the task's `claimed` event, written in the same transaction), the worker holds `task_lock` while it works on a task (the lock is its lease) and `recover_inflight` returns to `todo` only the `in_progress` tasks nobody holds (a `recovered` event, or `blocked` for a frozen task, through `task_events.record`); `recover_stale` runs it at the start of every review cycle, so a surviving replica dispatches a crashed one's task again within a cycle; `close_once` closes a sprint under its company's lock and `mark_completed` with a conditional update from `active` (one close, retrospective, procedure measurement, proposal round and follow-up), and `triage_slot` triages an escalation once (the `escalated` event is recorded inside it, once). The review cycle (`task_worker._review_cycle`) and the Support and Reviewer agents' cycles run under their own locks.

### Data Flow for a Sprint

Tasks are executed by the **task worker** (`backend/app/services/task_worker.py`);
the LangGraph graph is only an optional kickoff conversation.

1. `POST /v1/start_sprint` writes the sprint (or `POST /v1/init_company` Sprint 0, which with `start_now: false` stays `planned` until `POST /v1/sprints/{id}/start`), one `BacklogItem` per task and one
   outbox event per task on `task.assign`; the outbox processor stores them in the
   JetStream stream `FABRIQUE_TASKS` (an outbox row completes only once stored).
2. The task worker consumes `task.assign` as the durable consumer `task-worker`,
   so assignments published while it was down are processed when it starts. It
   loads the item and the assigned agent
   (empty model → role default from `llm/model_factory.py`) and calls the LLM proxy.
   The prompt asks for every file as a fenced block with `path=<relative path>`;
   on rework it also carries the pending `NEEDS WORK:` feedback. It also carries
   the company's past decisions from memory (scoped by `company_id`), and
   **Lessons from Recent Sprints**: the retrospective counts and the review
   requests from the company's last two completed sprints, read from the board
   (`_load_recent_lessons`), and the **Company Procedures** in force for the
   agent's role (QA's go into its review prompt).
3. `parse_file_manifest` turns the answer into files (a block marked `edit`
   carries SEARCH/REPLACE edits instead, `services/edit_blocks.py`; the daemon
   applies them exactly and writes a `.py` or `.json` file only if it still
   parses, `vscode-daemon/file_edits.py`). The worker sends them to the
   VS Code daemon with a NATS **request** on `workspace.generate`, targeting the
   company's project (`ProjectDiscovery.project_path`, recorded at company creation;
   `AGENT_WORKSPACE_ROOT` is the fallback). The daemon replies per file with the
   sha256 of the bytes it read back from disk. If no daemon answers, the files are
   queued on `workspace.deliver` (JetStream), the run is recorded as waiting for
   that job and the review loop holds the task; when a daemon connects it writes
   them and reports on `workspace.delivered`, and the run is re-verified against
   the hashes the backend recorded (`_on_delivery_result`).
4. Optionally (`DELIVERY_TESTS_ENABLED`) the daemon runs the project's tests via
   `workspace.execute` and returns the exit status. In a sprint planned from a
   specification, the plan puts a **test-writing task per requirement**
   (`BacklogItem.kind == "acceptance_tests"`, assigned to QA) before the
   implementation tasks, which depend on it: one test per acceptance criterion,
   named after its id (`REQ-1.2` → `test_req_1_2_…`), importing the code under
   test inside the test so a missing module fails that test and not the file.
   `services/acceptance.py` reads the runner's per-test lines back into
   criterion ids (`pass`, `fail`, `missing`; an observation, from the daemon's
   output) and the Verifier records them as `criteria_tests`: for the test task,
   every criterion must have run (failing is expected); for an implementation
   task, its own criteria must pass. `tests_run` excuses only the failing
   acceptance tests of criteria whose implementation is not done yet, never
   another failure; an implementation task that writes an acceptance test file
   is refuted (`acceptance_tests_untouched`) and sent back. Before its tests
   run, `acceptance.guard_test_files` asks the daemon for the sha256 of the
   acceptance test files on disk (`workspace.hash`) against what QA's delivery
   wrote; a file that differs or is missing is restored from QA's deliverable
   (`AgentRun.artifact_text`, parsed again) and the delivery refuted, so no
   criterion is judged against a weakened test. `services/criteria_tests.py`
   holds the reading of the runner's output and the two checks. A criterion
   is `verified` only from the most recent verification across the tasks
   covering it, and only when that run's whole verdict is verified.
   The PO's and the Architect's calls to the proxy (`spec_author`,
   `arch_author`, `spec_planning`) are `prepare`d with the request's session
   and run in a thread as plain values (`draft_from`, `plan_from`): a session
   never crosses a thread. The Developer's
   prompt names the test files and test names to make pass
   (`acceptance.section`), the specification's coverage says per criterion
   `verified` (its test passed in an observed run of an implementation task)
   with `last_result`, the publication's description carries how many criteria
   are verified, and `history_check.py` refuses a criterion marked passed with
   no observed test run.
5. The deliverable is ingested as an `AgentRun` (`producer: le-fabrique-task-worker`,
   `external_ref: task-<id>`) and verified deterministically (`files_written`,
   `tests_run`, …). Claims (declared files, the agent's summary) and observations
   (daemon reports) are kept apart; the board and Glassroom show the verdict.
6. The review loop sends a refuted/unconfirmed delivery or a failed test run back
   for rework without asking an LLM; otherwise QA reviews, and only an explicit
   `APPROVED:` closes the task. The Verifier also records a `risk_tier` check
   (`services/risk.py`: high for authentication, secrets, payments or the CI
   pipeline, medium for shell scripts, migrations, dependency manifests or
   container files, from the paths the daemon wrote and the producer declared;
   a producer may raise it with a reason, never lower it). An approved task
   whose tier is high waits for a person at every autonomy level, level 3
   included: the inbox lists it with `risk_hold`, a Manager accepts it (marks
   it done) or sends it back, and it counts neither as blocked in the
   procedures' numbers nor as a task to carry over on the company's own. Before any of that, the text checks on the
   deliverable (length, sections, balanced fences — `_validate_minimum_deliverable`)
   send it back with the reason as `Quality gate`, up to
   `AUTOPILOT_MAX_REWORK_CYCLES`, then block the task and escalate to the
   ScrumMaster. A sprint closes when every task is settled: done, or blocked
   until a person acts (nothing the worker retries, re-reviews or an operator
   froze). Blocked tasks stay on the board, appear in the retrospective and
   count in the procedures' `blocked_rate`; approvals close the sprint on the
   spot and the review cycle closes one whose last open task got blocked.
   `GET /v1/inbox` (Viewer, `routers/inbox.py`) lists what the company needs a
   person for: the tasks blocked until someone acts (not the ones the worker
   still retries or re-reviews), the procedure proposals waiting and how many
   runs of other producers nobody decided on (the company's own task worker
   and kickoff runs, `IN_HOUSE_PRODUCERS`, are decided by the review loop;
   `GET /v1/agent_runs?exclude_in_house=true` lists the rest; with
   `REVIEWER_AGENT_ENABLED` the Reviewer agent suggests a decision on each,
   kept on the run apart from the approval). The Review page shows them (a Manager can send a
   blocked task back to work, with how earlier second attempts went beside them) and the navigation counts them. A blocked task
   can also get another attempt in the next sprint (`services/carry_over.py`):
   `POST /v1/start_sprint` takes `carry_over_ids` (counted in the scope), the
   "Plan a sprint" form offers them (with how earlier second attempts went,
   from the sprint list's `second_attempts`), and with `autonomy_level` 3 the follow-up
   sprint carries the closed sprint's blocked tasks on its own, once each. The
   new task carries what blocked the old one (a `Previous attempt` prompt
   section); the old one is marked and leaves the inbox. The escalation gets
   an answer (`services/scrum_master.py`): the ScrumMaster reads what blocked
   the task, the rework asked for and how the company's second attempts went
   (`track_record`: done out of carried, with and without its brief, counted
   from the boards of completed sprints, the same numbers the Sprints page
   shows) and decides, in a fixed `DECISION … END`
   format a parser checks, between another attempt with a brief for it or
   leaving it to a person. The decision is a `decision` message that names
   the escalation it answers (written by the worker, not the model), a claim: the
   inbox shows it as `triage`, the brief travels with the carried task into
   its prompt (and into QA's review prompt, so the second attempt is judged
   against the same context), and a task left for a person is not carried
   over on the company's own (a person still can). No usable answer keeps
   the default.
   Every question to the ScrumMaster (the triage, the procedure proposals)
   goes through `scrum_master.ask`, and every count of rework requests
   (the sprint list, the procedures' numbers) through
   `board.rework_request_count`.
7. Each hand-over between roles is also recorded as an `AgentMessage`
   (`agent_messages`), the company's conversation:
   - the Developer's question to the Architect and its answer (the Architect
     is told how tasks went after its earlier briefs, counted from the boards);
   - the handoff to QA, listing the declared files;
   - QA's decision or rework request, and the Verifier's;
   - escalations to the ScrumMaster when a task is blocked;
   - the ScrumMaster's retrospective to the team when a sprint closes.

   Messages are claims: comments still drive the review loop, and the evidence
   decides. `GET /v1/messages` (Viewer; filters `sprint_id`, `backlog_item_id`,
   `kind`) feeds the sprint's Conversation tab and the task page.
8. When a sprint closes, procedures are measured and proposed
   (`services/procedures.py`):
   - Each active procedure with two completed sprints since its adoption is
     kept, or reverted if rework per task or the share of blocked tasks got
     clearly worse. A reverted procedure brings back the version it replaced.
   - After a sprint with rework or blocked tasks, the ScrumMaster proposes up to
     two procedures in a fixed `PROCEDURE … END` format; anything else is
     ignored. With `autonomy_level` 3 a proposal is adopted at once; otherwise
     a Manager adopts or rejects it (`/v1/procedures`, the Procedures page).
     The ScrumMaster is shown what earlier procedures did (kept, reverted or
     unmeasured with the board's numbers, or rejected by a person;
     `procedures.history_prompt`), and a proposal with the same text as one
     reverted or rejected is dropped (`propose(skip_failed=True)`); a Manager
     writing a procedure can still bring one back.
   - Adoption records the numbers of the last two completed sprints as the
     baseline. Outcomes and proposals are posted to the team as `proposal`
     messages. The numbers decide, never a model.
9. With `AGENT_GRAPH_ENABLED=true`, `agent_worker` also runs the graph kickoff for
   the sprint; its transcript is ingested as an `AgentRun` (`le-fabrique-graph`).
   A failed kickoff never blocks the sprint.
10. Engram events go to Neo4j for episodic memory; embeddings to pgvector for RAG.

### Memory Architecture

- **pgvector (Postgres)** — RAG short-term semantic memory. Used for code snippet retrieval and context injection. API: `GET /v1/memory/search`, `POST /v1/memory/store`
- **Neo4j** — Temporal knowledge graph. Immutable "Engram" nodes record every agent decision with timestamps. Used for lineage, compliance, and cross-sprint learning. Queried via APOC plugin.

### Auth & Multi-tenancy

- Company-scoped: all data (agents, sprints, memory, budgets) is namespaced by `company_id`
- RBAC covers both human roles (manager, developer) and AI agent roles (PO, Architect, etc.)
- Passwords hashed with bcrypt directly (`backend/app/passwords.py`); the schema rejects a password over bcrypt's 72-byte limit, and login re-hashes a stored hash whose cost is below the current one. `POST /v1/auth/login` issues an HS256 JWT (`JWT_SECRET_KEY`); `auth_deps.get_current_user` resolves it. Authentication enforcement is on by default; set `AUTH_REQUIRED=false` only for a local demo without a login. Configure `JWT_SECRET_KEY`, then register and log in through the frontend. Protected routes require a JWT and company roles follow Owner > Manager > Viewer. A company's agents (`/v1/agents`) and its token usage are read by its Viewers; only its Managers create, change or delete an agent, since its prompt, model and budget decide what it does (the agents page is read-only for a Viewer). Likewise only a Manager records the folder a company's agents write to (`POST /v1/discover_team` with a `company_id`; the New company wizard calls it without one), and `/v1/generate` bills only a business unit of the company itself; a sprint's token total counts only its own company's audit rows. Company initialization creates the caller's Owner membership; invitations require Owner, and inviting an existing member is a 409.
- Always public: `/health`, registration/login, the HMAC-signed GitHub webhook, support bug reporting/chat, `/v1/models`, `/v1/capabilities` (feature flags only, no company data), and the retired `/v1/approve_task` (410). Glassroom SSE cannot take an Authorization header (EventSource sends none): with `AUTH_REQUIRED` it takes a five-minute `ticket` for one sprint from `POST /v1/sprints/{id}/stream_ticket` (Viewer), and checks the holder's membership again when it is used; the frontend asks for a new one on every connection. `/v1/auth/me` always requires a JWT. Audit writes and memory stores resolve company ownership from authorized membership and existing interactions, never payload metadata; callers with multiple memberships should select a `company_id` query parameter. Audit verification, memory search, and lineage use column-backed Viewer scoping. Legacy NULL-company evidence is flagged and included only without a company filter (authenticated when `AUTH_REQUIRED=true`). Startup backfills historical company metadata into the audit/vector columns and Neo4j properties. Help-desk tickets are not company data: support admins (`SUPPORT_ADMIN_EMAILS`) see and update all of them and download the logs; other authenticated users see only their own (matched by account email, 403 for another reporter, 404 for another's ticket). With `AUTH_REQUIRED=false` and no token, the desk stays open as in a local demo. When `SUPPORT_AGENT_ENABLED`, the Support agent triages open tickets first (`suggested_severity`, `suggested_category`, `agent_reply`, `triaged_by`), and a support admin decides.

## Test Suite

The backend has a pytest test suite under `backend/tests/`.  No external services (Postgres, NATS, Neo4j, LLM proxy) are required — every boundary is stubbed with an in-memory SQLite engine or `unittest.mock`.

```bash
# Run all backend tests (fast, DB-free)
cd backend && PYTHONPATH=.:app python3 -m pytest tests -q
```

Test files:

| File | What it covers |
|------|----------------|
| `test_glassroom_sse.py` | SSE fan-out and stream ordering; the replayed history carries the same event ids on every connection, so a reconnecting client can tell it from new events |
| `test_orchestration_metrics.py` | Orchestration metric helpers |
| `test_start_sprint.py` | `POST /v1/start_sprint` — happy path, validation, outbox events |
| `test_graph.py` | LangGraph node compilation, routing helpers, `_invoke_agent` events |
| `test_needs_split.py` | `_mark_sprint_needs_split` and `_complete_sprint_and_plan_follow_up` |
| `test_model_factory.py` | Role-to-model map and business-unit header propagation |
| `test_crypto.py` | Fernet credential encryption: round trip, tamper and wrong-key rejection, keyless fallback, legacy rows |
| `test_llm_routing.py` | Task worker: empty agent model → role default from `model_factory`, LLM failures on the board, `served_model` on the AgentRun |
| `test_task_delivery.py` | File manifest parsing, delivery to the real daemon against `tmp_path`, sha256 `files_written` check, daemon-down → unverifiable |
| `test_run_reviews.py` | Line comments (a changed file's path, a line needs its file, new or old side), Manager writes and Viewer reads, a review carries the open comments and leaves resolved ones out; request changes on a task blocked for a person sends it back with the comments as `NEEDS WORK:` feedback the Developer's prompt carries, refused while the agents still work on it or its sprint is closed; approve accepts a high-risk hold and is refused otherwise; another producer's run gets an approval record; company scoping |
| `test_delivery_undo.py` | Undoing a delivery from the daemon's snapshot (opencode's snapshots), real files in `tmp_path`, in a folder that is not a repository and in one that is: the snapshot is stored outside the project and the project's `.git` gains no object or ref; undo restores the modified files and removes the created ones with their empty folders; a file changed or deleted since is a conflict that changes nothing and names it; a second undo is refused; a file written with the same bytes is left out of the snapshot and a later edit of it blocks nothing; `app.py` and `./app.py` are one entry with the last write; a delivery, an edit and an undo wait for the project's lock, whichever spelling of its path they get; a write that fails midway is rolled back to the delivery's bytes and the snapshot still undoes it; a folder that cannot be written is found before anything changes (skipped as root); a file that may hold secrets is never copied into the store and the delivery goes on without a snapshot; a delivery on a Git branch has none; the daemon answers `workspace.undo` in the reply shape; the snapshot lands on the evidence and in the changes with `undone_at`; a Manager undoes it (audited), a Viewer and an outsider cannot, another company's run is a 404, a conflict is a 409 with nothing audited, a branch delivery or a run without a snapshot is a 409, no daemon a 503 |
| `test_git_publish.py` | Real git against a bare remote in `tmp_path`: the task branch is pushed and the answer is the compare page without `gh`, a fake `gh` opens a draft with the title and the evidence as body, a failing `gh` still leaves the compare page, a non-`fabrique/` branch, an undelivered one or no origin is refused, compare pages per platform; a Manager publishes with the evidence in the body and the claims apart, a Viewer reads the publications, 409 without a branch, 503 without a daemon, 404 from another company |
| `test_specifications.py` | The specification: ids assigned in order by the check, never by a draft; what blocks approval (no criterion, an empty step, a target without a number, an open question) and what only warns (a promise without a number, in English and Spanish); the PO's draft read from the fixed shape and nothing else, a revision prompt with the answers; a Manager writes, a Viewer reads, outsiders refused; the PO drafts, asks, a person answers, the revision passes the check and is approved; an unusable answer leaves the draft as it was; a revision supersedes the approved version on approval; the PO plans tasks with `covers` (unknown ids dropped, the uncovered listed, a draft refused, one task per requirement without a model); a sprint keeps what each task covers and refuses an unknown id; coverage per criterion from the boards; the prompts carry only the covered requirements; a carried task keeps its `covers` |
| `test_architectures.py` | The architecture: ids assigned in order and a draft's names mapped onto them; what blocks approval (an unknown module or requirement, a cycle named along its path, a decision without its decision, an open question) and what warns (an uncovered requirement, no path, no contract, no overview); the prompt section carries a task's modules and their dependencies, never the rest, plus every decision and convention; the Markdown document; the Architect's draft read from the fixed shape, the prompt with the specification, the stack and the answers; a Manager writes, a Viewer reads, outsiders refused; the Architect drafts from an approved specification only, asks, a person answers, the revision passes and is approved; a revision supersedes the approved version and the prompts follow it; without an approved architecture the prompts carry the requirements alone; writing to the repository keeps the daemon's hash, refuses a draft, reports a rejected write and a missing daemon |
| `test_acceptance.py` | An acceptance test per criterion: criterion ids as test-name tokens, the per-test lines of pytest, jest, go and cargo read back per criterion, pytest made verbose; the plan puts QA's test task per requirement first and re-points dependencies within the scope; through the real daemon, QA's failing tests are expected and every criterion must run, an implementation that leaves its criterion failing is refuted, the one that makes it pass verifies the criteria on the specification (with REQ-2's tests still failing, excused) and in the publication line, an implementation that edits an acceptance test, or a test changed behind the loop's back, is refuted and the test restored from QA's delivery before the run, nothing changes without a specification; the daemon hashes files as they are on disk; the history check refuses a criterion verified without an observed run |
| `test_standards.py` | The quality standards: a sound catalogue that binds every role, the prompt section with what is checked, the quality areas a specification forgets, secrets found (keys, private keys, passwords in code) and placeholders left alone, TODOs and unpinned dependencies as findings, the check over a delivery, the Verifier refuting a leaked secret and the gate sending it back, the catalogue served and documented |
| `test_dependencies.py` | Positions checked (unknown, self, a cycle named along its path), edges that close a cycle dropped one at a time; the PO names dependencies by title and the plan carries positions, a cyclic answer broken; a sprint keeps them, queues only the tasks that can start and the board shows `depends_on` and `waiting_for`; dispatch and the worker's gate queue a task once its dependencies are done; a task waiting on one left for a person is blocked with the reason, transitively, settles the sprint, is not blocked by one the worker still retries, and is released when the dependency gets done or is back in work; a comment anyone appends does not hide the hold and a send-back behind a stuck root is held again; a sprint reconciles its holds before it counts as settled; a closed sprint is left alone; a carried task keeps a dependency carried with it; a title longer than shown still names a dependency; the history check refuses a task that delivered before its dependency closed |
| `test_sprint_integration.py` | A sprint's integration against real git in `tmp_path`: two task branches merged in dependency order into `fabrique/sprint-<id>` with the person's checkout untouched, a second run adding nothing, a missing or foreign branch named; a conflict observed with its files and hunks, the merge aborted and the rest not attempted; through the API a Manager integrates and the suite runs in the integrated worktree (a Viewer cannot), the plan and history read by a Viewer, one request published with every task's evidence and the claims apart; a conflict and a failing suite wait in the inbox, a conflict is not published, the tests off say untested, refusals (workflow off, nothing done, no daemon), another company's member refused; a closed sprint integrated on its own only with the workflow on; the suite tested in a worktree detached at the commit, so a concurrent integration cannot change what is recorded; the request built from the runs recorded at integration, never a later one, with an unchanged rerun still describing every task and a producer's diff only as a claim; a done task without its branch is incomplete, waits in the inbox and is not published; the inbox counts every waiting integration beyond its limit; a daemon absent at close leaves a pending integration retried until it runs, a refusal an error for a person; a test worktree per attempt (a retry never resets another's files, one a command runs in or a young one is never removed, every `workspace.execute` marked in use), integrations within the concurrency limit, the advisory lock taken off the event loop from a lock pool of its own, the acceptance coverage read once per specification, a task branch read once and merged at that commit while a delivery moves it, the job kept before the daemon is asked and retried after a restart in the same row, two attempts at one job integrate once, a sprint another replica holds left to it (the advisory lock taken and released on Postgres), a row without its tasks' evidence not published, the acceptance line the one seen at integration |
| `test_local_merge.py` | Accepting work into a local repository with no remote: a task branch fast-forwarded into the checked-out base with the person's files following, a base that moved gets a merge commit by La Fabrique, a conflict changes nothing and names the file, uncommitted work on the base is never overwritten, a base nobody has checked out moves without touching the checkout, merging again says it is already in, a moved branch, a branch La Fabrique does not write or a detached checkout refused; a Manager merges a run (a Viewer cannot) and it is listed as a `local` publication with no comments to bring, a conflict records nothing, a sprint's integration merged locally and one that stopped at a conflict or failed its suite refused |
| `test_review_sync.py` | Fake `gh` and `glab`: line comments (an outdated one keeps its line), reviews (pending and empty ones left out) and conversation from GitHub across pages, GitLab notes without system ones, a missing or failing CLI and a page that is not a request refused with the reason; a Manager brings them once (a Viewer cannot), reading again adds nothing and an edit there updates a comment still waiting, one a review carried keeps what it said, the feedback names who wrote it and where, a compare page or another company's publication refused |
| `test_git_workflow.py` | Real git in `tmp_path`: a delivery is a commit on `fabrique/task-<id>` in a worktree with the task's trailers and La Fabrique as author, the person's checkout, index and branch untouched; another attempt is another commit, the same bytes none; a named base starts the branch there; a bad branch name or a folder that is not a repository is an error the backend reads; the spec only with the workflow on; a task delivered with it is verified with the commit on its evidence; Viewer reads and Manager sets the workflow; the changes name the branch and commit |
| `test_review_changes.py` | The daemon's diff of each write (added, modified, unchanged, binary, too large, the reply's cap), one parser for its diffs and a pull request's, `GET /v1/agent_runs/{id}/changes` from the daemon's observation or the pull request, a delivery from before diffs, declared files kept apart, company scoping |
| `test_delivery_tests.py` | Opt-in post-delivery test run: command detection, `tests_run` check, passing/failing suites through the real daemon |
| `test_review_gate.py` | Review loop: delivery gate before QA, explicit `APPROVED:` verdict, rework limit, company-scoped evidence |
| `test_board.py` | Reading a task's comment thread: carry-over notes and the console-advice note are never a verdict (an assistant's "retry" or "manual triage" moves nothing), pending rework survives gate verdicts and send-backs, rework budgets restart after a send-back, only the worker's note marks a task as advised and its rework counts from that note on; the Architect's brief is never a verdict either, a deliverable's words are not a block reason, and a briefed task's rework counts from the brief on |
| `test_risk_tier.py` | The risk tier: each area a path marks, look-alike words left low, the highest path decides, a producer raises but never lowers it, the Verifier's check never decides the verdict; an approved high-risk task waits for a person at autonomy 3, settles the sprint, is listed as `risk_hold`, is not a failure in the procedures' numbers and is not carried over on the company's own |
| `test_history_check.py` | The independent checker of task histories (`app/history_check.py`): done needs an approval or a person, an approval needs a deliverable before it and a verified latest run (unless a person closed it), a high-risk hold closes only through a person, a task is carried over once, a ScrumMaster decision answers an escalation of its own task; the database is read the same way, and the checker imports none of the production readers; with events, a comment naming a missing event (also when the task has no events) or saying something else than its event, an event that renders text without its comment, comments out of the events' order, a done task without an approval event and a status the events did not leave are refused |
| `test_task_events.py` | A task's life as events: one event per transition with its actor and the comment carrying its id, an unknown kind or an actor signing as another refused, the per-task chain shows an altered event; each review-loop outcome (approved, risk hold, QA rework and its limit, the delivery gate and its limit, a failed review) records its events and the projections answer as the comment readers do; dependency holds and releases, carry-over on both tasks, a sprint stopped, a credit pause, a person's accept and request for changes, the ScrumMaster's decision and a board PATCH are events; a thread the events do not cover is read from its comments; the history checker reads the events from the database and finds a comment naming an event the task lacks (with no events at all too), an event whose comment was deleted and comments out of the events' order; a writer holding a stale task keeps the comment committed meanwhile; an unsigned event after a signed one is a `downgrade`; a person's text is kept whole and the platform's capped; the inbox loads the events once for every task; migration 0014 backfills a readable thread, marks one it cannot read and reads a person's closing note as their approval; `GET /v1/tasks/{id}/events` is company-scoped |
| `test_sprint_close.py` | A sprint closes when every task is settled (blocked for a person counts; retries, re-reviews and freezes keep it open); the pre-review quality gate asks for rework with the reason, then blocks and escalates |
| `test_inbox.py` | `GET /v1/inbox`: tasks blocked for a person (frozen included, retries excluded), proposals waiting, undecided runs, company scoping |
| `test_carry_over.py` | A blocked task carried into the next sprint: `carry_over_ids` on `start_sprint`, the reason in the new task and its prompt, refusals (404/409, scope), the original leaves the inbox, the autonomy-3 follow-up carries once |
| `test_scrum_master.py` | The ScrumMaster's triage of an escalation: the prompt, the fixed answer format, the decision recorded and read back, nothing recorded without a usable answer, the brief in the next attempt, a task left for a person not carried on the company's own, the second-attempt track record in the prompt, a send-back during the triage window makes the answer stale, a decision names the escalation it answers and is judged by it, `END` with a period or Windows line endings |
| `test_migrations.py` | The schema's version (head `0015`): the migrations build the models' schema from nothing and `alembic check` finds no difference; a database from the old startup (tables present, columns and tables missing) is converged, not rebuilt, and a second run changes nothing; the backend refuses a database that is not at head naming both versions; `DB_AUTO_UPGRADE` runs the migrations at startup; one head |
| `test_database_timezone.py` | The Postgres session is pinned to UTC, so aware datetimes in `timestamp` columns and the naive UTC text in board comments stay comparable |
| `test_backlog_comments.py` | In-place appends to `BacklogItem.comments` are persisted |
| `test_kickoff_failure.py` | A failed graph kickoff leaves the sprint active and its tasks running |
| `test_nats_connect_retry.py` | NATS connect backoff; workers start once connected |
| `test_nats_security.py` | The bus's credentials and TLS (issue #327): the backend's and the daemon's connect options from the environment (none, user and password, a creds file winning, an nkey seed, a missing file named, a user without a password refused, TLS with a pinned CA and a client certificate), each component's reply inbox; no secret in the logs or `/health`; the warning for an exposed broker without credentials; the daemon waits for the backend's streams, never creates one and passes its inbox to the pull consumer; the daemon image copies every module; the committed `nats/nats-server.conf` parses and matches `scripts/nats_config.py`, `--check` fails when stale, no password in it, the daemon may answer but not send a request, assign a task, read the backend's replies or touch a stream, the names follow the real streams with a namespace; the daemon answers only under `_INBOX.backend.>` and has no `allow_responses`; `nats/start.sh` with both, one or no passwords and with certificates, refusing to start when `NATS_TLS` or `NATS_TLS_VERIFY` asks for a file that is missing (or half a key pair); compose hands the backend and the daemon container their client certificate inside the mounted folder; live against `nats-server` (on `PATH` or `NATS_SERVER_BIN`, skipped otherwise): the daemon answers the backend and consumes its stream but is refused `workspace.execute` and stream changes, a wrong password and an unpinned CA are refused, and a daemon credential that publishes with `workspace.execute` as reply subject cannot answer itself into a command while the backend's request still gets its reply |
| `test_console_capabilities.py` / `test_daemon_capabilities.py` | `workspace.capabilities` probe and daemon handler; assistants skipped when absent |
| `test_console_assistants.py` | Which assistants a task gets, the prompt quoted into the command, persisted quota and missing-binary cooldowns, and the full path with its Glassroom telemetry |
| `test_sprint_list.py` | `GET /v1/sprints`: progress, blocked and rework counts per sprint, second attempts (done, and briefed by the ScrumMaster), tasks with and without console advice and the Architect's brief, ordering, membership scoping |
| `test_architect.py` | The Architect's record: briefed tasks against the rest over the company's completed sprints, rework from the brief on, and that record in its next consultation prompt |
| `test_agent_runs.py` | AgentRun ingest, verification, `external_ref` and `sprint_id` filters |
| `test_jetstream_durability.py` | JetStream publish/consume/ack and core fallback; deliveries queued for an offline daemon, held by the gate, verified from the daemon's report |
| `test_agent_messages.py` | The conversation between roles: messages at each hand-over, escalation and retrospective; `GET /v1/messages` scoping and filters |
| `test_procedures.py` | Proposal parsing, versions and supersession, baseline at adoption, keep / revert / unmeasured after two sprints, ScrumMaster proposals by autonomy level, what earlier procedures did in the proposal prompt, a reverted or rejected text not proposed again (a person still can), procedures in prompts, `/v1/procedures` |
| `test_memory_lessons.py` | Company-scoped memory search, retrospectives visible to every role, lessons from the last completed sprints in the task prompt |
| `test_support_agent.py` | Support agent triage: fixed answer format, suggestions beside the reporter's choice, unusable answers left to people, failed calls retried, reply visible to the reporter; its record against the admins' decisions (unusable answers as misses, beside the reporters who chose a value, legacy rows compared case-blind) and that record in its next prompt |
| `test_reviewer_agent.py` | The Reviewer agent: fixed answer format, the prompt treats the run as data and carries the Verifier's checks, a suggestion stored on an outside producer's run beside no approval (in-house and decided runs skipped), unusable answers left to people, failed calls retried; its record against the decisions people recorded, with the Verifier's verdict as the baseline, company-scoped, and in its next prompt |
| `test_brief_mail.py` | The client's brief by mail: sent to the company's Owners and Managers (never a Viewer) once per interval, the next window starting where the last sent one ended; a failed send audited with the error's type and no address, retried from the same start once an hour; nothing without SMTP, a Manager policy or a recipient; the render lists the decisions the agent took alone (not mere suggestions), what waits and the quota; STARTTLS and login |
| `test_manager_agent.py` | The Manager agent: the fixed answer format (another category, a decision the category does not allow, no reason or no `END` are unusable); the confirmation set cannot be unlocked, by the API or behind it; each rule holds only on its facts (verdict, tier, files, criteria, autonomy level, a reverted procedure) and only for its own decision, never over budget, after an override or when flagged; an autonomy-1 hold accepted alone, noted and audited with the rule, and the history check accepting it while still refusing the agent on a high-risk hold; a high-risk hold left to the client; no policy, only suggestions; an unusable answer left and not asked again, a failed call writing nothing, one row per situation, a second replica skipping an item the first is deciding without asking the model, the loop skipping a round another replica runs; a procedure adopted alone only at level 3, an outside run approved alone through the approval record; a Manager writes the policy and a Viewer reads it; the override (Manager only, once, company-scoped) puts the task back in the inbox, restores a superseded procedure, records a rejection, is counted and keeps the agent off that item; the record's agreement with the client's decisions in the next prompt; a rule whose overrides rise flagged and not applied, the policy untouched; the brief and its scoping |
| `test_llm_client.py` | The proxy client: `max_tokens` travels only when set, the default wait follows the proxy's upstream budget, an error the proxy answered is not sent again (no status is retried), a request that never reached the proxy is |
| `test_token_diet.py` | What the worker stops asking twice: a Developer answer cut at the length limit is sent back as such instead of "the complete result again", a formless QA answer is asked again with the format named, the fixed-form answers are capped and the deliverable is not, every wait follows the proxy's budget |
| `test_review_digest.py` | What QA is shown of a delivery: every file listed (lines, bytes) and headed `# Artifact:`, an excerpt of each within `DIGEST_CHARS` saying how many lines it left out, where the old 3000-character cut showed one file of three; text without files goes as it was; the Developer prompt runs from stable sections (output format, procedures, lessons) to variable ones (Architect brief, review feedback) so the providers' prefix cache applies |
| `test_support_access.py` | Support admins versus reporters on tickets and logs, `support_admin` in `/v1/auth/me`, 409 when re-inviting a member; an admin's severity and category decision stored apart, credited to whoever changed it and clearable, unknown values refused, `/v1/support/agent_record` admin-only; the reporter's values normalized and no severity or category made up; the Impact filter reads the decision first, else the reporter's choice; `resolved_at` is when the ticket was resolved, kept by later saves and cleared on reopening |
| `test_agent_access.py` | A company's agents and token usage: 401 without a token, 403 outside the company, a Viewer reads but cannot create, change or delete, a Manager can |
| `test_cross_company_access.py` | Only a Manager sets where a company's agents write (`discover_team`), `/v1/generate` bills only the company's own business unit, a sprint's tokens are its own company's |
| `test_glassroom_access.py` | The Glassroom stream with authentication required: a sprint-scoped ticket for a Viewer, refused without one, with an access token, another sprint's or an expired one, and once its holder left the company |
| `test_workspace_host.py` | Daemon folder browsing, git remote/branch inspection, snapshots, Windows shell/roots; `/v1/workspace/*` relay and discovery through the daemon |
| `test_audit_chain.py` | The audit hash chain per company: numbered and linked rows, each company its own chain, a deleted, altered or moved row breaks it where it was, a chain rewritten without the key does not verify, unkeyed chains say so, rows without a company stay unchained, KPI turn rows are signed and chained too |
| `test_command_policy.py` | The daemon's command policy: `$(...)` and nested substitutions checked without recursing forever (too deep is refused), allow matches whole words as an argv prefix (`git status` never allows `git push`, `ls` never `lsof`), backticks also inside double quotes and never inside single ones, file redirections refused in allow mode (`2>&1`, `/dev/null` pass) and inspected in deny mode, quotes do not hide a denied word, unreadable text and unknown modes refused; through the daemon's `workspace.execute`, nothing runs without a policy changing, a refused command never reaches the shell and says why, an unreadable policy is refused, `FABRIQUE_COMMAND_DENY` wins over the request; syntax the scanner does not model is refused (here-documents, `$'…'` quotes, comments, backslashes inside backticks, a non-ASCII stream number), a line continuation inside double quotes is read as the shell does, allow patterns are case-sensitive, an empty or malformed policy and a command that is not text are refused, and on Windows a policy refuses rather than read `cmd.exe` syntax with POSIX rules |
| `test_app_check.py` | The execution environment (issue #315): each stack's profile detected (Node, Python, Go, Rust, Maven, Gradle, .NET, PHP, Ruby) with its install, start and port, e2e and lint from `package.json`, manifests recognised at any depth, the deployment's then the company's overrides with an empty command turning a step off, bad overrides refused; an install only for a written manifest whose hash changed, recorded after an exit 0 and not run again for the same bytes, a failed one refuted and retried, off when disabled, the install's network setting applied both ways, a manifest the install does not cover (a root npm install and `backend/requirements.txt`) never recorded as installed, the resolved profile's test command run (no suite, no command; a stack override's runner; Poetry and Pipenv through their runners), the integration's tests alone, the reply wait covering the container's launch and teardown, a launch timeout or an unreadable log still removing the container, the process tree killed on Windows, a timed-out e2e command's children killed, the daemon bounding a requested timeout; a real `http.server` started, probed and stopped, an app that exits before listening refuted with its log, one that never listens stopped at the bound, a 500 refuted, the e2e command against the running app, the command policy, a busy port and a required sandbox refusing before anything runs, container mode through a fake runtime; through the task worker the install, tests and application checks recorded, a failing start or install sent back, no start after failed tests, the check opt-in, a company turning its tests off; a Manager sets the profile and a Viewer reads it |
| `test_sandbox.py` | Sandboxed execution: container mode through a fake runtime (worktree mounted, network off, limits, capabilities dropped, only the listed variables, the daemon's report), a mode that cannot run is an error and never a host run, host reports itself unisolated and the daemon can force a container, a hung sandboxed command is killed, limits are bounded, the company's mode in the delivery test run and the Developer's test runs, the evidence keeps the daemon's report and the Verifier records it (host and an older daemon as findings), a Manager sets the company's sandbox |
| `test_research_tools.py` | The research tools: a page read as text and recorded with its hash, a host off the list refused and the refusal recorded (a redirect that leaves the list too; plain http only for a host named explicitly), an error page and a binary as errors the model sees, a package looked up at each registry, advisories from OSV with the version asked, the tools for the roles that decide and the `## Sources` prompt section, `research` a reserved MCP name, the combined toolbox without a workspace root, the Verifier's `sources` check naming the dependencies nobody looked up, a Manager adds hosts and a Viewer reads them, the PO's draft keeps the source it consulted |
| `test_developer_tools.py` | The Developer's workspace tools: the daemon lists the tree without dependencies or VCS data, reads a file numbered with its hash and tells a binary apart, cuts a long file and says how long it is, searches and refuses a bad pattern, answers over NATS in the reply shape; the toolbox offers the read tools and the test run only with a command, records a read with the file's hash, turns errors into results, runs the project's command or a narrower one on the same runner (shell syntax falls back), the combined toolbox dispatches by prefix and caps the rounds, the tools go to the roles that deliver files when the project is known, the prompt carries the repository map; the Verifier says which modified files were read first and the tier follows; the worker offers the tools, reads through the daemon and keeps the reads as evidence; the history check refuses a read recorded without a hash; a narrower test command must be the same runner exactly, files that may hold secrets are never read nor searched, a secret-looking line is redacted, a symlink out of the project is never opened, a large file is hashed whole but only its head read, a search that backtracks forever is stopped at the deadline and one over more files than the bound still scans the bound, a test run with no sprint is reported to nobody, the tools look at the task's worktree once it delivered there |
| `test_mcp_tools.py` | MCP tools in a task, against a real MCP server in-process: discovery in the OpenAI `tools` shape with prefixed names, the loop runs the calls and records them (arguments, error, sha256, read-only), results masked for the server's secrets and cut with the record saying so, a failing, unknown or malformed call is an error result, the round cap ends in a request without tools, an unreachable server is named and the task goes on, the Verifier records the calls as `tools_used` and a writing tool raises the tier, the loader takes the company's enabled servers for the role, and the worker keeps the calls as evidence apart from the claims |
| `test_mcp_config.py` | The `mcpServers` map: stdio by `command`, http by `url`, `type`/`transport` aliases, unknown keys dropped, each refusal with its reason; secrets encrypted at rest and listed by name only, a row the key cannot read still lists |
| `test_mcp_router.py` | `/v1/mcp/servers`: a Manager adds (by name, replacing), disables, restricts and removes servers, a Viewer reads, an outsider gets 403 and no token 401; no answer carries a secret; the tools endpoint reports what the server advertises or why it could not be reached |
| `test_work_control.py` | Sprint 0 starts only when asked and never at autonomy 1; a planned sprint starts once and dispatches its tasks; pause, resume and stop (Manager only; stop blocks unfinished tasks); the autonomy level read by a Viewer and changed by a Manager; a planned, paused or cancelled sprint's task is not loaded, dispatched or reviewed; a credit answer is told from a passing error; no credit pauses the sprint and the task waits as todo; the execute call names the provider without credit; at autonomy 1 every approved task waits for a person |
| `test_webhooks.py` | The company's notification webhook (issue #357): a closed task and sprint queued with the change, a blocked task with its reason and whether it waits for a person, a refuted run (a verified one is not), only the events asked for, only while on and only for its company, nothing for a change rolled back; the delivery signed with HMAC-SHA256 and recorded, a failure recorded and raised for the retry, nothing sent for a webhook turned off since; an https URL without credentials, a private address refused unless allowed; the secret encrypted, kept when omitted, refused without a key; a Manager configures, a Viewer reads, an outsider neither, no answer carries the secret |
| `test_workspace_isolation.py` | The daemon writes only inside the project (issue #357): an absolute path, `..`, a symlinked file or folder that leads out are refused; VCS metadata (`.git/hooks`, `.git/config`, `.hg`, `.svn`, a nested `.git`) is never written, nor reached through a symlink; a symlink inside the project still works and look-alike names (`.gitignore`, `.github/`) are written |
| `test_run_secrets.py` | Secrets for one task run (issue #357): stored only encrypted (nothing without `FERNET_SECRET_KEY`), named as an environment variable, created only for a task of the company, listed by the task's company only (an empty list is still authorized, an unknown task is a 404), handed to that task's run without another company's rows, an undecryptable one left out and logged, never returned by the API |
| `test_tenant_scoping.py` | Every table carries a required `company_id` that references `companies`, or is listed with its reason (the tenant, users, help-desk tickets, the outbox, backlog items through their sprint); `audit_log` alone may hold rows without a company; the lists hold no stale names |
| `test_agent_tools.py` | The Developer's tools from SWE-agent's agent-computer interface (issue #359): a read pages in windows of 100 lines headed `lines X–Y of N`, a start past the end refused, an empty file said so; a search over the bound answers the count per file and asks to refine, a search without a match says so; `find_file` by name, glob or path glob, never a hidden or secret file, the count past the bound; a command without output says so; an edit (line range, insertion, SEARCH/REPLACE) applied with the hashes before and after, a syntax-breaking Python, JSON or JavaScript (`node --check`) edit refused with the file untouched, an ambiguous search refused, an edit outside the project, into `.git`, into a secret or past the file refused, at most `EDIT_MAX_FILES` per task; finish hands back the final texts and restores the originals; the daemon answers the edit subjects over NATS; the history check refuses an edit recorded without its hashes; the toolbox offers the edit only where edits may go (a worktree, or no Git workflow), records it as an observation that does not raise the tier, refuses a file type a delivery does not write and brings the edits back on close; the worker delivers them and the daemon sees a modified file, verified; finish never follows a folder swapped for a symlink and keeps the journal, a file someone saved after an edit is kept, refused to a later edit, not delivered and recorded as a conflict, a window cut by the result budget names the lines shown and the next `start_line`, the minimum-deliverable gate counts the edited files, a secret written through the edit tool refutes the delivery (re-verified too), QA's tests written with the edit tool can be restored, a JavaScript check that cannot complete refuses the edit, an append after a last line without a newline keeps the lines apart |
| `test_edit_blocks.py` | SEARCH/REPLACE edits in a deliverable and the syntax check before writing (issue #359, from Aider and SWE-agent): a block marked `edit` is read as edits, with no expected hash; the daemon applies an edit only when its search text appears exactly once, refuses a missing or ambiguous one, and writes a `.py` or `.json` file only when it still parses (parsed, never run); the Verifier names the daemon's reason |
| `test_provider_connections.py` | A company's model provider connections (issue #356): a Manager connects, changes and removes them, a Viewer reads them, nobody outside the company does; the key is encrypted at rest and never returned, an omitted key keeps the stored one and an empty one clears it; base URLs and headers are checked (local servers allowed on purpose); the validation lists the provider's models with the key and records the outcome, which a later change to the key or URL clears |
| `test_model_catalog.py` | The model catalogue from models.dev (issue #356): the shipped snapshot describes the providers callable through an OpenAI-compatible client with each model's capabilities, limits and price; a refresh replaces it and a failed one keeps it; a connection's base URL comes from the catalogue; the catalogue routes are public; a company's models merge the catalogue with what each enabled connection listed at its validation |
| `test_proxy_connections.py` | A company's provider connection reaches the LLM proxy (issue #356): `conn:<id>:<model>` resolves into the model the proxy calls and the headers carrying the connection with the shared internal token; only the company's own enabled connection is used, never another company's; without the token or the company the call fails with the reason; plain model ids are untouched |
| `test_company_models.py` | A company's models (issue #356, step 4): the agent's model, then the company's for the role, then its default, then the deployment's; the Reviewer follows QA unless it has its own; a Manager sets them and a Viewer reads them with what each role runs, an empty value clears one, an unknown role, a malformed id or another company's connection refused; the ScrumMaster, PO, Architect, the worker's task, Architect consultation and the Reviewer agent run the company's choice, and the agent's own model still wins; an agent's model may be a connection of its own company only |
| `test_model_suggestions.py` | The model suggested per role (issue #356, step 6): the catalogue's real fields decide, the deciding roles get a model that reasons, the coding roles the largest context with tools, the TechWriter the cheapest; no tools, no suggestion; a validated provider only suggests what it listed; an unpriced model is not taken for free; each suggestion is the connection's own reference; once the company ran a model on three of its tasks in a role, more done than not, its record decides (least rework per task), counted for the model of each task's last delivery in completed sprints, a record of mostly blocked tasks never outranks the rule; a Viewer reads them, an outsider does not |
| `test_replicas.py` | Two workers against one database (threads on a file-backed SQLite): a todo task claimed once and a task given to both run once, a restarting worker leaves the tasks another holds, a surviving worker's review cycle recovers and re-queues the task of one that died mid-task (and leaves one a live worker holds), a sprint closed once with one follow-up, one retrospective, one procedure measurement and one proposal round, one triage per escalation (a new escalation gets its own), one review cycle and one Support/Reviewer cycle at a time; with `REPLICAS_POSTGRES_URL` the advisory lock and the conditional updates on Postgres (the e2e workflow runs these) |
| `test_retention.py` | The janitor's retention over `sqlite3`: delivered outbox events go and pending or failed stay, a superseded run version goes with its verifications while the latest and one a person decided on stay, a decision recorded after the batch was picked keeps its run, a failing batch rolls back with the verifications in place, an autocommit connection refused, KPI turns outside the audit chain go and a chained one is never deleted (counted as kept), nor one a run points at, task events of closed sprints only, `0` keeps everything, settings from the environment with safe defaults |
| `test_metrics.py` | `/metrics` labels requests by route template, a fixed route by its path, a CORS preflight as `preflight` and a path no route answered as `unmatched`; memory search caps its `limit` |
| `test_bench_scorecard.py` | The bench (issue #316): every reference specification is data the check approves, numbered as the backend numbers it, with a person-written test per criterion kept apart from the fake producer's files; the grade reads pytest's JUnit report per criterion (pass, fail, missing; a skip is not a pass), the fake's files pass, an empty project fails, a wrong reader fails only its criterion; first attempts counted from the board (a gate's, the Verifier's or QA's `NEEDS WORK:`, a person's send-back, a second delivery; a carry-over note is not this task's), a project's `pytest.py`, `sitecustomize.py`, `conftest.py` or `json.py` cannot replace the grader or forge its report, a report pytest did not finish is not read, a failed collection is an `error` entry, the scorecard's totals and schema (each violation named, NaN, infinities and missing token counters refused, exit 2), the Markdown; `broker.py` refuses port 4222, a broker where a daemon answers and says when one cannot be reached; `compare.py` finds a drop in criteria or first attempts, a rise in rework or tokens and a spec that stopped completing beyond the thresholds, lists a new spec without judging it, picks the newest earlier scorecard and exits 0, 1 or 2; the committed scorecards keep to the schema |

**End to end, against a real stack:** `scripts/e2e/run.sh` starts the backend,
the workspace daemon and a fake LLM proxy (`scripts/e2e/fake_llm.py`,
deterministic answers) against a Postgres and a NATS you provide
(`docker compose up -d postgres nats`, or a local pair via `E2E_POSTGRES_*`
and `E2E_NATS_URL`; the e2e database is recreated on every run), then drives
one company through the loop and checks it through the API (a fake Copilot
CLI, `e2e-copilot`, on the daemon's PATH advises every task, so the
console-assistant path runs too): the foundation
sprint closes with its task blocked for a person after two rework requests,
the ScrumMaster's triage of it, the retrospective and an adopted procedure;
the inbox lists the task;
`start_sprint` with `carry_over_ids` retries it beside a new task whose
files are written and verified; both tasks were advised and the advice reached their prompts; the sprint list counts the retry as a briefed second attempt; the inbox then lists only the new attempt;
a person carries that attempt into a third sprint, where it blocks again and
the ScrumMaster's triage is told how the company's second attempts went
(counted from the boards), the procedure adopted after the foundation sprint
is measured over the two sprints since and kept, and the proposal prompt
lists what earlier procedures did. An Architect hired after the foundation
sprint briefs each new task after its first rework; the sprint list counts
the briefed tasks, and the Architect, asked again, is told how its earlier
brief went. Last, the help desk: the Support agent
(enabled for the run) triages a ticket from the public form beside the
reporter's choice, a support admin decides, `/v1/support/agent_record`
counts the decision, and the next ticket's prompt carries that record.
Then the Reviewer agent (enabled too): a run ingested from another
producer gets a suggested decision beside no approval, a Manager decides
against it, `/v1/agent_runs/reviewer_record` counts the miss beside what the
Verifier's verdict alone would have decided, and the next run's prompt
carries that record. Then the company connects its own provider (the fake
proxy plays it): the validation lists its models with the key, the suggestion
for the Reviewer is the listed model the catalogue describes, a Manager
chooses it, and the next run's review reaches the proxy through that
connection with its key, and only that one. Then a task whose file marks authentication is approved
by QA and still waits for a person (the risk tier, at autonomy level 3), the
inbox lists it as a high-risk hold, and a Manager accepts it. Then a
specification: the PO drafts it from a brief, the check blocks it (a target
without a number, an open question), a person answers, the PO's revision
carries the answer and passes, a person approves it, the PO plans a sprint
whose tasks name the criteria they cover with nothing uncovered, the sprint
runs to done, each task's prompt and QA's review carried only its own
requirements, and the specification's coverage reads every criterion as done.
Between the approval and the plan, the Architect designs it: a draft from
the specification with an open question, answered, revised, approved,
written to the repository as `docs/ARCHITECTURE.md` with the daemon's hash
kept; the planning prompt carries the architecture and each task's prompt
the modules its requirements map to. The plan puts the counter after the
greeting, the board shows it waiting, and the counter's prompt comes only
after the greeting's review.
Then the Manager agent (enabled for the run, suggesting all along without a
policy): a Manager gives it the `accept_delivery` rule (a policy naming
`accept_high_risk` is a 422) and moves the company to autonomy level 1; of a
sprint's two approved deliveries the agent accepts the low-risk one alone,
audited with the rule, while the one whose file marks authentication waits
for the client with the agent's suggestion beside it; the client overrides
the acceptance (a second override is a 409), `GET /v1/manager_record` counts
it, and the client's own acceptance of the high-risk one counts as agreeing
with the suggestion.
At the end, `app/history_check.py` reads every task's history from the run's
database, sharing no code with the readers it checks, and the run fails on
an illegal one.
The fake model also checks the loop's order from its side: a QA review of a
task must follow a delivery of it, one review per delivery, and show QA the
files that delivery wrote; a violation fails the run.
With `E2E_NATS_SERVER=<nats-server binary>` the run starts its own broker with one user per component (`scripts/nats_config.py --namespace e2e`) and checks `/health` reports the backend authenticated; without it, nothing changes.
Run it with the backend's interpreter (`E2E_PYTHON` pointing at the venv's
`python`, `$(cd backend && poetry env info --path)/bin/python`); it exits 0
only when every check passed and leaves the logs in `E2E_WORKDIR`. CI runs it
too (`.github/workflows/e2e.yml`) on every pull request that touches
`backend/`, `vscode-daemon/`, `scripts/e2e/` or the workflow itself, and on
pushes to `main`, against a `pgvector/pgvector:pg15` service and
a NATS container started with JetStream; the logs are kept as the `e2e-logs`
artifact. After the loop (host matrix entry), the same job backs up the e2e
database with `scripts/backup.sh`, restores it into a scratch database and
compares its tables and audit rows, then runs `test_replicas.py`'s Postgres
races against it (`REPLICAS_POSTGRES_URL`); a change to `scripts/backup.sh`
triggers the workflow too. Two backend processes driving the whole loop
together is not run yet (issue #328, next).

**The bench** (issue #316, `bench/README.md`): `scripts/bench/run.sh`, on
the e2e's stack (`BENCH_*` variables; Postgres falls back to the `E2E_*`
ones, with its own database `factory_bench`) on a NATS broker of its own
(it starts `nats-server` when installed; `scripts/bench/broker.py` refuses
port 4222 and a broker where a daemon already answers, since the daemon's
request subjects are not namespaced), drives each
reference specification in `bench/specs/<name>/` through the factory: a
company (autonomy 2, Sprint 0 left planned) with a PO, a Developer, QA and a
ScrumMaster, the specification posted as data (`spec.json`, `POST
/v1/specifications`) and approved, the PO's plan from it, the sprint run to
its close (`BENCH_TIMEOUT`, after which it is stopped). Then the acceptance
tests a person wrote (`<spec>/acceptance/`, never shown to the agents) run on
the produced folder and decide each criterion (`scripts/bench/scorecard.py
grade`: a test named `test_req_1_2_...` decides REQ-1.2, read back from
pytest's JUnit report; pytest starts with `-I`, the project only at the end
of `sys.path`, the report in a random folder of the grader's, read only after
an exit of 0 or 1), and `scorecard.py collect` records tasks done at the
first attempt (done, no `NEEDS WORK:` or send-back on the board per
`services/board.py`, at most one delivery), rework requests per task,
blocked share, wall-clock and
the tokens of the audit log's turns whose usage the provider reported, with
the models served and the factory's own verified criteria beside the grade.
A failed collection marks the entry `error`, and a failed grade or
collection stops the run. `scorecard.py write` checks the scorecard against
its schema (`scripts/bench/schema.py`: every counter present, every number
finite; exit 2 otherwise) and writes `<stamp>.json` and `<stamp>.md`; the references
kept live in `bench/scorecards/`. `scripts/bench/compare.py` diffs a
scorecard with the newest earlier one and exits 1 beyond a threshold
(criteria, first attempts or blocked share by `--rate`, rework per task by
`--rework`, reported tokens by `--tokens`, a spec that stopped completing).
By default the models are `scripts/bench/fake_llm.py`, which answers each
spec from its `fake/` folder (the PO's plan, QA's tests, the Developer's
files, every review approved), and the run fails unless every spec completes
with every criterion passed; `BENCH_LLM_URL` points it at a real proxy
instead. `.github/workflows/bench.yml` runs the fake circuit on demand and
nightly on the self-hosted runner, compares with the committed reference,
puts both in the job summary (also when the run failed, if a scorecard was written), opens an issue on a nightly regression and
removes its containers; the real-model run is not wired yet. The idea is
taken from SWE-bench Verified (graded by tests a person wrote) and OpenHands'
evaluation harness (the version beside every number).

`scripts/check_file_sizes.py` is a ratchet on file size: a source file
(backend app, daemon, proxy, scripts, the frontend's `src`, tests excluded)
stays at or under 600 lines, and the files already over it, listed with
their size in `scripts/file_size_baseline.json`, may shrink but never grow.
When one shrinks, `--update` lowers its entry. CI runs it on every pull
request that touches source (`.github/workflows/file-sizes.yml`).

The LLM proxy has its own suite under `llm-proxy/tests/` (model routing,
`unknown_model` 400s, upstream errors, catalogue-only fallback), run from the
repo root with the proxy's Poetry environment plus
`llm-proxy/requirements-dev.txt`: `python -m pytest llm-proxy/tests -q`. CI
runs it after the backend tests.

CI jobs run on the self-hosted runner labelled `lafabrique`
(`runs-on: [self-hosted, linux, x64, lafabrique]`), not on GitHub-hosted
machines; only the SLSA release generator stays on `ubuntu-latest`. The
runner keeps its state between jobs, so a job cleans up what it starts (the
e2e removes its NATS container) and avoids the ports a local stack uses.

CI runs this suite on every pull request that touches `backend/` or the
workflow itself, and on pushes to `main`
(`.github/workflows/backend-tests.yml`), installing dependencies with Poetry
from `backend/poetry.lock`. The lint step runs the ruff version the workflow
pins (`pip install "ruff==0.17.1"`); lint locally with that same version, since
ruff's default rule set moves between releases. Feature-branch pushes do not trigger it; open a
pull request to get a run. Add new backend dependencies to
`backend/pyproject.toml` and commit the refreshed lock file; CI installs from
`backend/poetry.lock`.

Frontend changes are checked separately by
`.github/workflows/frontend-build.yml` on every pull request that touches
`frontend/` or the workflow itself, and on pushes to `main`.
That job runs `npm ci`, `npx tsc --noEmit`, `npm run lint`, `npm run test:run`,
and `npm run build` from the `frontend/` directory, with no Actions cache: the
self-hosted runner keeps `~/.npm` between jobs, and archiving it (1.8 GB)
timed out and cancelled jobs whose steps had passed.
`frontend-build.yml` is the only frontend workflow: `frontend-tests.yml` was a
duplicate of the same checks on the same triggers and was removed.

`npm run lint` (`frontend/eslint.config.mjs`) enforces the i18n rule below with
`eslint-plugin-i18next`: any literal text in JSX, or in a `title`, `alt`,
`placeholder`, `aria-label` or `label` attribute, fails the build. Numbers,
punctuation, emoji, ALL_CAPS codes, hex colours, Tailwind class lists and the
brand name are allowed; keep value lists (enums, time zones) in module
constants rather than inline in JSX.

The frontend test suite uses Vitest with Testing Library and jsdom. Run it from
`frontend/` with `npm run test` for watch mode during development, or
`npm run test:run` for the non-watch CI/local verification command.

| File | What it covers |
|------|----------------|
| `src/components/AnalyticsDashboard.test.tsx` | Orchestration health KPI rendering, loading/error handling, empty state, and pre/post Batch 1 weekly trend behavior; how many Developer turns had no reported token count, shown only when some did |
| `src/pages/AgentSettings.test.tsx` | Model selector lists `/v1/models` entries after a "Default for role" option; the models the company's validated connections listed, grouped by connection; a Viewer reads the agents with nothing to change them, a Manager changes them |
| `src/i18n/coverage.test.ts` | Every statically referenced translation key exists in both `en.json` and `es.json` |
| `src/App.test.tsx` | Navigation order, capability-gated links, redirects, language switcher |
| `src/pages/Sprints.test.tsx` / `src/hooks/useSprints.test.tsx` | Sprint list page and its `GET /v1/sprints` hook; the Manager agent's record (`GET /v1/manager_record`: agreement and overrides by category, flagged rules) beside the other numbers, only once it has decisions |
| `src/components/LearningTrend.test.tsx` | The "Is the company learning?" tiles: rework per task and blocked share per closed sprint, the change since the previous one, the sparkline's values and the table view; second attempts that got done, with and without the ScrumMaster's brief; tasks with console advice, and tasks the Architect briefed, against the rest |
| `src/components/SprintControls.test.tsx` | Start for a planned sprint, the reason and Resume for a paused one, nothing while it runs; Stop only after confirming, no Pause or Stop on a finished sprint; a Manager changes the autonomy level and a Viewer only reads it; the wizard's start-now choice, disabled at level 1 |
| `src/pages/SprintWorkspace.test.tsx` | Sprint page tabs, URL sprint validation, Pause/Resume |
| `src/pages/TaskDetails.test.tsx` | Task page errors and delivery evidence |
| `src/pages/Procedures.test.tsx` | Proposals, procedures in force and history with their numbers; Manager adopt and write; load error retry |
| `src/components/SourcesConsulted.test.tsx` | The sources an agent consulted while drafting: a fetched page linked with its status and hash, a package lookup, a refused host marked and not linked; nothing without sources |
| `src/components/Conversation.test.tsx` | Sprint and task conversation: who spoke to whom, kind filter, long messages, empty and error states |
| `src/components/GovernancePanel.test.tsx` | The Review inbox: blocked tasks with their reason and a Manager's "Send back to work", "Accept" on an approved high-risk delivery only, proposals linked to Procedures, undecided runs with the Reviewer agent's suggestion beside each and its record against the decisions, how earlier second attempts went beside the blocked tasks, Viewer and error states |
| `src/components/ManagerDecisions.test.tsx` | The Manager agent on the Review page: a decision taken alone with its rule and a Manager's one-click override (none for a Viewer or once overridden), a suggestion shown as a claim or said to be unusable, the client's brief with the over-budget note |
| `src/components/ClientRequestBox.test.tsx` | The client's request box on the Review page: what they wrote, trimmed, sent to the PO (`POST /v1/specifications/draft`), the link to the draft specification they approve, the wait and the backend's refusal |
| `src/services/manager.test.tsx` | An override reads again every view it can change: the boards, procedures, runs and approvals, the inbox and the record |
| `src/pages/EvidenceDetail.test.tsx` | Run page: claims beside observations, audit rows, the review form's validation and body, task links; the Reviewer agent's suggestion shown as a claim beside the review form, and when it could not suggest; the Files changed tab with its count |
| `src/components/ReviewChanges.test.tsx` | Files changed as a pull request shows them: each line with its old and new number, the file list, Viewed folds a file, why a file has no diff, declared files kept apart, a long file shown in part, the source named, retry; a Manager comments on a line (a deleted line on its old side) and resolves, a Viewer gets no comment button |
| `src/components/UndoDelivery.test.tsx` | Undoing a delivery: only after the Manager confirms, what was restored and removed shown, the refusal shown, nothing offered once undone; on Files changed for a Manager only, never for a Viewer or a delivery on a Git branch; another run starts afresh, whatever the previous undo did |
| `src/components/PublishBranch.test.tsx` | A Manager publishes the branch, and brings back the request's comments (not for a compare page or a Viewer), with what came; the comments from the request on no line shown are listed with a link to the platform and their state; the request it opened is linked for everyone; a remote without a web page still says it was pushed; nothing for a Viewer before a publication; the daemon's refusal shown |
| `src/components/ReviewPanel.test.tsx` | Submitting a review (the decision picked, the text trimmed, how many line comments go with it), the backend's refusal shown, what a review did, the history, a Viewer reads only |
| `src/lib/patch.test.ts` | A file's hunks as review rows with their old and new line numbers |
| `src/pages/HelpDesk.test.tsx` | Help desk for support admins: filters, resolution required to close, logs download, the agent's suggestions beside the reporter's choice, an admin's decision on impact and category (only a decision changed in the form is sent, one put back as the ticket stores it is not, an empty one clears it, one another admin made meanwhile is kept, a ticket another admin resolved meanwhile is not reopened, and after a save the form follows what is stored), the list showing the decided impact or the reporter's marked as theirs, the Support agent's record with its unusable answers and the reporters who chose |
| `src/pages/Models.test.tsx` | The Models page: a Viewer reads the connections and the models per role with nothing to change; only what a validated provider listed is offered, the suggestion in its own group; a Manager chooses models, takes the suggestions and saves them; a suggestion the company's record decided says so with its numbers; a Manager connects (a refusal shown, the catalogue's base URL as placeholder, the key sent and cleared), validates and removes a provider |
| `src/viteConfig.test.ts` | Absolute asset base for the web build (deep links), relative only for Electron |
| `src/store/agentStore.test.ts` | The Glassroom connection asks for a stream ticket on every connection, opens without one when none is needed or none could be had, and opens nothing once disconnected; an event the backend replays on a reconnection is shown once |

### Manual validation (no test deps needed)

```bash
# Health check
curl http://localhost:8000/health

# Manual API test
curl -X POST http://localhost:8000/v1/init_company \
  -H "Content-Type: application/json" \
  -d '{"company_name": "Test Corp"}'

# Full API reference
open http://localhost:8000/docs
```

## Frontend i18n

All user-facing strings must be added to both:
- `frontend/src/i18n/en.json`
- `frontend/src/i18n/es.json`

Use the `useTranslation` hook from i18next; never hardcode English strings in JSX.

## LLM Proxy Model Routing

Every LLM call goes through the proxy client (`backend/app/llm_proxy_client.py`).
`LLMProxyClient.chat()` / `chat_async()` take a `model` id (default
`"gemini-pro"`), a `role`, and an optional `bu_id`. There is no
`model_preference` parameter: the `fast` / `powerful` tiers are an internal
fallback in the proxy (`_PREFERENCE_TIERS`). Role→model defaults live in
`backend/llm/model_factory.py` (`ModelFactory.ROLE_MODEL_MAP`); every caller
reads them through `default_model_for_role` → `ModelFactory.model_for_role`,
which applies, most specific first, `AGENT_MODEL_<ROLE>`, `NEMOTRON_ROLES`,
`AGENT_MODEL` (one model for the whole team, e.g. DeepSeek alone), then the
map. Above those sit the company's own choice and the agent's: an agent's
`model` (the Agents page) wins, then the company's model for the role
(`Company.role_models`), then its default (`Company.default_model`;
`services/company_models.py`, `GET`/`PUT /v1/companies/{id}/model_settings`,
Viewer / Manager). The ScrumMaster, the PO, the Architect, the task worker's
Developer, Architect consultation and QA review, and the Reviewer agent (its
own entry, else QA's) all read it; difficulty routing (`AGENT_MODEL_POLICY`)
refines only the deployment's default, never a model a person chose. A model
may be one of the company's provider connections (`conn:<id>:<model>`).
`GET /v1/companies/{id}/model_suggestions` (Viewer, `services/model_suggestions.py`)
suggests one per role by a fixed rule over the catalogue (the deciding roles: a
model that reasons and calls tools, largest context, then cheapest; Developer, QA
and the Reviewer: tools, largest context; TechWriter: the cheapest with tools),
only among what a validated provider listed, never applied on its own. Once
the company has run a candidate on at least three of its tasks in a role, more
done than not, its own record decides that role (`services/model_record.py`:
the model of each task's last delivery, done and rework from the boards of
completed sprints; least rework per task first), and the suggestion carries
the numbers (`measured`). The
company's **Models** page (`/company/models`, `pages/Models.tsx`,
`components/ModelConnections.tsx`, `components/ModelChoices.tsx`) is where a
Manager connects, validates and removes providers and picks the default and
per-role models with the suggestions beside them; a Viewer reads it.

Token budgets are enforced per company at the proxy layer — the backend never calls a model provider directly.

## Documentation

- [`README.md`](README.md) — Project overview, architecture, setup, and API reference
- Branches: trunk-based. Each change is a branch cut from `main` with one pull request into `main`, merged once the head is green (`CONTRIBUTING.md`, Branches). There is no `develop` integration branch.
- The Glassroom VR entry for the Meta VR Start Developer Competition lives on
  the `main_meta` branch (`vr/`, `docs/VR_ENTRY.md`); `main` is the web product.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — Development standards and workflows
- [`SECURITY.md`](SECURITY.md) — Security policy and vulnerability reporting
- [`docs/POSITIONING.md`](docs/POSITIONING.md) — Project direction and design rules
- [`docs/STANDARDS.md`](docs/STANDARDS.md) — The quality standards every project is held to, and which the loop checks without a model
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — The operations runbook: deploy, upgrade, key rotation, `/health` and `/metrics`, the daemon absent, a sprint stuck, a provider out of credit, backups and restore, retention, and how many workers may run
- [`bench/README.md`](bench/README.md) — The bench: reference specifications graded by person-written tests, scorecards, and how to add a specification

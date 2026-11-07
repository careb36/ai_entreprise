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
  `GET /v1/agent_runs/reviewer_record` counts how often its suggestions
  matched the decisions (unusable answers as misses) beside how often the
  Verifier's verdict alone would have (verified → approved, refuted →
  rejected), and those numbers go into its next prompt. Off by default: each
  run costs an LLM call.
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
  other machines, which can then ask the daemon to run commands.
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
  In a sprint planned from a specification it also decides the **acceptance
  tests per criterion** (`services/acceptance.py`): the runner's per-test lines
  are read back into criterion ids (pytest gets `-v`; name a verbose command
  here for another runner, e.g. `jest --verbose`, `vitest --reporter=verbose`).
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
- `DEVELOPER_TOOLS_ENABLED` (true), `DEVELOPER_TOOL_ROUNDS` (8) — the
  Developer's workspace tools (`services/developer_tools.py`): the roles that
  deliver files may list, read and search the project through the daemon, and
  run its tests with `DELIVERY_TESTS_ENABLED`, for that many rounds before
  delivering; every read is evidence.
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

**`backend/app/main.py`** — The FastAPI app: lifespan (workers, JetStream, schema backfills), middleware and the routers it includes. The routes live in `backend/app/routers/` (`sprint`, `agents`, `agent_runs`, `inbox`, `messages`, `procedures`, `support`, `workspace`, `github`, `auth`, `enterprise`).

**`backend/app/migrations.py`** / **`backend/alembic/`** — The schema's version. Alembic owns it: `alembic.ini` and `alembic/env.py` (the models are the target metadata; the URL is the backend's `POSTGRES_*`, or `ALEMBIC_DATABASE_URL`), `alembic/versions/0001_baseline.py` (the baseline: creates each table the models define and the database lacks, adds each column a table lacks, and repeats the old startup's `ALTER TABLE` fixes, indexes and backfills on Postgres, so an empty database, one from `db/init-db/init.sql` and one patched by an older backend all converge). `database.prepare_database` (startup) runs `require_schema_at_head`: a database behind head stops the start with both versions in the error; `DB_AUTO_UPGRADE=true` runs `upgrade_to_head` first. `schema_differences` is what `alembic check` reports (`include_object` leaves `vector_memory` and init.sql's other tables out). The container's `CMD` runs `alembic upgrade head` before uvicorn; the e2e runs `upgrade head` then `check` on Postgres; `backend-tests.yml` runs both on a SQLite file. How to write a migration: `CONTRIBUTING.md` §5.

**`backend/agents/graph.py`** — Optional LangGraph producer, disabled unless `AGENT_GRAPH_ENABLED=true`. Its runs are ingested as `AgentRun` records with `producer: "le-fabrique-graph"`.

**`backend/app/llm_proxy_client.py`** — Thin HTTP client wrapping the LLM proxy for token-budget-aware requests. `chat()` / `chat_async()` take a `model` id (default `"gemini-pro"`), a `role`, and an optional `bu_id`.

**`llm-proxy/app.py`** — Flask gateway that enforces per-company token budgets, routes by model-id prefix to the wired providers (Nebius Token Factory, Gemini, DeepSeek, Qwen), and refreshes each provider's catalogue hourly. Each answer says how its token count is known (`llm-proxy/usage.py`): `usage_status: reported` with the provider's figure in `usage`, or `estimated` with a characters/4 estimate apart in `usage_estimate` (charged to the quota, never shown as usage); the backend's `usage_status` keeps estimates out of the KPI averages.

**`vscode-daemon/daemon.py`** — The workspace daemon: a Python process that runs natively on the user's macOS, Linux or Windows machine (started by `scripts/start-daemon.*`, reconnects to NATS on its own). It answers `workspace.generate` (write files, reply with the sha256 read back from disk and the diff against the bytes that were there, `file_diff.py`; with a `git` spec, as a commit on the task's branch in a worktree, `git_workspace.py`), `workspace.execute` (run a command), `workspace.capabilities`, `workspace.status`, `workspace.browse` (folder picker) and `workspace.inspect` (canonical path, git remote/branch, file snapshot for stack detection), `workspace.hash` (the sha256 of named files as they are on disk, `file_hash.py`; the backend keeps QA's acceptance tests intact with it), `workspace.list` / `workspace.read` / `workspace.search` (the Developer's read-only tools on the project, bounded, `workspace_read.py`), `workspace.git.publish` (push a `fabrique/` task branch to `origin` with the person's credentials and open a draft pull or merge request with `gh` / `glab`, else the compare page), `workspace.git.reviews` (the comments people left on that request, read with `gh api` / `glab api`, `platform_reviews.py`), and consumes the JetStream queue `workspace.deliver` for files queued while it was offline, reporting on `workspace.delivered`. Its `requirements.txt` is `nats-py` only.

**`backend/app/routers/workspace.py`** / **`services/workspace_daemon.py`** — `GET /v1/workspace/{status,browse,inspect}` relay the picker and project check to the daemon (503 with start instructions when none is connected). `status` reports `state`: `connected`, `absent` (NATS says nothing listens) or `unknown` (NATS unreachable, no answer in time, or an error reply), with a `reason`; unknown is never shown as stopped. `POST /v1/discover_team` inspects the folder through the daemon and runs stack detection on its file snapshot (`materialized_snapshot`), recording the host path.

**`backend/app/services/review_changes.py`** — What a run changed, the way a code review shows it: `GET /v1/agent_runs/{id}/changes` reads the daemon's per-file diffs from the latest verification whose evidence carries them, or a pull request's diff (the artifact of a GitHub-ingested run), or, with neither, a diff the producer sent as its artifact (labelled `artifact`), with one `git diff` parser; the files the producer only declared come back apart (`declared_not_observed`), never as changes. The run page's **Files changed** tab (`components/ReviewChanges.tsx`, `lib/patch.ts`) renders it like a pull request.

**`backend/app/services/run_review.py`** / **`routers/reviews.py`** — Reviews as on a pull request: `ReviewComment` (a line of a changed file, new or old side, or the run) and `RunReview` (`comment`, `approve`, `request_changes`, with its `outcome`). A Manager writes them, a Viewer reads them. A submitted review carries the open comments; on a task-worker run it acts only where the loop waits for a person (`request_changes` sends a task `waits_for_a_person` back to `todo` with a send-back note and one `NEEDS WORK:` comment listing `path:line: comment`, which `board.pending_rework_feedback` hands to the Developer; `approve` accepts an `awaits_risk_acceptance` hold), and anything else is a 409 with the reason; on another producer's run it records an `AgentRunApproval` (`record_approval`, shared with `POST /approve`). Audited as `run_review_<event>`; never evidence. The Files changed tab's line threads and form are `components/LineComments.tsx`, the review form and history `components/ReviewPanel.tsx`.
**`backend/app/services/spec_check.py`** / **`spec_author.py`** / **`spec_planning.py`** — The specification (`models/specification.py`, `routers/specifications.py`): what the company's agents build from, as data. `spec_check.normalize` assigns the ids (`REQ-1`, `REQ-1.2`, `NFR-1`) in order, never a model or a request; `spec_check.check` is the deterministic gate: blocking (a requirement without a criterion, a criterion with an empty given/when/then, a quality target without a number, an unanswered question) and warnings (a promise without a number: "fast", "easy", "rápido"…). `spec_author.draft` asks the PO for a draft in a fixed JSON shape from a person's brief, with the questions it could not decide; a person answers (`POST /v1/specifications/{id}/questions/{i}`), asks for a revision (the answers go into the prompt) or edits, and only a Manager approves (`/approve`, 409 while the check blocks); an approved version is never edited, `/revise` opens a new draft that supersedes it. `spec_planning.plan` has the PO split an approved specification into tasks that name the criteria they cover (`POST /v1/plan_sprint` with `specification_id`; `uncovered` lists what no task covers; without a usable answer, one task per requirement), `POST /v1/start_sprint` keeps `specification_id` on the sprint and `covers` on each task (422 for an unknown id), the task prompt and QA's review prompt carry only the requirements the task covers (`requirements_context`), and `coverage` reads the boards: per criterion, done, blocked, planned or uncovered.

**`backend/app/services/arch_check.py`** / **`arch_author.py`** — The architecture (`models/architecture.py`, `routers/architectures.py`) of an approved specification, as data: modules (responsibility, place in the repository, `depends_on`, the requirements they `cover`), interfaces (kind, the module that provides it, a contract, consumers), decisions (ADRs) and conventions, plus the Architect's open questions. `arch_check.normalize` assigns the ids (`MOD-1`, `IF-1`, `ADR-1`) and maps a draft's names onto them; `arch_check.check` blocks approval on a module without a responsibility, a reference to a module or requirement that does not exist, a dependency cycle (`find_cycle`), a decision without its decision or an unanswered question, and warns on a requirement no module covers, a module without a path, an interface without a contract, no decision. `arch_author.draft` asks the Architect, from the specification, the detected stack and the project's root files, in a fixed JSON shape, or revises from the answers; only a Manager approves (`/approve`, 409 while the check blocks), `/revise` opens the next version. Once approved, the PO's planning prompt carries it (`planning_context`), each task's prompt and QA's review carry the modules its requirements map to with their interfaces, and the decisions and conventions every task follows (`context_for`, appended by `spec_planning.requirements_for`), and `POST /v1/architectures/{id}/write` sends it as `docs/ARCHITECTURE.md` through the daemon (`workspace.generate`), keeping the sha256 the daemon read back (`written_sha256`) as an observation; `GET .../document` is the same Markdown.

**`backend/app/services/dependencies.py`** — Dependencies between a sprint's tasks: `BacklogItem.depends_on` holds the ids of the tasks that must be done first. The PO's plan names them by title (`spec_planning._validate_tasks` maps them to positions and breaks a cycle at the edge that closes it), `POST /v1/start_sprint` takes each task's `depends_on` as positions in `backlog_items` (`check_positions`: an unknown position, self or a cycle is a 422), keeps the ids and queues only the tasks that can start, with a `Waits for task …` note on the rest. The worker follows the order instead of the queue's: `sprint_control.dispatch_todo` and `_dispatch_todo_items` queue a `todo` task only when `is_ready` (every dependency done), `_on_task_assign` asks the same before touching a task that arrived early, and each review cycle `settle_active` blocks a `todo` task waiting on one a person must act on (`Blocked: waits for task …, which waits for a person.`, an explicit block reason, so the sprint closes around it and the inbox shows what the decision unblocks) and releases a task so blocked once its dependencies are done, or back in work (`Released: …`). The hold is the System's last note on the task (a comment anyone appends does not hide it); a chain of holds follows its root, so a task sent back to `todo` behind a held one is held again while the root is stuck; `sprint_settled` reconciles the holds before a sprint counts as settled, so it never closes over a task that could run. With the Git workflow, `git_workflow.delivery_spec` names the branches of the done tasks a task depends on (`merge`) and the daemon merges them into the task's worktree before writing (`git_workspace.merge_branches`, `merged` in its report; a conflict is an error). A carried task keeps a dependency carried with it and drops the rest. The board lists `depends_on` and `waiting_for` per task; `history_check.py` refuses a task that delivered before every task it depends on had closed, comparing its first deliverable with the dependency's last approval or person's note (`worked_before_dependency`).

**`backend/app/services/git_workflow.py`** — The company's Git workflow (`Company.git_workflow`, `git_base_branch`; `GET|PUT /v1/companies/{id}/git`, Viewer/Manager): the task worker asks the daemon to deliver on `fabrique/task-<id>` (`delivery_spec`), the daemon commits in a worktree under `<git dir>/fabrique-worktrees/` and answers with the branch, commit and parent (`vscode-daemon/git_workspace.py`, real git, no shell), and `record_reply` keeps that on the evidence as `git` and points the delivery tests at the worktree. The person's checkout is never touched; merging is a person's call or the platform's. `services/git_publish.py` publishes a run's branch (`POST /v1/agent_runs/{id}/publish`, Manager; `GET .../publications`, Viewer): the request's description is the evidence the Verifier observed, the producer's claims labelled apart, with a `Fabrique-Run:` trailer; the daemon's answer is kept as a `RunPublication` and audited. The Files changed tab offers it (`components/PublishBranch.tsx`). `services/review_sync.py` brings the request's comments back (`POST /v1/agent_runs/{id}/publications/{pid}/sync`, Manager): each becomes a `ReviewComment` with its `source`, `external_id`, page and, for a review, its state there; reading again updates one still waiting and never duplicates; the next review a Manager submits carries them, and its `NEEDS WORK:` names who wrote each and on which platform. A review submitted on the platform decides nothing here.

**`backend/app/services/standards.py`** — The quality standards every project is held to: a fixed catalogue by discipline (`STD-ARCH-1` … `STD-PO-1`, `docs/STANDARDS.md`) that no model, request or procedure edits. `prompt_section(role)` goes into every prompt by role (the task prompt before the company's procedures, QA's review, the PO's draft and plan, the Architect's design); a procedure adds to the standards, never relaxes them. `check_delivery` is the Verifier's `standards` check over the files a deliverable declares: a secret-looking value (keys, tokens, private keys, a password assigned in code; placeholders and `os.environ` references are not) refutes the delivery and the gate sends it back (`standards violated`); code without a test, a `TODO`/`FIXME` left behind and an unpinned dependency (`requirements*.txt`, `package.json`) are findings the reviewer sees. `missing_quality_areas` gives the specification check its `quality_area_missing` warnings. `GET /v1/standards` serves the catalogue; `tests/test_standards.py` keeps the document in step with it.

**`backend/app/services/board.py`** — The pure readers of a task's comment thread, shared by the task worker, `routers/inbox.py` and `services/carry_over.py`: which comment is the deliverable to review, the pending `NEEDS WORK:` requests, whether a blocked task waits for a person (`blocked_item_next_step`, `item_is_settled`) and its `block_reason`. No engine, no I/O; the gate author names (`Quality gate`, `Verifier`) live here too. The advice a task got (the worker's console-advice note, the Architect's `ARCHITECT_CONSULTED:` brief) is never a verdict, and the phrases that keep a task blocked count only from the system, a gate or a person (`BLOCK_VERDICT_AUTHORS`), never from a deliverable.

**`backend/app/services/sprint_control.py`** / **`routers/sprint_control.py`** — Who decides when a sprint's agents work. A sprint runs only while `active` (or `needs_split`); `planned` (Sprint 0 left unstarted by the New company wizard, `start_now: false` on `POST /v1/init_company`, always at autonomy 1), `paused` and `cancelled` are halted: the worker neither dispatches, reviews nor retries their tasks (`is_halted`). `POST /v1/sprints/{id}/start` starts a planned sprint, `/pause` and `/resume` pause it (resume dispatches its waiting tasks), `/stop` cancels it for good (unfinished tasks blocked with a note, no follow-up), `/dispatch` re-queues `todo` tasks of a running sprint; all Manager. When a provider refuses a call for lack of credit (402, or a 429 that names a quota or balance; `credit_failure`), the worker pauses the sprint with `pause_reason` and puts the task back to `todo` instead of blocking it, so resuming runs it again and a sprint of failures cannot loop into follow-ups; a company with a planned or paused sprint gets no follow-up on its own. `GET`/`PUT /v1/companies/{id}/autonomy` reads (Viewer) or changes (Manager) the autonomy level: at 1 every task QA approves waits for a person like a high-risk delivery (`HUMAN_LED_REASON` in the hold note), at 2 only high-risk ones, at 3 follow-ups and procedures also run on the company's own. The Sprint page shows a banner on every tab for a planned or paused sprint (Start / Resume, the reason) and the Board tab's controls (`components/SprintControls.tsx`): Dispatch, Pause, Stop, Autonomy.
**`backend/app/services/console_assistants.py`** — The terminal assistants the daemon can run (GitHub Copilot CLI, Claude CLI): which ones to ask for a task (`select_assistants`), the command with the prompt shell-quoted, `collect_advice` through `workspace.execute`, and the per-company cooldown persisted in `AssistantRuntimeState` when one is out of quota or missing on the host. Their advice is prompt context and a board note, never evidence; `GET /v1/sprints` counts tasks with and without it (`console_advice`: done and rework per group), so the Sprints page shows whether it helps. The Architect's briefs are counted the same way (`architect_advice`, rework from the first brief on), and `services/architect.py` tells the Architect, when it is asked again, how the company's briefed tasks went in its completed sprints against the rest. `services/glassroom.py` holds `publish_glassroom`, the best-effort publisher every worker uses for the sprint's SSE stream.

**`backend/app/services/mcp_tools.py`** / **`mcp_config.py`** / **`mcp_client.py`** / **`routers/mcp.py`** — MCP (Model Context Protocol) tools for the task worker's producers. A Manager configures a company's servers with the standard `mcpServers` map (`POST /v1/mcp/servers`: `command`/`args` for stdio, `url` for streamable HTTP or SSE, `env`/`headers` as secrets, optional `roles`); `McpServer` rows keep the secrets Fernet-encrypted and the API lists only their names (`GET /v1/mcp/servers`, Viewer; `PATCH`/`DELETE`, Manager; `GET /v1/mcp/servers/{id}/tools` connects now and lists what the server advertises). The worker loads the role's `Toolbox` (`mcp_tools.Toolbox.load`), offers the tools to the model as `<server>__<tool>` in the OpenAI `tools` shape (the proxy passes them to every provider; `llm-proxy/gemini.py` translates them for Gemini) and runs the bounded loop (`chat_with_tools`): at most `MCP_MAX_TOOL_ROUNDS` rounds, then one request without tools, so a task always ends in a deliverable. Each call is an observation the backend made (server, tool, arguments, error, duration, sha256 of the result) kept on the Verifier's evidence as `tool_calls` and shown as the `tools_used` check; the model's reading of a result is a claim. Results are masked for the server's own secrets and cut at `MCP_RESULT_MAX_CHARS`; a tool without a read-only annotation raises the delivery's risk tier to at least `medium` (`risk.raise_for_tool_calls`). A server that cannot be reached is named in the toolbox's `unavailable` and the task goes on without it. stdio servers run inside the backend container, so their runtime (`npx`, `uvx`, …) must be in that image; remote servers need only a URL. Built on the official `mcp` package.

**`backend/app/services/research_tools.py`** / **`research_registries.py`** / **`research_schemas.py`** — The research tools, offered in the same loop to the roles that decide (`RESEARCH_ROLES`; `offered_to`): `research__fetch` (one page as text, `html_to_text`, from a host on the list: the defaults `DEFAULT_HOSTS`, `RESEARCH_ALLOWED_HOSTS` and `Company.research_hosts`, `allowed_hosts`; https only unless the host was named explicitly; a redirect is followed only while it stays allowed; no credential; cut at `RESEARCH_FETCH_MAX_BYTES`), `research__package` (`research_registries.package_record`: PyPI, npm, crates.io, the Go proxy) and `research__advisories` (OSV, `advisory_rows`). `ResearchToolbox` has the `mcp_tools.Toolbox` shape; `toolbox_for` builds it for a task (the worker's `developer_tools.toolbox_for` combines it with the workspace and MCP toolboxes, `CombinedToolbox` taking any built-in boxes; `research` is a reserved server name) and `toolbox_for_request` for a draft (`spec_author.ask_with_tools` runs `chat_with_tools`; `prepare` puts the company's hosts and the `## Sources` prompt section, `prompt_section`, into the request). Every call is an observation (`url`, `host`, `allowed`, `fetched_at`, `status`, `sha256`, `bytes`, package and ecosystem); `sources_of` keeps those fields on `Specification.sources` and `Architecture.sources`, and `check_sources` is the Verifier's `sources` check (the sources, the refusals, and the dependencies the delivery's manifests name without a registry lookup, `packages_in`), present when tools were offered. `GET`/`PUT /v1/companies/{id}/research` (`routers/sprint_control.py`, Viewer / Manager) read and replace the company's hosts (`valid_host`). The frontend shows a draft's sources (`components/SourcesConsulted.tsx`). A web search is an MCP server the company configures.

**`backend/app/services/developer_tools.py`** — The Developer's workspace tools, offered beside the MCP tools in the same loop to the roles that deliver files (`TOOL_ROLES`: Developer, QA) when the company's project path is known: `workspace__list`, `workspace__read`, `workspace__search` (the daemon's `workspace.list` / `read` / `search`, `vscode-daemon/workspace_read.py`, bounded and never outside the project) and `workspace__run_tests` (`workspace.execute` with the project's detected test command, or a narrower one that starts with the same runner; offered only with `DELIVERY_TESTS_ENABLED`). `toolbox_for` replaces `mcp_tools.Toolbox.load` in the worker and returns a `CombinedToolbox` that dispatches by the built-in tools' exact names (the server name `workspace` is reserved in `mcp_config`); `tool_root` points the tools at the task's worktree once it delivered there (Git workflow), else the project; `bind` gives the module the worker's NATS client and loop, since the LLM call runs in a thread (`_request_from_thread`). `repository_section` puts the top two levels of the tree into the task prompt (`## Repository`). Each call is an observation like an MCP call (`read_only: true`); a read also carries the file's `path`, `sha256` and `bytes` as the daemon read them, and `services/context_read.py` holds the evidence side: `check_context_read` is the Verifier's `context_read` check (which of the files the delivery modified, the daemon's `change == "modified"`, were read first and which were not; `workspace_tools_used` says the tools were in use at all, a listing or a failed read included), `redact_secrets` keeps a secret-looking line (`standards.secrets_in`) out of what the model is shown; `risk.raise_for_unread_changes` raises the tier to `medium` for a modified file never read while the tools were in use. The tool schemas live in `services/developer_tool_schemas.py`. The daemon never opens a file that may hold secrets (`workspace_read.SENSITIVE_NAMES`, `SENSITIVE_DIRS`) or a symlink that leads out of the project, hashes a file in chunks and indexes only its first `READ_SCAN_BYTES`, and runs a search in a child process killed at `SEARCH_TIMEOUT_SECONDS`. Settings: `DEVELOPER_TOOLS_ENABLED`, `DEVELOPER_TOOL_ROUNDS` (forwarded by `docker-compose.yml`).

**`backend/app/nats_client.py`** — `enable_jetstream()` declares the work-queue streams in `DURABLE_STREAMS` (`task.assign`, `workspace.deliver`, `workspace.delivered`); `publish()` stores durable subjects with a `Nats-Msg-Id`, `consume()` is a durable pull consumer with explicit acks. Without JetStream both fall back to core NATS with a warning; `/health` reports `jetstream`.

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — sprint task tracking with drag-and-drop (`@dnd-kit`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat

**`backend/janitor/main.py`** — Background maintenance container (runs daily). Cleans outbox events and rebuilds pgvector indices with `REINDEX CONCURRENTLY`.

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
3. `parse_file_manifest` turns the answer into files. The worker sends them to the
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
| `test_git_publish.py` | Real git against a bare remote in `tmp_path`: the task branch is pushed and the answer is the compare page without `gh`, a fake `gh` opens a draft with the title and the evidence as body, a failing `gh` still leaves the compare page, a non-`fabrique/` branch, an undelivered one or no origin is refused, compare pages per platform; a Manager publishes with the evidence in the body and the claims apart, a Viewer reads the publications, 409 without a branch, 503 without a daemon, 404 from another company |
| `test_specifications.py` | The specification: ids assigned in order by the check, never by a draft; what blocks approval (no criterion, an empty step, a target without a number, an open question) and what only warns (a promise without a number, in English and Spanish); the PO's draft read from the fixed shape and nothing else, a revision prompt with the answers; a Manager writes, a Viewer reads, outsiders refused; the PO drafts, asks, a person answers, the revision passes the check and is approved; an unusable answer leaves the draft as it was; a revision supersedes the approved version on approval; the PO plans tasks with `covers` (unknown ids dropped, the uncovered listed, a draft refused, one task per requirement without a model); a sprint keeps what each task covers and refuses an unknown id; coverage per criterion from the boards; the prompts carry only the covered requirements; a carried task keeps its `covers` |
| `test_architectures.py` | The architecture: ids assigned in order and a draft's names mapped onto them; what blocks approval (an unknown module or requirement, a cycle named along its path, a decision without its decision, an open question) and what warns (an uncovered requirement, no path, no contract, no overview); the prompt section carries a task's modules and their dependencies, never the rest, plus every decision and convention; the Markdown document; the Architect's draft read from the fixed shape, the prompt with the specification, the stack and the answers; a Manager writes, a Viewer reads, outsiders refused; the Architect drafts from an approved specification only, asks, a person answers, the revision passes and is approved; a revision supersedes the approved version and the prompts follow it; without an approved architecture the prompts carry the requirements alone; writing to the repository keeps the daemon's hash, refuses a draft, reports a rejected write and a missing daemon |
| `test_acceptance.py` | An acceptance test per criterion: criterion ids as test-name tokens, the per-test lines of pytest, jest, go and cargo read back per criterion, pytest made verbose; the plan puts QA's test task per requirement first and re-points dependencies within the scope; through the real daemon, QA's failing tests are expected and every criterion must run, an implementation that leaves its criterion failing is refuted, the one that makes it pass verifies the criteria on the specification (with REQ-2's tests still failing, excused) and in the publication line, an implementation that edits an acceptance test, or a test changed behind the loop's back, is refuted and the test restored from QA's delivery before the run, nothing changes without a specification; the daemon hashes files as they are on disk; the history check refuses a criterion verified without an observed run |
| `test_standards.py` | The quality standards: a sound catalogue that binds every role, the prompt section with what is checked, the quality areas a specification forgets, secrets found (keys, private keys, passwords in code) and placeholders left alone, TODOs and unpinned dependencies as findings, the check over a delivery, the Verifier refuting a leaked secret and the gate sending it back, the catalogue served and documented |
| `test_dependencies.py` | Positions checked (unknown, self, a cycle named along its path), edges that close a cycle dropped one at a time; the PO names dependencies by title and the plan carries positions, a cyclic answer broken; a sprint keeps them, queues only the tasks that can start and the board shows `depends_on` and `waiting_for`; dispatch and the worker's gate queue a task once its dependencies are done; a task waiting on one left for a person is blocked with the reason, transitively, settles the sprint, is not blocked by one the worker still retries, and is released when the dependency gets done or is back in work; a comment anyone appends does not hide the hold and a send-back behind a stuck root is held again; a sprint reconciles its holds before it counts as settled; a closed sprint is left alone; a carried task keeps a dependency carried with it; a title longer than shown still names a dependency; the history check refuses a task that delivered before its dependency closed |
| `test_review_sync.py` | Fake `gh` and `glab`: line comments (an outdated one keeps its line), reviews (pending and empty ones left out) and conversation from GitHub across pages, GitLab notes without system ones, a missing or failing CLI and a page that is not a request refused with the reason; a Manager brings them once (a Viewer cannot), reading again adds nothing and an edit there updates a comment still waiting, one a review carried keeps what it said, the feedback names who wrote it and where, a compare page or another company's publication refused |
| `test_git_workflow.py` | Real git in `tmp_path`: a delivery is a commit on `fabrique/task-<id>` in a worktree with the task's trailers and La Fabrique as author, the person's checkout, index and branch untouched; another attempt is another commit, the same bytes none; a named base starts the branch there; a bad branch name or a folder that is not a repository is an error the backend reads; the spec only with the workflow on; a task delivered with it is verified with the commit on its evidence; Viewer reads and Manager sets the workflow; the changes name the branch and commit |
| `test_review_changes.py` | The daemon's diff of each write (added, modified, unchanged, binary, too large, the reply's cap), one parser for its diffs and a pull request's, `GET /v1/agent_runs/{id}/changes` from the daemon's observation or the pull request, a delivery from before diffs, declared files kept apart, company scoping |
| `test_delivery_tests.py` | Opt-in post-delivery test run: command detection, `tests_run` check, passing/failing suites through the real daemon |
| `test_review_gate.py` | Review loop: delivery gate before QA, explicit `APPROVED:` verdict, rework limit, company-scoped evidence |
| `test_board.py` | Reading a task's comment thread: carry-over notes and the console-advice note are never a verdict (an assistant's "retry" or "manual triage" moves nothing), pending rework survives gate verdicts and send-backs, rework budgets restart after a send-back, only the worker's note marks a task as advised and its rework counts from that note on; the Architect's brief is never a verdict either, a deliverable's words are not a block reason, and a briefed task's rework counts from the brief on |
| `test_risk_tier.py` | The risk tier: each area a path marks, look-alike words left low, the highest path decides, a producer raises but never lowers it, the Verifier's check never decides the verdict; an approved high-risk task waits for a person at autonomy 3, settles the sprint, is listed as `risk_hold`, is not a failure in the procedures' numbers and is not carried over on the company's own |
| `test_history_check.py` | The independent checker of task histories (`app/history_check.py`): done needs an approval or a person, an approval needs a deliverable before it and a verified latest run (unless a person closed it), a high-risk hold closes only through a person, a task is carried over once, a ScrumMaster decision answers an escalation of its own task; the database is read the same way, and the checker imports none of the production readers |
| `test_sprint_close.py` | A sprint closes when every task is settled (blocked for a person counts; retries, re-reviews and freezes keep it open); the pre-review quality gate asks for rework with the reason, then blocks and escalates |
| `test_inbox.py` | `GET /v1/inbox`: tasks blocked for a person (frozen included, retries excluded), proposals waiting, undecided runs, company scoping |
| `test_carry_over.py` | A blocked task carried into the next sprint: `carry_over_ids` on `start_sprint`, the reason in the new task and its prompt, refusals (404/409, scope), the original leaves the inbox, the autonomy-3 follow-up carries once |
| `test_scrum_master.py` | The ScrumMaster's triage of an escalation: the prompt, the fixed answer format, the decision recorded and read back, nothing recorded without a usable answer, the brief in the next attempt, a task left for a person not carried on the company's own, the second-attempt track record in the prompt, a send-back during the triage window makes the answer stale, a decision names the escalation it answers and is judged by it, `END` with a period or Windows line endings |
| `test_migrations.py` | The schema's version: the migrations build the models' schema from nothing and `alembic check` finds no difference; a database from the old startup (tables present, columns and tables missing) is converged, not rebuilt, and a second run changes nothing; the backend refuses a database that is not at head naming both versions; `DB_AUTO_UPGRADE` runs the migrations at startup; one head |
| `test_database_timezone.py` | The Postgres session is pinned to UTC, so aware datetimes in `timestamp` columns and the naive UTC text in board comments stay comparable |
| `test_backlog_comments.py` | In-place appends to `BacklogItem.comments` are persisted |
| `test_kickoff_failure.py` | A failed graph kickoff leaves the sprint active and its tasks running |
| `test_nats_connect_retry.py` | NATS connect backoff; workers start once connected |
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
| `test_llm_client.py` | The proxy client: `max_tokens` travels only when set, the default wait follows the proxy's upstream budget, an error the proxy answered is not sent again (no status is retried), a request that never reached the proxy is |
| `test_token_diet.py` | What the worker stops asking twice: a Developer answer cut at the length limit is sent back as such instead of "the complete result again", a formless QA answer is asked again with the format named, the fixed-form answers are capped and the deliverable is not, every wait follows the proxy's budget |
| `test_review_digest.py` | What QA is shown of a delivery: every file listed (lines, bytes) and headed `# Artifact:`, an excerpt of each within `DIGEST_CHARS` saying how many lines it left out, where the old 3000-character cut showed one file of three; text without files goes as it was; the Developer prompt runs from stable sections (output format, procedures, lessons) to variable ones (Architect brief, review feedback) so the providers' prefix cache applies |
| `test_support_access.py` | Support admins versus reporters on tickets and logs, `support_admin` in `/v1/auth/me`, 409 when re-inviting a member; an admin's severity and category decision stored apart, credited to whoever changed it and clearable, unknown values refused, `/v1/support/agent_record` admin-only; the reporter's values normalized and no severity or category made up; the Impact filter reads the decision first, else the reporter's choice; `resolved_at` is when the ticket was resolved, kept by later saves and cleared on reopening |
| `test_agent_access.py` | A company's agents and token usage: 401 without a token, 403 outside the company, a Viewer reads but cannot create, change or delete, a Manager can |
| `test_cross_company_access.py` | Only a Manager sets where a company's agents write (`discover_team`), `/v1/generate` bills only the company's own business unit, a sprint's tokens are its own company's |
| `test_glassroom_access.py` | The Glassroom stream with authentication required: a sprint-scoped ticket for a Viewer, refused without one, with an access token, another sprint's or an expired one, and once its holder left the company |
| `test_workspace_host.py` | Daemon folder browsing, git remote/branch inspection, snapshots, Windows shell/roots; `/v1/workspace/*` relay and discovery through the daemon |
| `test_audit_chain.py` | The audit hash chain per company: numbered and linked rows, each company its own chain, a deleted, altered or moved row breaks it where it was, a chain rewritten without the key does not verify, unkeyed chains say so, rows without a company stay unchained, KPI turn rows are signed and chained too |
| `test_sandbox.py` | Sandboxed execution: container mode through a fake runtime (worktree mounted, network off, limits, capabilities dropped, only the listed variables, the daemon's report), a mode that cannot run is an error and never a host run, host reports itself unisolated and the daemon can force a container, a hung sandboxed command is killed, limits are bounded, the company's mode in the delivery test run and the Developer's test runs, the evidence keeps the daemon's report and the Verifier records it (host and an older daemon as findings), a Manager sets the company's sandbox |
| `test_research_tools.py` | The research tools: a page read as text and recorded with its hash, a host off the list refused and the refusal recorded (a redirect that leaves the list too; plain http only for a host named explicitly), an error page and a binary as errors the model sees, a package looked up at each registry, advisories from OSV with the version asked, the tools for the roles that decide and the `## Sources` prompt section, `research` a reserved MCP name, the combined toolbox without a workspace root, the Verifier's `sources` check naming the dependencies nobody looked up, a Manager adds hosts and a Viewer reads them, the PO's draft keeps the source it consulted |
| `test_developer_tools.py` | The Developer's workspace tools: the daemon lists the tree without dependencies or VCS data, reads a file numbered with its hash and tells a binary apart, cuts a long file and says how long it is, searches and refuses a bad pattern, answers over NATS in the reply shape; the toolbox offers the read tools and the test run only with a command, records a read with the file's hash, turns errors into results, runs the project's command or a narrower one on the same runner (shell syntax falls back), the combined toolbox dispatches by prefix and caps the rounds, the tools go to the roles that deliver files when the project is known, the prompt carries the repository map; the Verifier says which modified files were read first and the tier follows; the worker offers the tools, reads through the daemon and keeps the reads as evidence; the history check refuses a read recorded without a hash; a narrower test command must be the same runner exactly, files that may hold secrets are never read nor searched, a secret-looking line is redacted, a symlink out of the project is never opened, a large file is hashed whole but only its head read, a search that backtracks forever is stopped at the deadline and one over more files than the bound still scans the bound, a test run with no sprint is reported to nobody, the tools look at the task's worktree once it delivered there |
| `test_mcp_tools.py` | MCP tools in a task, against a real MCP server in-process: discovery in the OpenAI `tools` shape with prefixed names, the loop runs the calls and records them (arguments, error, sha256, read-only), results masked for the server's secrets and cut with the record saying so, a failing, unknown or malformed call is an error result, the round cap ends in a request without tools, an unreachable server is named and the task goes on, the Verifier records the calls as `tools_used` and a writing tool raises the tier, the loader takes the company's enabled servers for the role, and the worker keeps the calls as evidence apart from the claims |
| `test_mcp_config.py` | The `mcpServers` map: stdio by `command`, http by `url`, `type`/`transport` aliases, unknown keys dropped, each refusal with its reason; secrets encrypted at rest and listed by name only, a row the key cannot read still lists |
| `test_mcp_router.py` | `/v1/mcp/servers`: a Manager adds (by name, replacing), disables, restricts and removes servers, a Viewer reads, an outsider gets 403 and no token 401; no answer carries a secret; the tools endpoint reports what the server advertises or why it could not be reached |
| `test_work_control.py` | Sprint 0 starts only when asked and never at autonomy 1; a planned sprint starts once and dispatches its tasks; pause, resume and stop (Manager only; stop blocks unfinished tasks); the autonomy level read by a Viewer and changed by a Manager; a planned, paused or cancelled sprint's task is not loaded, dispatched or reviewed; a credit answer is told from a passing error; no credit pauses the sprint and the task waits as todo; the execute call names the provider without credit; at autonomy 1 every approved task waits for a person |
| `test_tenant_scoping.py` | Every table carries a required `company_id` that references `companies`, or is listed with its reason (the tenant, users, help-desk tickets, the outbox, backlog items through their sprint); `audit_log` alone may hold rows without a company; the lists hold no stale names |
| `test_metrics.py` | `/metrics` labels requests by route template, a fixed route by its path, a CORS preflight as `preflight` and a path no route answered as `unmatched`; memory search caps its `limit` |

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
carries that record. Then a task whose file marks authentication is approved
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
At the end, `app/history_check.py` reads every task's history from the run's
database, sharing no code with the readers it checks, and the run fails on
an illegal one.
The fake model also checks the loop's order from its side: a QA review of a
task must follow a delivery of it, one review per delivery, and show QA the
files that delivery wrote; a violation fails the run.
Run it with the backend's interpreter (`E2E_PYTHON` pointing at the venv's
`python`, `$(cd backend && poetry env info --path)/bin/python`); it exits 0
only when every check passed and leaves the logs in `E2E_WORKDIR`. CI runs it
too (`.github/workflows/e2e.yml`) on every pull request that touches
`backend/`, `vscode-daemon/`, `scripts/e2e/` or the workflow itself, and on
pushes to `main`, against a `pgvector/pgvector:pg15` service and
a NATS container started with JetStream; the logs are kept as the `e2e-logs`
artifact.

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
and `npm run build` from the `frontend/` directory, using
`frontend/package-lock.json` as the cache key.
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
| `src/pages/AgentSettings.test.tsx` | Model selector lists `/v1/models` entries after a "Default for role" option; a Viewer reads the agents with nothing to change them, a Manager changes them |
| `src/i18n/coverage.test.ts` | Every statically referenced translation key exists in both `en.json` and `es.json` |
| `src/App.test.tsx` | Navigation order, capability-gated links, redirects, language switcher |
| `src/pages/Sprints.test.tsx` / `src/hooks/useSprints.test.tsx` | Sprint list page and its `GET /v1/sprints` hook |
| `src/components/LearningTrend.test.tsx` | The "Is the company learning?" tiles: rework per task and blocked share per closed sprint, the change since the previous one, the sparkline's values and the table view; second attempts that got done, with and without the ScrumMaster's brief; tasks with console advice, and tasks the Architect briefed, against the rest |
| `src/components/SprintControls.test.tsx` | Start for a planned sprint, the reason and Resume for a paused one, nothing while it runs; Stop only after confirming, no Pause or Stop on a finished sprint; a Manager changes the autonomy level and a Viewer only reads it; the wizard's start-now choice, disabled at level 1 |
| `src/pages/SprintWorkspace.test.tsx` | Sprint page tabs, URL sprint validation, Pause/Resume |
| `src/pages/TaskDetails.test.tsx` | Task page errors and delivery evidence |
| `src/pages/Procedures.test.tsx` | Proposals, procedures in force and history with their numbers; Manager adopt and write; load error retry |
| `src/components/SourcesConsulted.test.tsx` | The sources an agent consulted while drafting: a fetched page linked with its status and hash, a package lookup, a refused host marked and not linked; nothing without sources |
| `src/components/Conversation.test.tsx` | Sprint and task conversation: who spoke to whom, kind filter, long messages, empty and error states |
| `src/components/GovernancePanel.test.tsx` | The Review inbox: blocked tasks with their reason and a Manager's "Send back to work", "Accept" on an approved high-risk delivery only, proposals linked to Procedures, undecided runs with the Reviewer agent's suggestion beside each and its record against the decisions, how earlier second attempts went beside the blocked tasks, Viewer and error states |
| `src/pages/EvidenceDetail.test.tsx` | Run page: claims beside observations, audit rows, the review form's validation and body, task links; the Reviewer agent's suggestion shown as a claim beside the review form, and when it could not suggest; the Files changed tab with its count |
| `src/components/ReviewChanges.test.tsx` | Files changed as a pull request shows them: each line with its old and new number, the file list, Viewed folds a file, why a file has no diff, declared files kept apart, a long file shown in part, the source named, retry; a Manager comments on a line (a deleted line on its old side) and resolves, a Viewer gets no comment button |
| `src/components/PublishBranch.test.tsx` | A Manager publishes the branch, and brings back the request's comments (not for a compare page or a Viewer), with what came; the comments from the request on no line shown are listed with a link to the platform and their state; the request it opened is linked for everyone; a remote without a web page still says it was pushed; nothing for a Viewer before a publication; the daemon's refusal shown |
| `src/components/ReviewPanel.test.tsx` | Submitting a review (the decision picked, the text trimmed, how many line comments go with it), the backend's refusal shown, what a review did, the history, a Viewer reads only |
| `src/lib/patch.test.ts` | A file's hunks as review rows with their old and new line numbers |
| `src/pages/HelpDesk.test.tsx` | Help desk for support admins: filters, resolution required to close, logs download, the agent's suggestions beside the reporter's choice, an admin's decision on impact and category (only a decision changed in the form is sent, one put back as the ticket stores it is not, an empty one clears it, one another admin made meanwhile is kept, a ticket another admin resolved meanwhile is not reopened, and after a save the form follows what is stored), the list showing the decided impact or the reporter's marked as theirs, the Support agent's record with its unusable answers and the reporters who chose |
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
`backend/llm/model_factory.py` (`ModelFactory.ROLE_MODEL_MAP`).

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

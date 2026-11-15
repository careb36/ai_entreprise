# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**La Fabrique** is an AI-powered autonomous software development platform. It orchestrates a multi-agent team (PO, Architect, ScrumMaster, Developer, QA, SecOps, TechWriter, and the specialised BackendDeveloper, FrontendDeveloper, DevOps, Designer and DataEngineer; `services/contracts/roles.py`) using LangGraph to execute software sprints — from backlog to code generation and execution — inside a user's VS Code workspace.

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

# The knowledge graph (Neo4j) is optional and on by default; without it:
NEO4J_ENABLED=false docker compose up -d --scale neo4j=0

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

**Local git hooks** (`.pre-commit-config.yaml`): `pip install pre-commit && pre-commit install --hook-type pre-commit --hook-type post-merge --hook-type post-rewrite`. Before a commit, ruff and the `.env` check run; after a pull, merge or rebase that changed `frontend/package-lock.json`, `scripts/hooks/sync-frontend-deps.sh` runs `npm ci` in `frontend/` (it never fails the git command, and says so when npm is missing or the install failed).

**Frontend production build:**
```bash
cd frontend && npm run build
```


Every environment variable is in [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

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

## Contexts

`backend/app/services` is divided into fourteen contexts, each a folder with one responsibility. **Read the context's document before changing code in that context.** This file is an index; the detail lives in the documents it links.

| Context | Folder | What it owns | May import | Document |
|---|---|---|---|---|
| contracts | `services/contracts/` | The shapes several contexts exchange, as plain data and pure functions | nothing | [`docs/contexts/contracts.md`](docs/contexts/contracts.md) |
| platform | `services/platform/` | The infrastructure every context may use: audit, locks, Glassroom, tokens, health, the knowledge graph reader | contracts | [`docs/contexts/platform.md`](docs/contexts/platform.md) |
| tasks | `services/tasks/` | A task's record: events and their hash chain, projections, the board readers, dependencies | contracts, platform | [`docs/contexts/tasks.md`](docs/contexts/tasks.md) |
| models | `services/models/` | Which model a role runs and what it costs | contracts, platform, tasks | [`docs/contexts/models.md`](docs/contexts/models.md) |
| evidence | `services/evidence/` | Judging a run from what was observed: the Verifier, risk tier, standards, acceptance tests, the gate before QA | contracts, platform | [`docs/contexts/evidence.md`](docs/contexts/evidence.md) |
| workspace | `services/workspace/` | The client of the workspace daemon over NATS (and, here, the daemon itself) | contracts, platform | [`docs/contexts/workspace.md`](docs/contexts/workspace.md) |
| tools | `services/tools/` | What a model may call during a task: the tool loop, MCP, workspace tools, edits, repository map, research | contracts, platform, evidence, workspace | [`docs/contexts/tools.md`](docs/contexts/tools.md) |
| learning | `services/learning/` | How the company learns from its sprints: procedures, retrospective, triage, the Architect's record | contracts, platform, tasks, models | [`docs/contexts/learning.md`](docs/contexts/learning.md) |
| planning | `services/planning/` | What is built and in which tasks: specification, architecture, the PO's plan, the sprint's team | contracts, platform, tasks, models, evidence, workspace, tools | [`docs/contexts/planning.md`](docs/contexts/planning.md) |
| producers | `services/producers/` | The interchangeable producers other than the task worker's own model call | contracts, platform, tasks, models, evidence, workspace, tools | [`docs/contexts/producers.md`](docs/contexts/producers.md) |
| loop | `services/loop/` | Driving a sprint's tasks: the task worker, board moves, carry-over, sprint control, a person's review | contracts, platform, tasks, models, evidence, workspace, tools, learning, planning, producers | [`docs/contexts/loop.md`](docs/contexts/loop.md) |
| release | `services/release/` | What leaves the factory once accepted: publish, local merge, integration, undo | contracts, platform, tasks, models, evidence, workspace, planning, loop | [`docs/contexts/release.md`](docs/contexts/release.md) |
| integrations | `services/integrations/` | Telling the outside world: the notification webhook and the outbox processor | contracts, platform, tasks, evidence | [`docs/contexts/integrations.md`](docs/contexts/integrations.md) |
| management | `services/management/` | The owner as a client: the Manager, Reviewer and Support agents, the brief | contracts, platform, tasks, models, evidence, workspace, tools, learning, planning, producers, loop, release, integrations | [`docs/contexts/management.md`](docs/contexts/management.md) |

Outside `services/`: the workspace daemon (`vscode-daemon/`) is described in workspace; a router with the service it fronts; the LLM proxy (`llm-proxy/`) and model routing in models; the janitor, the migrations, the NATS client, locks and replicas, auth and multi-tenancy and memory in platform; the frontend's views in [`docs/contexts/frontend.md`](docs/contexts/frontend.md). The task worker's steps are in [`docs/SPRINT_LOOP.md`](docs/SPRINT_LOOP.md).

**The dependency rule** (issue #469, `docs/ARCHITECTURE.md`, ADRs 0012–0016). A context imports only the contexts its row names; the evidence imports only contracts and platform. The assignment and the rule are data, `docs/architecture/contexts.json`, checked by `scripts/check_architecture.py` (`python3 scripts/check_architecture.py`; `tests/test_check_architecture.py`, and a step of `backend-tests.yml`): a module in no context, a cycle between contexts or an import against the rule fails. Today it reports 0 shims and 0 allowlisted edges, and both stay there. HTTP lives only in named adapter modules: under `services/`, a module that imports an HTTP library (`httpx`, `requests`, `urllib.request`…) must be listed in `http_adapters` in `contexts.json`, returns data and raises named errors, and that list only shrinks unless a new adapter is reviewed (issue #511, after AixleHQ/flow's "no HTTP in domain services" rule). A new module names its context by its folder, `services/<context>/`, in the same pull request. How the code got there is [`docs/architecture/MIGRATION.md`](docs/architecture/MIGRATION.md).

The frontend has a rule of its own (issue #519): `frontend/src` is in layers, from the bottom up foundation (`i18n`, `styles`), `services`, `lib`, state (`store`, `hooks`), `components`, `pages` and the app (`App.tsx`, `main.tsx`); a module imports only from its layer or below, and only the modules in `network.modules` (`services/api/client.ts`, `services/api/stream.ts`) use `fetch`, `EventSource`, `WebSocket`, `XMLHttpRequest` or `axios`. The data is `docs/architecture/frontend.json`, checked by `scripts/check_frontend_architecture.py` (a step of `frontend-build.yml`; `backend/tests/test_check_frontend_architecture.py`), and its allowlist only shrinks; the detail is in [`docs/contexts/frontend.md`](docs/contexts/frontend.md).

## Tests and lint

The whole section (both tables of test files, the e2e run, the bench, CI) is [`docs/TESTING.md`](docs/TESTING.md).

```bash
# Backend (no Postgres, NATS, Neo4j or LLM proxy needed)
cd backend && PYTHONPATH=.:app python3 -m pytest tests -q

# Backend lint: the ruff version CI pins, since the default rule set moves between releases
pip install "ruff==0.17.1"
cd backend && ruff check app tests && ruff format --check app tests

# LLM proxy, from the repo root with the proxy's Poetry environment
python -m pytest llm-proxy/tests -q

# Frontend
cd frontend && npx tsc --noEmit && npm run lint && npm run test:run && npm run build

# The dependency rules (backend, frontend) and the file-size ratchet
python3 scripts/check_architecture.py
python3 scripts/check_frontend_architecture.py
python3 scripts/check_file_sizes.py
```

`scripts/check_file_sizes.py` keeps a source file at or under 600 lines; the files already over it (`scripts/file_size_baseline.json`) may shrink but never grow, and `--update` lowers an entry.

## Frontend i18n

All user-facing strings must be added to both:
- `frontend/src/i18n/en.json`
- `frontend/src/i18n/es.json`

Use the `useTranslation` hook from i18next; never hardcode English strings in JSX.

## Branches

- Branches: trunk-based. Each change is a branch cut from `main` with one pull request into `main`, merged once the head is green (`CONTRIBUTING.md`, Branches). There is no `develop` integration branch.
- The Glassroom VR entry for the Meta VR Start Developer Competition lives on
  the `main_meta` branch (`vr/`, `docs/VR_ENTRY.md`); `main` is the web product.

## Where things are documented

- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — Every environment variable
- [`docs/TESTING.md`](docs/TESTING.md) — The test suites and their tables, the e2e run, the bench, CI and the runner, manual validation
- [`docs/SPRINT_LOOP.md`](docs/SPRINT_LOOP.md) — Data flow for a sprint: the task worker's steps
- [`docs/contexts/`](docs/contexts/) — One document per context (the table above), and [`frontend.md`](docs/contexts/frontend.md)
- [`docs/architecture/MIGRATION.md`](docs/architecture/MIGRATION.md) — The record of the ADR 0016 migration, step by step
- [`README.md`](README.md) — Project overview, architecture, setup, and API reference
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — Development standards and workflows
- [`SECURITY.md`](SECURITY.md) — Security policy and vulnerability reporting
- [`.claude/skills/`](.claude/skills/README.md) — Claude Code skills based on Microsoft's SDL practices: `sdl-review` (a change before its PR), `threat-model` (a new design), `sdl-program` (the quarterly or pre-release security review)
- [`docs/POSITIONING.md`](docs/POSITIONING.md) — Project direction and design rules
- [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) — Design with the owner's decisions (not built yet): workflows per board column, task kind or sprint close, as typed-step DAGs whose invariants keep the Verifier, the holds and the history checker; custom columns as views over the five statuses; Owner-only activation
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — The backend's contexts and their dependency rule, rendered from `docs/architecture/architecture.json`; ADRs in `docs/adr/`
- [`docs/STANDARDS.md`](docs/STANDARDS.md) — The quality standards every project is held to, and which the loop checks without a model
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — The operations runbook: deploy, upgrade, key rotation, `/health` and `/metrics`, the daemon absent, a sprint stuck, a provider out of credit, backups and restore, retention, and how many workers may run
- [`bench/README.md`](bench/README.md) — The bench: reference specifications graded by person-written tests, scorecards, and how to add a specification

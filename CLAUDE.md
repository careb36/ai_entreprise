# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Le Fabrique** is an AI-powered autonomous software development platform. It orchestrates a multi-agent team (PO, Architect, ScrumMaster, Developer, QA, SecOps, TechWriter) using LangGraph to execute software sprints — from backlog to code generation and execution — inside a user's VS Code workspace.

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

## Running the System

```bash
# Full stack (preferred)
docker compose up -d

# Individual services
docker compose up backend -d
docker compose up frontend -d
docker compose up llm-proxy -d

# VS Code daemon (runs locally, not in Docker)
pip install nats-py
python vscode-daemon/daemon.py
```

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
- `AUDIT_SIGNING_KEY` — optional. Signs audit rows with HMAC-SHA256.
  Generate with: `python -c "import secrets; print(secrets.token_hex(32))"`

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

**`backend/app/main.py`** — All FastAPI routes (single large file). Responsible for: company init, sprint management, kanban board state, memory search/store, enterprise quotas, support tickets, and analytics.

**`backend/agents/graph.py`** — Optional LangGraph producer, disabled unless `AGENT_GRAPH_ENABLED=true`. Its runs are ingested as `AgentRun` records with `producer: "le-fabrique-graph"`.

**`backend/app/llm_proxy_client.py`** — Thin HTTP client wrapping the LLM proxy for token-budget-aware requests. Use `model_preference="fast"` (flash models) or `"powerful"` (pro models).

**`llm-proxy/app.py`** — Flask gateway that enforces per-company token budgets, routes to Gemini or DeepSeek based on the model name prefix, and refreshes available Gemini models hourly.

**`vscode-daemon/daemon.py`** — Long-running Python process that subscribes to NATS subjects `workspace.generate` and `workspace.execute`, then performs actual file writes and shell commands inside the user's VS Code workspace. Must run locally (not in Docker) so it has direct filesystem access.

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — sprint task tracking with drag-and-drop (`@dnd-kit`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat

**`backend/janitor/main.py`** — Background maintenance container (runs daily). Cleans outbox events and rebuilds pgvector indices with `REINDEX CONCURRENTLY`.

### Data Flow for a Sprint

1. User creates a sprint via frontend → `POST /v1/start_sprint`
2. Backend publishes the sprint to NATS via the transactional outbox
   Steps 3–6 only happen when `AGENT_GRAPH_ENABLED=true`.
3. LangGraph graph executes agent nodes sequentially, each calling LLM proxy
4. Architect/PO nodes use Gemini (high reasoning); Developer/QA nodes use DeepSeek (cost-effective)
5. Agent decisions and code are published back to NATS as `workspace.generate` / `workspace.execute`
6. VS Code daemon receives NATS messages, writes files and runs shell commands in the workspace
7. Engram events are written to Neo4j for episodic memory; vector embeddings to pgvector for RAG

### Memory Architecture

- **pgvector (Postgres)** — RAG short-term semantic memory. Used for code snippet retrieval and context injection. API: `GET /v1/memory/search`, `POST /v1/memory/store`
- **Neo4j** — Temporal knowledge graph. Immutable "Engram" nodes record every agent decision with timestamps. Used for lineage, compliance, and cross-sprint learning. Queried via APOC plugin.

### Auth & Multi-tenancy

- Company-scoped: all data (agents, sprints, memory, budgets) is namespaced by `company_id`
- RBAC covers both human roles (manager, developer) and AI agent roles (PO, Architect, etc.)
- Passwords hashed with passlib/bcrypt. `POST /v1/auth/login` issues an HS256 JWT (`JWT_SECRET_KEY`); `auth_deps.get_current_user` resolves it. Authentication enforcement is on by default; set `AUTH_REQUIRED=false` only for a local demo without a login. Configure `JWT_SECRET_KEY`, then register and log in through the frontend. Protected routes require a JWT and company roles follow Owner > Manager > Viewer. Company initialization creates the caller's Owner membership; invitations require Owner.
- Always public: `/health`, registration/login, the HMAC-signed GitHub webhook, support bug reporting/chat, `/v1/models`, `/v1/capabilities` (feature flags only, no company data), Glassroom SSE (EventSource cannot send headers), and the retired `/v1/approve_task` (410). `/v1/auth/me` always requires a JWT. Audit writes and memory stores resolve company ownership from authorized membership and existing interactions, never payload metadata; callers with multiple memberships should select a `company_id` query parameter. Audit verification, memory search, and lineage use column-backed Viewer scoping. Legacy NULL-company evidence is flagged and included only without a company filter (authenticated when `AUTH_REQUIRED=true`). Startup backfills historical company metadata into the audit/vector columns and Neo4j properties. Help-desk tickets/logs require authentication, not company membership.

## Test Suite

The backend has a pytest test suite under `backend/tests/`.  No external services (Postgres, NATS, Neo4j, LLM proxy) are required — every boundary is stubbed with an in-memory SQLite engine or `unittest.mock`.

```bash
# Run all backend tests (fast, DB-free)
cd backend && PYTHONPATH=.:app python3 -m pytest tests -q
```

Test files:

| File | What it covers |
|------|----------------|
| `test_glassroom_sse.py` | SSE fan-out and stream ordering |
| `test_orchestration_metrics.py` | Orchestration metric helpers |
| `test_start_sprint.py` | `POST /v1/start_sprint` — happy path, validation, outbox events |
| `test_graph.py` | LangGraph node compilation, routing helpers, `_invoke_agent` events |
| `test_needs_split.py` | `_mark_sprint_needs_split` and `_complete_sprint_and_plan_follow_up` |
| `test_model_factory.py` | Role-to-model map and business-unit header propagation |
| `test_crypto.py` | Fernet credential encryption: round trip, tamper and wrong-key rejection, keyless fallback, legacy rows |
| `test_llm_routing.py` | Task worker: empty agent model → role default from `model_factory`, LLM failures on the board, `served_model` on the AgentRun |

The LLM proxy has its own suite under `llm-proxy/tests/` (model routing,
`unknown_model` 400s, upstream errors, catalogue-only fallback), run from the
repo root with the proxy's Poetry environment plus
`llm-proxy/requirements-dev.txt`: `python -m pytest llm-proxy/tests -q`. CI
runs it after the backend tests.

CI runs this suite on every pull request that touches `backend/` or the
workflow itself, and on pushes to `main` and `develop`
(`.github/workflows/backend-tests.yml`), installing dependencies with Poetry
from `backend/poetry.lock`. Feature-branch pushes do not trigger it; open a
pull request to get a run. Add new backend dependencies to
`backend/pyproject.toml` and commit the refreshed lock file, or CI will fail
the `poetry check --lock` step.

Frontend changes are checked separately by
`.github/workflows/frontend-build.yml` on every pull request that touches
`frontend/` or the workflow itself, and on pushes to `main` and `develop`.
That job runs `npm ci`, `npx tsc --noEmit`, `npm run test:run`, and
`npm run build` from the `frontend/` directory, using
`frontend/package-lock.json` as the cache key.

The frontend test suite uses Vitest with Testing Library and jsdom. Run it from
`frontend/` with `npm run test` for watch mode during development, or
`npm run test:run` for the non-watch CI/local verification command.

| File | What it covers |
|------|----------------|
| `src/components/AnalyticsDashboard.test.tsx` | Orchestration health KPI rendering, loading/error handling, empty state, and pre/post Batch 1 weekly trend behavior |
| `src/pages/AgentSettings.test.tsx` | Model selector lists `/v1/models` entries after a "Default for role" option |

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

When adding new LLM calls in agents, pass `model_preference` to the proxy client:
- `"fast"` → Gemini Flash or DeepSeek V2 (default for most agent nodes)
- `"powerful"` → Gemini Pro (reserved for Architect and high-stakes reasoning)

Token budgets are enforced per company at the proxy layer — the backend never calls Gemini/DeepSeek directly.

## Documentation

- [`README.md`](README.md) — Project overview, architecture, setup, and API reference
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — Development standards and workflows
- [`SECURITY.md`](SECURITY.md) — Security policy and vulnerability reporting
- [`docs/POSITIONING.md`](docs/POSITIONING.md) — Project direction and design rules
- [`runbook.md`](runbook.md) — Production hardening guide
- [`REFACTORING_PLAN.md`](REFACTORING_PLAN.md) — Current refactoring strategy

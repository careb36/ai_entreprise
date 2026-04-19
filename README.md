<div align="center">
  <img src="frontend/public/fabrique-icon.svg" width="90" alt="La Fabrique" />

# La Fabrique

[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/react-%2320232b.svg?style=for-the-badge&logo=react&logoColor=%2361DAFB)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/typescript-%23007acc.svg?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)

**AI-powered autonomous software development platform**

> **Languages:** [🇺🇸 English](README.md) | [🇪🇸 Español](README.es.md)

</div>

---

## Table of Contents

- [System Overview](#system-overview)
- [Architectural Stack](#architectural-stack)
- [Local Setup](#local-setup)
- [Agent Orchestration](#agent-orchestration)
- [GlassRoom](#glassroom)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [License](#license)

---

## System Overview

La Fabrique is a multi-agent platform that orchestrates AI agents through a structured sprint lifecycle. Work is organized into **AI Sprints** — units of bounded, verifiable scope rather than fixed durations.

**Sprint scope tiers:**

| Size | Max backlog items | Typical use |
|------|-------------------|-------------|
| S    | 4                 | Bug fixes, small features |
| M    | 8                 | Feature iterations (default) |
| L    | 12                | Large features, foundational work |

When an agent workflow exhausts its capacity before completing all items, the sprint transitions to `needs_split` status — signalling that the scope should be broken into smaller sprints before retrying.

**Sprint lifecycle:** `active` → `completed` | `needs_split` | `cancelled`

---

## Architectural Stack

```
┌──────────────────────────────────────────────────┐
│  React + TypeScript (Vite)  ← frontend           │
│  Dashboard / GlassRoom / KanbanBoard              │
└────────────────────┬─────────────────────────────┘
                     │ HTTP + SSE
┌────────────────────▼─────────────────────────────┐
│  FastAPI  (backend/app)                           │
│  Routers: sprint, agents, enterprise, support     │
│  Services: agent_worker, task_worker, outbox      │
└──┬───────────────────────────────┬────────────────┘
   │ NATS JetStream                │ SQLModel / pgvector
   │ workspace.generate            │
   │ glassroom.<sprint_id>         ▼
   │ task.assign              PostgreSQL + pgvector
   │ sprint.needs_split       (sprint state, audit, embeddings)
   ▼
LangGraph multi-agent graph       Neo4j 5 (temporal engram graph)
kickoff → po → scrum_master       (decision lineage, agent memory)
        → developer → po_wrap
        → burn → END
   │
   ▼
LLM Proxy  (token budget + audit gateway)
Gemini / DeepSeek / configurable per role
```

**8 Docker services:** `nats`, `postgres` (pgvector/pg15), `neo4j` (5.15 Enterprise + APOC), `llm-proxy`, `backend`, `vscode-daemon`, `janitor`, `frontend`

**Key patterns:**
- **Transactional Outbox** — `OutboxEvent` rows committed atomically with business data; background worker drains to NATS
- **SSE bridge** — `_listen_to_nats()` coroutine in `main.py` forwards `glassroom.<sprint_id>` NATS messages to connected browser clients with a 50-event replay buffer
- **`_ensure_runtime_schema()`** — additive `ALTER TABLE IF EXISTS … ADD COLUMN IF NOT EXISTS` at startup; no Alembic required
- **LLM routing** — `ModelFactory` maps agent roles to specific models; swap models per role without code changes

---

## Local Setup

The canonical way to start a clean local environment is `reset_infra.sh`:

```bash
cp .env.example .env
# Edit .env — add API keys for your LLM provider(s)

./reset_infra.sh
```

This script runs 6 steps: stops all containers (`down -v`), prunes Docker resources, rebuilds all images (`--no-cache`), starts all services, waits for health checks, and prints final status.

After it completes:
- Frontend: `http://localhost:5173`
- Backend API + Swagger: `http://localhost:8000/docs`

**Prerequisites:** Docker Desktop, Docker Compose v2

**Incremental restarts** (after code changes):
```bash
docker compose up -d --build backend
docker compose up -d --build frontend
```

---

## Agent Orchestration

Each sprint kickoff triggers a **LangGraph workflow** via the `workspace.generate` NATS subject. The graph has a fixed topology with `recursion_limit=12`:

```
kickoff → po → scrum_master → developer → po_wrap → burn → END
                ↘ (on error) ↗
                    burn
```

**Node responsibilities:**

| Node | Agent Role | Responsibility |
|------|-----------|----------------|
| `kickoff` | System | Validates sprint brief, extracts backlog items, emits `status` event |
| `po` | Product Owner | Creates OpenProject work package, prioritizes backlog, identifies riskiest item |
| `scrum_master` | Scrum Master | Sequences work, identifies blockers, assigns first move to Developer |
| `developer` | Developer | Claims first actionable item, states implementation plan, surfaces dependencies |
| `po_wrap` | Product Owner | Closes kickoff with 3-bullet summary: priority, owner, next action |
| `burn` | System | Persists engram to Neo4j, publishes `needs_split` or completion event to NATS |

**Error handling:** Any node failure sets `last_error` and routes directly to `burn`, which marks the sprint `needs_split`. `GraphRecursionError` is caught explicitly at the workflow level and also returns `needs_split: True`.

**Agent models** are resolved by `ModelFactory.ROLE_MODEL_MAP` — each role (PO, ScrumMaster, Developer) maps to a configurable LLM.

---

## GlassRoom

The GlassRoom is the real-time observability view for a running sprint. It uses SSE (Server-Sent Events) to stream `glassroom.<sprint_id>` NATS events directly to the browser.

**3-column layout:**

| Column | Content |
|--------|---------|
| Left Sidebar | Sprint metadata (scope badge, status dot) + online agent roster |
| Message Feed | Slack-style grouped messages per agent turn; `NeedsSplitBanner` for scope-exceeded events |
| Right Panel | Per-agent FSM state cards — `active` (green), `waiting` (brand), `error` (red), `idle` (gray) |

**Event types streamed:** `status`, `agent_start`, `message`, `agent_complete`, `error`, `needs_split`

The SSE endpoint (`GET /v1/glassroom/stream?sprint_id=<id>`) replays the last 50 events on connect so late-joining clients get full context.

---

## Project Structure

```
la-fabrique/
├── backend/
│   ├── agents/
│   │   ├── graph.py              # LangGraph multi-agent workflow
│   │   └── tools/
│   │       └── openproject_tools.py  # Work package creation
│   ├── app/
│   │   ├── main.py               # FastAPI entry point, 4 bg workers, SSE bridge
│   │   ├── models/               # SQLModel ORM (Sprint, Company, BacklogItem…)
│   │   ├── schemas.py            # Pydantic request/response schemas
│   │   ├── database.py           # Engine, session, _ensure_runtime_schema()
│   │   ├── routers/              # sprint, agents, enterprise, support
│   │   ├── services/
│   │   │   ├── agent_worker.py   # NATS consumer → LangGraph execution
│   │   │   ├── task_worker.py    # Task assignment worker
│   │   │   └── outbox_service.py # Transactional outbox → NATS
│   │   └── nats_client.py        # Async NATS publish/subscribe wrapper
│   ├── llm/
│   │   └── model_factory.py      # Role → model mapping
│   ├── memory/
│   │   └── engram_processor.py   # pgvector + Neo4j engram persistence
│   ├── llm_proxy_client.py       # HTTP client for LLM proxy service
│   └── telemetry.py              # OpenTelemetry setup
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── GlassRoom.tsx     # 3-column real-time sprint view
│       │   ├── KanbanBoard.tsx   # Sprint backlog board
│       │   ├── ControlPanel.tsx  # Sprint configuration UI
│       │   └── ErrorBoundary.tsx
│       ├── pages/
│       │   ├── Dashboard.tsx     # KPI strip + sprint cards + quick launch
│       │   └── CompanyNew.tsx    # Company onboarding wizard
│       ├── store/
│       │   └── agentStore.ts     # Zustand FSM store (idle/active/waiting/error)
│       └── i18n/                 # EN + ES translations
├── llm-proxy/                    # Token budget + audit gateway service
├── vscode-daemon/                # Local NATS subscriber for IDE file ops
├── db/                           # Postgres init scripts
├── docker-compose.yml            # 8-service local stack
├── reset_infra.sh                # Canonical full-reset script
└── .env.example
```

---

## API Reference

Interactive docs available at `http://localhost:8000/docs` when running locally.

Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/init_company` | Create company with default scope size (S/M/L) |
| `POST` | `/v1/start_sprint` | Start sprint with scope constraint; validates item count |
| `POST` | `/v1/generate` | Trigger LangGraph workflow via NATS |
| `GET`  | `/v1/glassroom/stream` | SSE stream for sprint agent events |
| `POST` | `/v1/audit` | Write audit entry |
| `GET`  | `/v1/memory/search` | Semantic search over engram memory |
| `POST` | `/v1/memory/store` | Store content in vector memory |

---

## License

**Proprietary Software.** All rights reserved.  
Copyright © 2026 Carolina Pereira.

Unauthorized copying, distribution, or commercial use of this system, via any medium, is strictly prohibited without explicit written permission from the owner.

---

*Maintained by Carolina Pereira. Critical errors are automatically reported. For licensing and Enterprise SLA inquiries, contact the owner directly.*

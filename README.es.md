# La Fabrique

Agent orchestration platform with **GlassRoom** observability, **Engram** memory,
and **vector search** over **Neo4j** + **PostgreSQL/pgvector**.

- **Backend** — Python 3.11 · FastAPI · LangGraph
- **Frontend** — React 18 · Vite 5 · TypeScript
- **llm-proxy** — standalone FastAPI gateway in front of LLM providers
- **Data** — PostgreSQL 16 (+ pgvector), Neo4j 5

> **Sprint 0 status:** Foundations. APIs are unstable; do not run outside local dev.

---

## Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Python | 3.11.x | `python3 --version` |
| Node.js | 20 LTS | `node --version` |
| Docker + Compose v2 | 24+ / v2.20+ | `docker compose version` |
| GNU Make | 4.0+ | `make --version` |
| uv (recommended) | latest | `uv --version` |

> No `uv`? Every `make` Python target also works with `pip` (see `Makefile` `PY_INSTALL`).

---

## Quickstart (clean checkout → running stack)
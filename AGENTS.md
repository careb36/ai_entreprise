# AGENTS.md

Read this first. `CLAUDE.md` has the long-form architecture notes (some of it
stale — see "Docs that mislead"); `docs/POSITIONING.md` carries the project
direction and the design rules that constrain new work.

## What this repository is

La Fabrique: an AI software factory. A React app, a FastAPI control plane and a
Flask LLM gateway run agent roles (PO, Architect, ScrumMaster, Developer, QA,
SecOps, TechWriter) over sprints. Since the repositioning tracked in issue #38
(`docs/POSITIONING.md`) the agents are interchangeable *producers*, and the
product is the layer that verifies their work and records evidence.

| Path | What lives there |
|------|------------------|
| `backend/app/` | FastAPI app. `main.py` only wires routers, CORS, lifecycle; `routers/` hold HTTP endpoints, `services/` the logic, `models/` the SQLModel tables |
| `backend/agents/`, `backend/llm/`, `backend/memory/` | optional LangGraph producer, role→model map, engram processing |
| `backend/janitor/` | daily cleanup/reindex container |
| `backend/tests/` | pytest suite, 50 modules |
| `frontend/` | React 18 + Vite + TypeScript + Tailwind; `electron/` for the desktop build |
| `llm-proxy/` | Flask gateway (per-company token budgets, model routing) with its own tests |
| `vscode-daemon/` | host-side NATS consumer that writes files and runs commands |
| `scripts/e2e/` | end-to-end run against a real Postgres and NATS |
| `db/init-db/`, `deployment/k8s/` | schema bootstrap, manifests |

Toolchain: Python (`^3.11` in `backend/pyproject.toml`; CI runs 3.12), Poetry
per project, Node 24, React 18, Vite 5, Vitest 3, ESLint 10 flat config, ruff
(version pinned in CI). The backend and the LLM proxy are **two independent
Poetry projects**, each with its own lock file.

## Commands

All of these were run green on `main` when this file was written.

```bash
# Backend — no Postgres, NATS, Neo4j or LLM needed (in-memory SQLite + mocks)
cd backend && poetry install
cd backend && PYTHONPATH=.:app poetry run pytest tests -q        # ~50s, 665 tests
cd backend && PYTHONPATH=.:app poetry run pytest tests/test_board.py -q
cd backend && ruff check app tests && ruff format --check app tests   # ruff==0.17.1, as CI pins it

# Frontend — the same four gates CI runs, in order
cd frontend && npm ci && npx tsc --noEmit && npm run lint && npm run test:run && npm run build
cd frontend && npx vitest run src/components/LearningTrend.test.tsx

# LLM proxy — its own environment and test-only requirements
cd llm-proxy && poetry install --no-interaction --no-root \
  && poetry run pip install -r requirements-dev.txt \
  && poetry run python -m pytest tests -q

# Whole stack (.env copied from .env.example, keys filled in)
docker compose up -d        # backend :8000 (docs at /docs), frontend :5173,
                            # llm-proxy :5100, Jaeger :16686
make dev-backend            # or dev-frontend / dev-llm-proxy: Poetry and Vite directly
./scripts/start-daemon.sh   # workspace daemon, on the host, not in Docker
```

There is no ruff config file, so ruff's defaults apply; the CI-pinned version is
0.17.1 and its default rule set moves between releases, so lint with that
version. mypy runs in CI but is non-blocking (`|| true`). Nothing formats
TypeScript: the prettier step in `frontend-tests.yml` is non-failing and
prettier is not a dependency.

`scripts/e2e/run.sh` is the end-to-end check. It needs a real Postgres and a
NATS started with JetStream (`docker compose up -d postgres nats`), recreates
its own `factory_e2e` database, and wants `E2E_PYTHON` pointing at the backend
venv's interpreter.

## Architecture facts that shape most changes

- **Imports are top-level.** `from config import settings`, `from models.sprint
  import BacklogItem` — because tests run with `PYTHONPATH=.:app`. Keep that
  style rather than switching to `app.`-prefixed imports.
- **Layering.** Routers call `services/`; DB access stays out of routers, with
  `get_session` and `auth_deps.get_current_user` injected. Company data is
  scoped by `company_id`; roles are Owner > Manager > Viewer.
- **Evidence layer.** Agent work is ingested as `AgentRun` (+ `AgentRunApproval`,
  `Verification`) and verified deterministically in `services/verification.py`,
  `services/board.py`, `services/reviewer_agent.py`. Claims (what an agent says
  it did) and observations (what the daemon reports from disk) never share a
  field; `unverifiable` is a verdict of its own; no LLM decides whether a claim
  holds; hashes or signatures are never taken from the party being recorded.
- **Async path.** Sprint tasks go sprint → transactional outbox
  (`OutboxEvent`, `services/outbox_service.py`) → NATS JetStream
  (`task.assign`) → `services/task_worker.py` (LLM call) → the daemon
  (`workspace.generate`, `workspace.execute`) → verification → review loop.
  The LangGraph graph (`backend/agents/graph.py`) is only an optional kickoff
  producer (`AGENT_GRAPH_ENABLED`).
- **LLM access always goes through the proxy.** The backend never calls a model
  provider directly: `app/llm_proxy_client.py` → `llm-proxy/app.py`, with
  `model_preference="fast"` (default) or `"powerful"` (Architect and other
  high-stakes callers). Role→model defaults live in `backend/llm/model_factory.py`.
- **Memory.** pgvector in Postgres for RAG (`/v1/memory/*`), Neo4j Engram nodes
  for decision lineage (`backend/memory/engram_processor.py`).
- **Auth.** Enforced by default (`AUTH_REQUIRED`); login answers 503 until
  `JWT_SECRET_KEY` is set. Public routes are a short, deliberate list
  (`/health`, register/login, the signed GitHub webhook, the public support
  form/chat, `/v1/models`, `/v1/capabilities`, and the ticketed Glassroom SSE
  stream).
- **Frontend.** Zustand for state, TanStack Query for API calls, Glassroom over
  SSE.

## Conventions

- **i18n is a build gate.** Every user-facing string goes through
  `useTranslation` and into both `frontend/src/i18n/en.json` and `es.json`.
  `npm run lint` runs `eslint-plugin-i18next`'s `no-literal-string` over JSX
  text and the `title`/`alt`/`placeholder`/`aria-*`/`label` attributes, so a
  hardcoded English literal fails the build. Use i18next `defaultValue` rather
  than `t(key) || fallback` (a missing key returns the key, which is truthy).
- **Backend tests.** Use the `sqlite_session` and `test_client` fixtures from
  `backend/tests/conftest.py`. Import any new SQLModel table in that conftest,
  or `SQLModel.metadata.create_all()` will not create it.
- **Dependencies.** Add them to the relevant `pyproject.toml` *and* commit the
  refreshed `poetry.lock`; `frontend/package-lock.json` is what CI installs
  from (`npm ci`).
- **Git.** `main` is the default branch and the web product; the Glassroom VR
  entry lives on `main_meta`. Branch names are typed (`ci/…`, `copilot/…`,
  `claude/…`), commit subjects are written in Spanish and often carry a
  Conventional Commits prefix (`docs: …`, `ci: …`, sometimes none). Fill in
  `.github/PULL_REQUEST_TEMPLATE.md` (it carries the backend and
  frontend checklists) and reference an issue. A human merges; agents never
  merge to `main`.
- **Secrets.** Keys (`JWT_SECRET_KEY`, `FERNET_SECRET_KEY`,
  `AUDIT_SIGNING_KEY`, provider API keys) come from `.env`, which is
  git-ignored; only `.env.example` is committed. `SECURITY.md` outranks
  `CONTRIBUTING.md`, which outranks tooling defaults.

## CI

Workflows live in `.github/workflows/` and mostly run on the self-hosted
`[self-hosted, linux, x64, lafabrique]` runner: `backend-tests.yml` (Poetry,
ruff, mypy, pytest, then the llm-proxy suite), `frontend-build.yml` (`npm ci`,
tsc, lint, tests, build), `frontend-tests.yml` (the same plus the non-failing
prettier step), `e2e.yml` (the loop against pgvector/Postgres and NATS with
JetStream), `security-scan.yml` (Trivy, plus a non-failing `safety check`) and
a manual SLSA provenance workflow. Feature-branch pushes do **not** trigger the
test workflows — push a pull request to get a run. Backend and frontend jobs
are path-filtered on `backend/**`, `llm-proxy/**`, `frontend/**` and their own
workflow file.

## Docs that mislead

Several top-level documents stop mid-section or describe tooling that is not
here. Trust the code and the workflows over them:

- `CONTRIBUTING.md` ends at "## 3. Local setup" and `CODING_STANDARDS.md` at
  "### 3.1 Monorepo layout" — the rules survive, the details do not.
- `TESTING_STRATEGY.md` describes Playwright, msw and deepeval; none are
  installed.
- `CLAUDE.md` still says `backend/app/main.py` holds every route and that CI
  runs `poetry check --lock`. Neither is true: routes live in
  `backend/app/routers/`, and the backend job runs ruff, mypy and pytest.

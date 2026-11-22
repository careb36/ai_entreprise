# AGENTS.md

Guidance for coding agents. Start from [`CLAUDE.md`](CLAUDE.md): it is the
index (what the project is, the owner's directives, the contexts and their
dependency rule, tests, i18n, branches). Read
[`docs/POSITIONING.md`](docs/POSITIONING.md) for the design rules. This file
adds the practical facts CLAUDE.md leaves out. When a document disagrees with
`backend/app/`, the code wins.

## Layout

| Path | What lives there |
|------|------------------|
| `backend/app/` | FastAPI app: `main.py` wires routers and lifecycle, `routers/` the endpoints, `services/` the logic (fourteen contexts), `models/` the SQLModel tables |
| `backend/alembic/` | schema migrations |
| `backend/janitor/` | daily cleanup and reindex container |
| `backend/tests/` | pytest suite |
| `frontend/` | React 18, Vite, TypeScript, Tailwind; `electron/` for the desktop build |
| `llm-proxy/` | Flask gateway: per-company token budgets, routing by model id |
| `vscode-daemon/` | host-side NATS consumer that writes files and runs commands |
| `scripts/e2e/` | end-to-end run against a real Postgres and NATS |
| `db/init-db/`, `deployment/k8s/` | schema bootstrap, manifests |

The backend and the LLM proxy are two independent Poetry projects, each with
its own lock file. Python `^3.11` (`backend/pyproject.toml`), Node 24.

## Running pieces by hand

The backend needs `PYTHONPATH=.:app` for its top-level imports (`from config
import settings`). `make dev-backend` and the Docker image set it; without it
uvicorn fails with `ModuleNotFoundError: No module named 'config'`.

```bash
cd backend && PYTHONPATH=.:app poetry run alembic upgrade head
cd backend && PYTHONPATH=.:app poetry run uvicorn app.main:app --reload --port 8000
make dev-frontend            # Vite alone; it proxies /v1 to localhost:8000
cd llm-proxy && poetry install --no-root && poetry run pip install -r requirements-dev.txt \
  && poetry run python -m pytest tests -q
```

- The backend refuses to start on a database behind the migrations' head;
  `DB_AUTO_UPGRADE=true` runs them at startup instead.
- ruff is not in the backend's Poetry dev group and has no config file:
  `pip install "ruff==0.17.1"` (the version CI pins). Nothing formats
  TypeScript.
- `scripts/e2e/run.sh` needs `docker compose up -d postgres nats` and
  `E2E_PYTHON` set to the backend venv's interpreter.

## Facts that shape most changes

- **Imports are top-level** (`from models.sprint import BacklogItem`), not
  `app.`-prefixed. Keep that style.
- **Routers call services.** DB access stays out of routers; `get_session` and
  `auth_deps.get_current_user` are injected. Company data is scoped by
  `company_id`; roles are Owner > Manager > Viewer.
- **Evidence.** Agent work is stored as `AgentRun` and verified
  deterministically (`services/evidence/`). No model decides whether a claim
  holds, and `unverifiable` is a verdict of its own.
- **Async path.** Sprint tasks go through the transactional outbox
  (`services/integrations/`) to NATS JetStream, then the task worker
  (`services/loop/task_worker.py`), the daemon, the Verifier and review.
- **Model calls go through the proxy.** The backend never calls a provider
  directly: `app/llm_proxy_client.py` to `llm-proxy/`. The providers are
  DeepSeek (default) and Qwen.
- **Auth is on by default** (`AUTH_REQUIRED`); login answers 503 until
  `JWT_SECRET_KEY` is set. Public routes are a short, deliberate list.
- **Frontend.** Zustand for state, TanStack Query for API calls.

## Conventions

- **i18n is a build gate.** `npm run lint` fails on a hardcoded JSX string.
  Use i18next `defaultValue`, not `t(key) || fallback` (a missing key returns
  the key). `src/i18n/coverage.test.ts` and `src/i18n/locales.test.ts` require
  every key in both locales.
- **Backend tests** use the `sqlite_session` and `test_client` fixtures from
  `backend/tests/conftest.py`. Import a new SQLModel table there, or
  `create_all()` will not create it.
- **Dependencies.** Add them to the right `pyproject.toml` and commit the
  refreshed `poetry.lock`; CI installs the frontend with `npm ci`.
- **Secrets** come from `.env`, which is git-ignored; only `.env.example` is
  committed. `SECURITY.md` outranks `CONTRIBUTING.md`, which outranks tooling
  defaults.

## CI

Workflows are in `.github/workflows/` and run on the self-hosted
`[self-hosted, linux, x64, lafabrique]` runner; keep every workflow on that
label. `changes.yml` decides which jobs a change affects; a skipped job counts
as green. A red run is not always broken code: read the check-run annotations,
not just the log. "The self-hosted runner lost communication with the server"
is the runner host, not the change.

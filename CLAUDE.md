# CLAUDE.md

Guidance for Claude Code in this repository. This file is an index; the detail
lives in the documents it links.

## What this project is

**La Fabrique** is a company of AI agents that plans, builds and documents IT
projects, and records verifiable evidence of what the agents did. Today the
loop runs software sprints end to end inside a user's workspace, with LangGraph
roles (`services/contracts/roles.py`), and builds the project documentation
pack (`docs/contexts/planning.md`). Other project types, automation and
research are direction.

Owner directives (detail in [`docs/POSITIONING.md`](docs/POSITIONING.md); read
it before planning new work):

- **Direction (2026-09-19, issue #38).** The agents are interchangeable
  producers; the product is the layer that verifies their work and records
  evidence.
- **Amendment (2026-10-18).** The factory runs itself as a company of agents
  with procedures and retrospectives. Messages between agents are claims, and
  deterministic metrics keep or revert procedure changes.
- **Reference implementations (2026-11-07).** Before building a piece, study
  how shipping agents did it (Copilot coding agent, Codex cloud, OpenHands,
  Devin, Kiro, Factory) and take what the licence allows. Name the source in
  the pull request.
- **Scope (2026-11-19).** La Fabrique manages IT projects of any kind (data
  science, ML, deep learning…), not only software. An agent's reported metric
  is a claim; it is an observation only when the platform measured it.

Design rules that constrain every change:

- A claim and an observation never share a field.
- An agent's report of its own test run is a claim, not an observation.
- Hashes and signatures are never accepted from the party being recorded.

## Running the system

```bash
docker compose up -d                                     # full stack
NEO4J_ENABLED=false docker compose up -d --scale neo4j=0  # without Neo4j
./scripts/start-daemon.sh                                # daemon (.\scripts\start-daemon.ps1 on Windows)
cd frontend && npm install && npm run dev                # frontend with hot reload
```

Backend `http://localhost:8000` (OpenAPI at `/docs`), frontend
`http://localhost:5173`, LLM proxy `http://localhost:5100`. The daemon runs
natively on the computer that holds the projects and is the only component
that touches the user's files. Every environment variable is in
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

Local git hooks: `pip install pre-commit && pre-commit install --hook-type
pre-commit --hook-type post-merge --hook-type post-rewrite` (ruff, the `.env`
check and gitleaks before a commit; `npm ci` in `frontend/` after a pull that
changed its lock file).

## Contexts

`backend/app/services` is divided into fourteen contexts. **Read the context's
document before changing code in it.**

| Context | Folder | Owns | May import | Document |
|---|---|---|---|---|
| contracts | `services/contracts/` | Shared shapes, pure functions | nothing | [contracts.md](docs/contexts/contracts.md) |
| platform | `services/platform/` | Audit, locks, Glassroom, health, graph reader | contracts | [platform.md](docs/contexts/platform.md) |
| tasks | `services/tasks/` | Task events, hash chain, board readers | contracts, platform | [tasks.md](docs/contexts/tasks.md) |
| models | `services/models/` | Model per role, cost | contracts, platform, tasks | [models.md](docs/contexts/models.md) |
| evidence | `services/evidence/` | Verifier, risk tier, standards, gate before QA | contracts, platform | [evidence.md](docs/contexts/evidence.md) |
| workspace | `services/workspace/` | Daemon client over NATS | contracts, platform | [workspace.md](docs/contexts/workspace.md) |
| tools | `services/tools/` | Tool loop, MCP, edits, repo map, research | contracts, platform, evidence, workspace | [tools.md](docs/contexts/tools.md) |
| learning | `services/learning/` | Procedures, retrospective, triage | contracts, platform, tasks, models | [learning.md](docs/contexts/learning.md) |
| planning | `services/planning/` | Specification, architecture, PO plan, team | contracts, platform, tasks, models, evidence, workspace, tools | [planning.md](docs/contexts/planning.md) |
| producers | `services/producers/` | Producers other than the worker's model call | contracts, platform, tasks, models, evidence, workspace, tools | [producers.md](docs/contexts/producers.md) |
| loop | `services/loop/` | Task worker, board moves, sprint control, review | contracts, platform, tasks, models, evidence, workspace, tools, learning, planning, producers | [loop.md](docs/contexts/loop.md) |
| release | `services/release/` | Publish, local merge, integration, undo | contracts, platform, tasks, models, evidence, workspace, planning, loop | [release.md](docs/contexts/release.md) |
| integrations | `services/integrations/` | Notification webhook, outbox | contracts, platform, tasks, evidence | [integrations.md](docs/contexts/integrations.md) |
| management | `services/management/` | Manager, Reviewer, Support agents, brief | all of the above | [management.md](docs/contexts/management.md) |

Outside `services/`: the daemon (`vscode-daemon/`) is in workspace; the LLM
proxy (`llm-proxy/`) in models; the janitor, migrations, NATS client, auth and
memory in platform; the frontend in
[`docs/contexts/frontend.md`](docs/contexts/frontend.md). The task worker's
steps are in [`docs/SPRINT_LOOP.md`](docs/SPRINT_LOOP.md).

## Dependency rules

- **Backend** (issue #469, ADRs 0012–0018, [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)):
  a context imports only the contexts its row names. The rule is data,
  `docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`.
  It reports 0 shims and 0 allowlisted edges; keep both at 0.
- Across contexts, import only the other context's public modules (`public` in
  `contexts.json`, ADR 0018). The `interface_allowlist` only shrinks.
- HTTP lives only in the adapter modules listed in `http_adapters` (issue
  #511). That list only shrinks unless a new adapter is reviewed.
- A new module names its context by its folder, `services/<context>/`.
- **Frontend** (issue #519): `frontend/src` is layered (foundation, `services`,
  `lib`, state, `components`, `pages`, app). A module imports only from its
  layer or below, and only `services/api/client.ts` and
  `services/api/stream.ts` use the network. Checked by
  `scripts/check_frontend_architecture.py` against
  `docs/architecture/frontend.json`.

## Tests and lint

```bash
scripts/check.sh quick   # while working: what your change can break
scripts/check.sh full    # once before pushing: everything CI runs
```

Individual commands:

```bash
cd backend && PYTHONPATH=.:app python3 -m pytest tests -q   # no Postgres, NATS or model needed
cd backend && ruff check app tests && ruff format --check app tests   # ruff==0.17.1, as CI pins it
python -m pytest llm-proxy/tests -q                         # with the proxy's Poetry env
cd frontend && npx tsc --noEmit && npm run lint && npm run test:run && npm run build
python3 scripts/check_architecture.py
python3 scripts/check_frontend_architecture.py
python3 scripts/check_file_sizes.py
```

A source or test file stays at or under 600 lines; files already over it may
shrink but never grow. Details: [`docs/TESTING.md`](docs/TESTING.md).

## Frontend i18n

Every user-facing string goes in both `frontend/src/i18n/en.json` and
`frontend/src/i18n/es.json`. Use the `useTranslation` hook; never hardcode
English strings in JSX.

## Branches

Trunk-based. Each change is a branch cut from `main`, landed with
`scripts/land.sh <branch>`: it merges `main` and the branch in a clean worktree
(never a rebase), runs `scripts/check.sh full`, fast-forwards local `main` and
writes a verification record. `--push` mirrors `main` and the notes to GitHub.
A protected path lands only with the owner (`LAND_OWNER=1`). There is no
`develop` branch. The Glassroom VR entry lives on `main_meta`. Details:
`CONTRIBUTING.md` and `docs/OPERATIONS.md`.

## Where things are documented

- [`docs/README.md`](docs/README.md): the index of every document
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md): environment variables
- [`docs/TESTING.md`](docs/TESTING.md): test suites, e2e, the bench, CI
- [`docs/SPRINT_LOOP.md`](docs/SPRINT_LOOP.md): the task worker's steps
- [`docs/contexts/`](docs/contexts/): one document per context
- [`docs/POSITIONING.md`](docs/POSITIONING.md): direction and design rules
- [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md): workflow design (not built yet)
- [`docs/STANDARDS.md`](docs/STANDARDS.md): the quality standards
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md): the operations runbook
- [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md)
- [`.claude/skills/`](.claude/skills/README.md): security skills (`sdl-review`, `threat-model`, `sdl-program`)

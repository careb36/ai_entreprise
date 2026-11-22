# La Fabrique — hackathon entry

**Nebius × NVIDIA Global AI Hackathon.** Public repository, Apache 2.0.
Delivery deadline: **30 October 2026, 14:00 GMT-3**.

- Repository: <https://github.com/careb36/la-fabrique>
- License: Apache 2.0 — see [LICENSE](../LICENSE)
- Commits are written in Spanish (the team's convention)

## The pitch

AI agents are cheap and abundant. What is scarce is a **trustworthy answer to
one question** about the work they produce:

> Did this agent actually do what it says it did?

La Fabrique is an AI software factory whose product is not the agents — the
agents are interchangeable. The product is the **verification and evidence
layer** that sits around them. It ingests an agent's work, checks it
deterministically against what is on disk and what CI reports, and stores the
result as evidence, kept strictly separate from the agent's own claims.

Three verdicts, not two: `verified`, `refuted`, and `unverifiable`. A model may
summarise a refutation for a person afterwards; it never decides whether a
claim holds.

## What runs it

| Layer | Technology |
| --- | --- |
| Control plane | FastAPI + SQLModel, PostgreSQL with pgvector |
| Evidence & lineage | Deterministic verifier, append-only signed audit chain, Neo4j engram graph |
| Async path | Transactional outbox → NATS JetStream → task worker → workspace daemon |
| Model access | A single LLM proxy that enforces per-company token budgets and routes by model id |
| Frontend | React 18 + TypeScript + Tailwind, over REST and Server-Sent Events |

## The 5-minute demo (no API keys)

The whole point of the product is that the three verdicts exist and are
distinguishable — so the demo seeds one run per verdict and shows the screen
that tells them apart. No model or collector key is needed.

1. Start the stack and register a user (see the [README quick start](../README.md#quick-start)).
2. Seed the evidence layer:

   ```bash
   python3 scripts/seed_demo_evidence.py --company-id 1
   ```

3. Open `/evidence`. Three runs, one per verdict: **verified**, **refuted**,
   **unverifiable**. Open each to see the claim on one side and the independent
   observations on the other.

The seed is idempotent: each run carries a fixed `external_ref`, and a run that
already has a verification is left alone. The image in the
[README](../README.md#what-it-is) is this exact demo.

## Evidence that it works

Run against a clean checkout; each command was run to completion and is quoted
with its real result, not estimated.

| Check | Command | Result |
| --- | --- | --- |
| Backend tests | `cd backend && PYTHONPATH=.:app python3 -m pytest tests -q` | 771 passed |
| Backend lint | `cd backend && ruff check app tests && ruff format --check app tests` | clean |
| Frontend tests | `cd frontend && npm run test:run` | 392 passed (49 files) |
| Frontend typecheck | `cd frontend && npx tsc --noEmit` | clean |
| Frontend lint | `cd frontend && npm run lint` | clean |
| Frontend build | `cd frontend && npm run build` | built |
| LLM proxy tests | `cd llm-proxy && poetry run python -m pytest tests -q` | 24 passed |

Continuous integration runs on every pull request (path-filtered backend,
frontend, proxy, e2e, security and file-size jobs) on a self-hosted runner.

## What the judges should look at

- **The evidence layer, not the agents.** `backend/app/services/evidence/verification.py`
  is where claims and observations are kept apart and `unverifiable` is a first-class
  verdict.
- **The audit chain.** `POST /v1/audit` signs entries; `GET /v1/audit/chain`
  walks the chain and reports where it breaks.
- **The LLM proxy.** `llm-proxy/` enforces per-company token budgets and routes
  every model call by model id.
- **The governance story.** A blocked task is triaged by the ScrumMaster in a
  fixed, parsable format, and the Review page shows a person what needs them.

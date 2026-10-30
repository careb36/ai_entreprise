# Testing strategy

> **Last updated:** 2026-10-30
> **Related:** [`CODING_STANDARDS.md`](CODING_STANDARDS.md), [`AGENTS.md`](AGENTS.md), [`backend/eval/verificador/README.md`](backend/eval/verificador/README.md)

## The one rule

A test is worth what it would cost to make it fail. Every layer below exists to
answer a question no other layer can, and this document says for each one what
it can and cannot catch.

## The layers

| Layer | Runs where | What it answers | Command |
|---|---|---|---|
| Backend unit and integration | CI, every PR | Does this function, router or service behave as claimed, including its failure modes? | `cd backend && PYTHONPATH=.:app poetry run pytest tests -q` |
| Frontend unit | CI, every PR | Does this component render and behave, with the API mocked at the module boundary? | `cd frontend && npm run test:run` |
| Build gates | CI, every PR | Does it compile and does every user-facing string exist in both locales? | `npx tsc --noEmit`, `npm run lint`, `npm run build` |
| End to end | CI on pull requests, and by hand | Does one company get through a whole sprint, observed only through the API? | `scripts/e2e/run.sh` |
| Verifier benchmark | By hand, and as part of the backend suite | Does the product's central claim hold: does it refuse work it cannot verify? | `cd backend && PYTHONPATH=.:app poetry run python -m eval.verificador.run` |

## Backend: pytest, no external services

57 modules and 771 tests on `main` when this was written, in about six minutes,
with no Postgres, NATS, Neo4j or model provider. That constraint is deliberate: a
suite that needs a database to answer a question about a predicate is a suite
people stop running.

- **Fixtures** live in `backend/tests/conftest.py`. `sqlite_session` gives an
  in-memory database; `test_client` gives an authenticated HTTP client.
- **A new SQLModel table must be imported in that conftest.** Otherwise
  `SQLModel.metadata.create_all()` does not create it and the test passes against
  nothing — a green test for a table that does not exist.
- **The engine is shared and only rolled back, not torn down,** so a committed
  row survives into the next test. Two tests that register the same email make
  `select(...).one()` see two rows and fail. Make identifiers unique per test.
- **Model calls are stubbed at the proxy client**, never at the provider. The
  boundary the tests care about is `LLMProxyClient`, not HTTP.
- **NATS is stubbed** by `_FakeNATSClient`, so the outbox can be exercised
  without a broker.

### What this layer cannot catch

Anything that depends on real Postgres, on JetStream semantics, or on the daemon
behaving like the host it runs on. Those belong to the next layer.

## End to end: the loop against a real stack

`scripts/e2e/run.sh` starts the backend, the workspace daemon and a fake LLM
proxy against a Postgres and a NATS with JetStream that you provide
(`docker compose up -d postgres nats`), then drives one company through a whole
sprint and checks each step **from the outside, through the API**.

The model is replaced by `scripts/e2e/fake_llm.py`, which speaks the proxy's
`/v1/chat/completions` shape and answers by the prompt's section headers. That
makes the run deterministic without weakening it: the fake sends work back for
rework, approves on the second pass, blocks a task that fails the text checks,
and appends every prompt it receives to a file so the run can assert what the
agents were actually told.

What the run asserts includes the parts a unit test cannot reach: the blocked
task reaching the inbox, the sprint closing anyway with its retrospective, a
procedure being proposed and adopted, and the reviewer reading the exact head
commit.

It recreates its own `factory_e2e` database and wants `E2E_PYTHON` pointing at
the backend virtualenv interpreter.

## The verifier benchmark

The product claims that verification is independent of the producer. The
benchmark in `backend/eval/verificador/` measures that claim instead of
asserting it: 14 cases, each a claim plus the evidence a daemon would report,
fed to the real `verify_agent_run` against an in-memory database, so the stored
`Verification` and the audit write are exercised too. No model is called.

Twelve cases are lies that must never come back `verified`; two are honest
deliveries that must, because a benchmark that only proves the verifier says no
would score 100% by rejecting everything. Current output:

```
lies not believed:  12/12
cases as specified: 14/14
stored:             14 verification(s), one audit row each
```

Each case declares the verdicts it accepts and, where the point is the
classification, the risk tier it expects. A case that reaches `verified` when
its own list does not allow it is printed as **BELIEVED A LIE** and fails the
run, so the number cannot drift upward unnoticed.

The benchmark's evidence is currently synthetic: `SENT_SHA = "a" * 64`, a literal
diff, and a write status (`refused`) the daemon never emits. It therefore proves
the comparisons are wired, not that a hash from disk matches bytes sent. It is
the honest limit of the layer, and
[`docs/verificador-sandbox.md`](docs/verificador-sandbox.md) plans to replace the
evidence with output from the real daemon.

## Frontend: Vitest and Testing Library

49 test files and 388 tests under `frontend/src`, in about 35 seconds. The API
is mocked at the module boundary in `services/api.ts`, so a component test never
needs a backend.

The i18n gates are tests, not conventions: `src/i18n/coverage.test.ts` fails when
a key passed to `t('…')` is missing from a locale, and `src/i18n/locales.test.ts`
requires `en.json` and `es.json` to have identical leaf key sets. Together with
`eslint-plugin-i18next`'s `no-literal-string`, that makes a hardcoded English
string a build failure rather than a review comment.

## CI

Workflows live in `.github/workflows/` and mostly run on the self-hosted
`[self-hosted, linux, x64, lafabrique]` runner.

| Workflow | Runs | Gates |
|---|---|---|
| `backend-tests.yml` | ruff (0.17.1, pinned), mypy, pytest, then the llm-proxy suite | ruff and pytest block; mypy is `|| true` |
| `frontend-build.yml` | `npm ci`, tsc, lint, tests, build | all block |
| `e2e.yml` | the loop against pgvector/Postgres and NATS | blocks |
| `security-scan.yml` | Trivy over the filesystem, `safety` in the backend | Trivy blocks; `safety` does not |
| `security-baseline.yml` | pinned Trivy security baseline | blocks |
| `file-sizes.yml` | the size ratchet | blocks |
| `docker-build.yml` | the images build | blocks |
| `informe-estado.yml` | the status report | manual |

There is no `frontend-tests.yml` and no prettier step: nothing formats
TypeScript, because prettier is not a dependency.

Feature-branch pushes do **not** trigger the test workflows. Push a pull request
to get a run, and note that the backend and frontend jobs are path-filtered: a
documentation-only pull request starts almost nothing, which is not the same as
passing.

## What is not tested, and why

Saying this out loud is cheaper than a reviewer discovering it.

| Gap | Reality |
|---|---|
| Browser automation | No Playwright, no Cypress. Nothing drives the real DOM. The frontend is covered by component tests and by the build gates. |
| Network-level API mocking | No msw. Mocks live in `services/api.ts`. |
| LLM output quality | No deepeval, no LLM-as-judge. `unverifiable` is a verdict, not a score, and no model grades a claim — that is a design rule, not a backlog item. |
| Coverage numbers | No coverage tool is installed and none is reported. The suite asserts behaviour; a percentage would not add a fact. |
| Load and concurrency | No locust, no k6. Latency and throughput under load are unmeasured. |
| Host portability | The daemon is tested on Linux. Windows path handling is covered only by unit tests. |

## Adding a test

1. Put it where the failure would surface: a predicate in `backend/tests`, a
   route through `test_client`, a whole loop in `scripts/e2e`.
2. Prove it bites. Break the code under test, watch the test fail, restore it.
   A test you have never seen fail is a comment.
3. If the change is a fix, write the test against the bug first.

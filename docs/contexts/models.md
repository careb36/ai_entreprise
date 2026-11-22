# The models context

Which model a role runs and what it costs: the catalogue and prices, the
company's provider connections and model choices, difficulty routing, the
usage and calibration reports, and the LLM proxy that actually calls the
providers. Read this before changing `backend/app/services/models/` or
`llm-proxy/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/models/` (and `llm-proxy/`, outside the backend)
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md).
- **Interface:** `company_models`, `edit_format`, `effort`, `model_factory`, `model_profile`, `model_routing`, `run_cost`.
- **Modules:** `bench_schema`, `bench_schema_blocks`, `bench_schema_edit_format`, `bench_schema_swebench`, `bench_scorecards`, `calibration`, `company_models`, `difficulty`, `edit_format`, `effort`, `model_catalog`, `model_factory`, `model_prices`, `model_profile`, `model_record`, `model_routing`, `model_suggestions`, `prompt_weight`, `provider_client`, `provider_connections`, `routing_policy`, `routing_record`, `run_cost`, `thinking`, `usage_csv`, `usage_report`.
- **HTTP adapters:** `model_catalog` (the models.dev fetch), `provider_client` (a provider's model list, raising `ProviderUnreachable`). Only these import an HTTP library; they return data and raise named errors.

## How it works

The backend never calls a provider. Every model call goes through
`backend/app/llm_proxy_client.py` to the LLM proxy, which enforces each
company's token budget and routes by model-id prefix. Which model a call
names is decided here, most specific first: the agent's own `model`, the
company's model for the role, the company's default, then the deployment's
(`AGENT_MODEL_<ROLE>`, `NEMOTRON_ROLES`, `AGENT_MODEL`, then
`ModelFactory.ROLE_MODEL_MAP`). Difficulty routing refines only the
deployment's default. What a call cost is read back from the audit log's turn
rows.

## Rules

- A model a person chose (agent or company) is never re-routed.
- Token counts: only `reported` usage is priced. An estimate is counted apart,
  charged to the quota, and never shown as usage or priced. An unknown cost is
  `null` with a reason, never 0. A known provider that does not price a model
  is `price_unknown`, never another provider's price.
- A call is charged the rate in force when it was recorded: off-peak (half,
  by the provider's published hours and holiday calendar, the same as the run
  window's preset) or peak. A call without a time is charged the peak rate,
  so a report never under-states. `off_peak_turns` counts the ones charged
  half.
- An empty answer is a named error, never a success; the reasoning is never
  returned as the answer.
- The proxy sends a model only when the provider's live list has it; an
  unknown id is refused (`unknown_model`), never replaced.
- Every call names its thinking mode; no call is left to the provider's
  default.
- A company connection's key and headers are encrypted at rest and never
  serialized; it has no fallback to the deployment's providers.
- Suggestions are never applied on their own. The routing record, the
  calibration report and the bench are observations; calibration signals are
  information only and never change weights, bands or a policy.
- A model profile's limits stay under `CEILINGS`; only the deployment's
  settings set a profile, nothing an agent can reach. A malformed override is
  ignored with a warning rather than lifting a limit.
- What a model says about its edit format is a claim and is never read.
- DeepSeek's API processes requests in China: for personal or client data
  that is a third-country transfer under the GDPR
  ([configuration](../CONFIGURATION.md), `DEEPSEEK_API_KEY`).

## Components

### The default team

The default team runs on DeepSeek alone, so a deployment needs only
`DEEPSEEK_API_KEY`. `model_factory.py` holds the role map: PO, Architect,
SecOps and the Reviewer agent on DeepSeek Pro (`deepseek-v4-pro`); every other
role on DeepSeek Flash (`deepseek-flash`). Set `AGENT_MODEL_<ROLE>` or
`AGENT_MODEL` to an id `GET /v1/models` lists to change it.

### The LLM proxy client

`backend/app/llm_proxy_client.py`: `chat()` / `chat_async()` with a `model`,
a `role` and an optional `bu_id`. It waits `LLM_UPSTREAM_TIMEOUT_SECONDS`
plus 15 s; only a request that never reached the proxy is sent again.

### The LLM proxy

A Flask gateway (`llm-proxy/app.py`, started by gunicorn as `app:app`). Every
module is copied by name in `llm-proxy/Dockerfile`, so a new one is added
there too.

- `app.py`: configuration, the live catalogues and their hourly refresh, the
  routes (`/health`, `/v1/providers`, `/v1/models`, `/v1/chat/completions`,
  `/v1/embeddings`).
- `routing.py`, `catalogue.py`: which provider and model serve an id, and the
  fallback on 429/5xx within the request's time budget.
- `providers.py`, `upstream.py`, `gemini.py`: one chat completion per
  provider; `UpstreamError` names the failure (`upstream_timeout`,
  `empty_content`, `empty_content_reasoning_exhausted`).
- `thinking.py`: DeepSeek's `thinking` field, dropped for other providers;
  `LLM_THINKING` overrides.
- `inline_tool_calls.py`: a tool call DeepSeek wrote as text in `content` is
  read back into `tool_calls`, or retried once when the request offered no
  tools.
- `timeouts.py`, `gunicorn.conf.py`: one budget, `LLM_UPSTREAM_TIMEOUT_SECONDS`,
  enforced as a deadline on the whole body.
- `connections.py`: a company's own connection, honoured only with
  `LLM_PROXY_INTERNAL_TOKEN`.
- `usage.py`: `reported` or `estimated`. `quota.py`: per-business-unit token
  quotas (429 at the limit, `X-Quota-Warning` from 80 %).
- `provider_keys.py`, `provider_status.py`: whether each key works, observed
  at startup and on refresh, never logging the key; a placeholder key is
  `not_configured` and never sent.
- `embedding.py`, `local_model.py`, `embedding_model.json`,
  `fetch_embedding_model.py`: embeddings from a local model pinned by sha256,
  fetched at image build time, never at run time.
- `telemetry.py`: OpenTelemetry, exported only when configured.

### Thinking mode

`thinking.py` (backend) is the one policy: off for structured calls (an answer
that is parsed) and the EngramProcessor; on for free-form calls of PO,
Architect, SecOps, the developer roles and Designer, with
`LLM_THINKING_RESERVE_TOKENS` added to `max_tokens`; off otherwise. A
structured call that ran out of budget reasoning is retried once with thinking
off.

### Model routing

- `company_models.py`: the company's default and per-role models
  (`conn:<id>:<model>` must be the company's).
- `difficulty.py`: a pure estimate from what is known before a task runs
  (criteria, modules, files, dependencies, attempt, rework), banded `low`,
  `medium`, `high`; never asked of a model.
- `routing_policy.py`: bands to models, the company's or `AGENT_MODEL_POLICY`.
  An unreadable policy or band is dropped with a warning.
- `model_routing.py`: `route`, `estimate_task`, `effective_policy`. The loop
  stores the estimate and routing as an `estimated` event.
- `routing_record.py`: completed sprints split by band, model and policy, so
  a policy is kept or reverted on numbers.
- `model_suggestions.py`, `model_record.py`: one suggestion per role by a
  fixed rule over the catalogue, decided by the company's own record once a
  candidate has run three tasks in a role.
- `model_catalog.py`: the models.dev catalogue (a description, not evidence of
  what a provider serves). `provider_connections.py`: a company's connections
  and the check that a key works.

### A model's profile

`model_profile.py`: what a model can take and return and the Developer's
limits for it, each field from the first source that has it: `MODEL_PROFILES`
overrides, the catalogue, then the settings and defaults. `sources` names who
decided each field; the run keeps the profile in its evidence.

### The edit format per model

`edit_format.py`: `EDIT_FORMAT_BY_MODEL` maps a model to `auto`, `whole` or
`search_replace`; the run's evidence records the format asked for and what the
backend observed in the answer.

### What a run, a task and a sprint cost

`run_cost.py` sums the turn rows into tokens and unrounded cents per run, task
and sprint. `model_prices.py` reads prices from the catalogue, with
`PUBLISHED_PRICES` for DeepSeek where models.dev lags. Routes:
`GET /v1/agent_runs/{id}/cost`, `/v1/tasks/{id}/cost`,
`/v1/sprints/{id}/cost` (Viewer).

### Who used what: the usage report

`prompt_weight.py` adds up the prompt measures the worker recorded (never
measures again). `usage_report.py` serves usage by period, person, team,
role, producer and model, every number read back from the records
(`GET /v1/companies/{id}/usage`, Manager). `usage_csv.py` writes the CSV
export, audited, with cells guarded against CSV injection. Design:
[`TEAMS_AND_USAGE.md`](../archive/design/TEAMS_AND_USAGE.md).

### The bench's scorecards: the /bench page

`bench_scorecards.py` reads the committed `bench/scorecards/*.json` read-only
and serves them to any signed-in caller. `bench_schema.py` (with
`bench_schema_blocks`, `bench_schema_swebench`, `bench_schema_edit_format`) is
a copy of `scripts/bench/schema.py`, kept in step in the same pull request,
since the backend never imports the bench's scripts. A card is `valid`,
`invalid` or `unreadable`, never dropped; an unmeasured metric stays
`{"measured": false, "reason"}`, never 0.

### Calibration: the estimate scored against observed effort

`effort.py` observes what a task took (tokens, wall time, rework, files the
daemon reported) at sprint close and stores it as an `effort_observed` task
event, never overwritten. `calibration.py` scores the first `estimated` event
against it (rework share, token spread, Spearman) and names `signals`, with
`information_only: true`. Changing the estimator means a new
`difficulty.VERSION`, scored by the same report, never auto-tuned.

## See also

- `LLM_PROXY_INTERNAL_TOKEN`, `MODELS_CATALOG_URL`, the provider keys: [configuration](../CONFIGURATION.md).
- Architect and editor mode (`services/loop/edit_plan.py`) and the run window: [loop](loop.md#the-run-window).
- The proxy's tests and the bench: [testing](../TESTING.md).

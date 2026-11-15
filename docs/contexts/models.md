# The models context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/models/`.

- **Folder:** `backend/app/services/models/`
- **Responsibility:** Which model a role runs and what it costs: the models.dev catalogue, prices, the company's provider connections and model settings, difficulty routing, the suggestions per role and the records over completed sprints, and, after the migration, the role defaults from backend/llm/model_factory.py. It never calls a provider; the LLM proxy does.
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md) (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Modules:** `calibration`, `company_models`, `difficulty`, `effort`, `model_catalog`, `model_factory`, `model_prices`, `model_record`, `model_routing`, `model_suggestions`, `provider_client`, `provider_connections`, `routing_policy`, `routing_record`, `run_cost`.
- **HTTP adapters** (`http_adapters` in `docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`, issue #511): `model_catalog` (the models.dev fetch) and `provider_client` (`list_models`, raising `ProviderUnreachable`; `provider_connections.validate` calls it). Only these modules of the context may import an HTTP library; each returns data and raises named errors, and the rest of the context calls it.

## Components

### The LLM proxy client

**`backend/app/llm_proxy_client.py`** — Thin HTTP client wrapping the LLM proxy for token-budget-aware requests. `chat()` / `chat_async()` take a `model` id (default `"gemini-pro"`), a `role`, and an optional `bu_id`.

### The LLM proxy

**`llm-proxy/app.py`** — Flask gateway that enforces per-company token budgets, routes by model-id prefix to the wired providers (Nebius Token Factory, Gemini, DeepSeek, Qwen), and refreshes each provider's catalogue hourly. Each answer says how its token count is known (`llm-proxy/usage.py`): `usage_status: reported` with the provider's figure in `usage`, or `estimated` with a characters/4 estimate apart in `usage_estimate` (charged to the quota, never shown as usage); the backend's `usage_status` keeps estimates out of the KPI averages.

The proxy is a set of sibling modules, each with one job; `app.py` is the entry point gunicorn starts (`app:app`) and every module is copied by name in `llm-proxy/Dockerfile`, so a new one is added there too.

| Module | Its job |
|---|---|
| `app.py` | The configuration (each provider's key, read with `os.getenv`, and URL; `LLM_UPSTREAM_TIMEOUT_SECONDS`), the live catalogue sets and their hourly refresh, and the HTTP routes (`/health`, `/v1/models`, `/v1/chat/completions`, `/v1/embeddings`). It passes this state to the modules below at call time, so a test that replaces a key, a catalogue set, a refresh or `track_usage_in_db` on `app` is seen everywhere. |
| `routing.py` | Which provider and model serve a requested id (prefix and catalogue, the deprecated Gemini aliases) and the fallback on 429/5xx: one catalogue model per other provider within the request's time budget. |
| `catalogue.py` | The provider prefixes, tier rules and deprecated ids; fetching one provider's model list (a failed refresh keeps the previous one); the `/v1/models` entries and the recommended orchestrator. |
| `providers.py` | One chat completion on a deployment provider: Gemini through `gemini.py`, the OpenAI-compatible ones (DeepSeek, Qwen, Nebius) directly. |
| `upstream.py` | The one POST every provider call goes through, and `UpstreamError`: what counts as a failure and whether another provider may be tried. |
| `gemini.py` | The Gemini request and answer shapes, tools included. |
| `connections.py` | A company's own provider connection, honoured only with `LLM_PROXY_INTERNAL_TOKEN`, with no fallback to the deployment's providers. |
| `usage.py` | How an answer's token count is known (`reported` or `estimated`). |
| `quota.py` | Per-business-unit token quotas in Postgres: the 429 once a unit has used its limit, `X-Quota-Warning` from 80 %, and charging each call. |
| `embedding.py` | `/v1/embeddings`: Gemini's embedding model when a key is set, else a deterministic local vector. |
| `provider_keys.py` | Asking each provider to authenticate at startup, so a bad key fails loudly. |
| `telemetry.py` | OpenTelemetry: spans for Flask and outbound `requests`, exported only when an OTLP endpoint is configured. |

### What a run, a task and a sprint cost

**`backend/app/services/models/run_cost.py`** / **`model_prices.py`** / **`token_usage.py`** / **`routers/costs.py`** — What a run, a task and a sprint cost, in tokens and US cents, after Aixle Flow's cost per session (Apache 2.0: one usage record per call as the only source, the cost summed from it, unrounded cents until shown, the models as a sorted set). The usage record is the audit log's KPI turn row (`execute_task`, `qa_review`, `architect_consult`, `interaction_id` `task-<id>`); no second ledger and no migration: `record_agent_turn` adds to `details` the provider's input and output tokens (`input_tokens`, `output_tokens`, only when the usage is `reported`), the estimate apart (`estimated_tokens`) and the proxy's `provider` (`token_usage.usage_fields`; `llm-proxy/gemini.py` now keeps Gemini's prompt/candidates split, thinking as output). `model_prices.price_for` reads the price per million input and output tokens from the model catalogue: the provider the proxy named (`gemini`→`google`, `qwen`→`alibaba`…) or a company connection's models.dev provider, else a `<provider>/<model>` id, else the one price every provider listing the model agrees on (`price_ambiguous` when they differ); a known provider that does not price the model is `price_unknown`, never another's price. Only `reported` turns are priced; estimated tokens are counted apart and never enter the cents; a reported turn without a price or without its split (rows from before the split: `token_split_unknown`) makes `cost_cents` `null` with the `reason`, never 0, and with no turns or no reported usage it is `null` too (`no_turns`, `no_reported_usage`). A task's turns go to the first version of its task-worker run ingested at or after them (the turns after the latest version, its QA review, to the latest); a task's cost is all its turns, a sprint's all its tasks'. `GET /v1/agent_runs/{id}/cost?company_id=`, `GET /v1/tasks/{id}/cost` (with `runs`), `GET /v1/sprints/{id}/cost` (with `tasks`), all Viewer and company-scoped, and `cost` on each sprint of `GET /v1/sprints`: `{turns, reported_turns, estimated_turns, unavailable_turns, reported_tokens, input_tokens, output_tokens, estimated_tokens, cost_cents, reason, unpriced_models, models, price_source}`. The run page, the task page and the sprint list show it (`components/CostSummary.tsx`, `services/cost.ts`: currency such as `$0.42`, "price unknown" with the reason, estimated tokens labelled apart).

### Model routing

Every LLM call goes through the proxy client (`backend/app/llm_proxy_client.py`).
`LLMProxyClient.chat()` / `chat_async()` take a `model` id (default
`"gemini-pro"`), a `role`, and an optional `bu_id`. There is no
`model_preference` parameter: the `fast` / `powerful` tiers are an internal
fallback in the proxy (`_PREFERENCE_TIERS`). Role→model defaults live in
`backend/app/services/models/model_factory.py` (`ModelFactory.ROLE_MODEL_MAP`); every caller
reads them through `default_model_for_role` → `ModelFactory.model_for_role`,
which applies, most specific first, `AGENT_MODEL_<ROLE>` (for BackendDeveloper,
FrontendDeveloper, DevOps and DataEngineer, then `AGENT_MODEL_DEVELOPER`; their
map default is the Developer's, the Designer's the deciding roles'), `NEMOTRON_ROLES`,
`AGENT_MODEL` (one model for the whole team, e.g. DeepSeek alone), then the
map. Above those sit the company's own choice and the agent's: an agent's
`model` (the Agents page) wins, then the company's model for the role
(`Company.role_models`), then its default (`Company.default_model`;
`services/models/company_models.py`, `GET`/`PUT /v1/companies/{id}/model_settings`,
Viewer / Manager). The ScrumMaster, the PO, the Architect, the task worker's
Developer, Architect consultation and QA review, and the Reviewer agent (its
own entry, else QA's) all read it; difficulty routing (`AGENT_MODEL_POLICY`)
refines only the deployment's default, never a model a person chose. A model
may be one of the company's provider connections (`conn:<id>:<model>`).
Difficulty routing (issue #319; taken as design from OpenHands' LLM
configuration per agent, a cheaper model for routine steps, and Factory's
model choice per droid and task). **The estimate**
(`services/models/difficulty.py`) is a pure function of what is known before
a task runs, never asked of a model: the criteria it covers, the approved
architecture's modules covering them and the paths they map to, the tasks it
waits for, the attempt (deliveries so far plus one), and the `NEEDS WORK:`
requests and a person's send-backs on its board. Each input adds a fixed
weight (base 1; criteria up to 3; modules up to 2; three files or more +1;
two dependencies or more +1; any rework +1; a send-back +1), clamped to 1..5,
banded `low` (1-2), `medium` (3) or `high` (4-5), and returned as `{score,
band, inputs, weights, version}`; without architecture or dependencies it is
the score routing always used (`model_routing.estimate_difficulty`, which the
Architect/editor threshold reads). **The policy**
(`services/models/routing_policy.py`) maps bands (`low`/`medium`/`high`, or
score ranges such as `"1-2"`) to a model, a connection model or a proxy tier,
with an optional `label`: the company's (`Company.model_policy`, migration
0023, written by a Manager through `PUT /v1/companies/{id}/model_settings`
with `model_policy`, checked strictly: a bad band or a connection not the
company's is a 422; omitted, it is kept; `null` clears it; the Models page's
"Model by task difficulty" card, `components/RoutingPolicy.tsx`), else the
deployment's `AGENT_MODEL_POLICY`. A policy that cannot be read is ignored
with a warning and a band that cannot be read dropped with one, so the role
default stays; a tier name runs on the role default (the proxy serves no model
by that name) and the routing says so. **The choice**
(`model_routing.route`) returns the model to request with the estimate and
`routing: {policy, source, band, requested, chosen, reason}` (`configured`: a
person's choice, never re-routed; `no_policy`, `no_band`, `band`,
`tier_role_default`, `editor_plan`); `resolve_agent_model` is its model. The
task worker calls it through `services/loop/task_routing.py`, which stores the
estimate and the routing on the task as an `estimated` event and on the run's
evidence (`difficulty`, `routing`); the served model stays `AgentRun.model`,
the proxy's report. Whether it pays is counted, never claimed:
`services/models/routing_record.py` splits the company's completed sprints by
the difficulty each task's first attempt was routed by (its first `estimated`
event; a task from before the event is recomputed from its `covers`) and the
model the proxy reported for its last delivery, with tasks, done, done at the
first attempt (done, no `NEEDS WORK:`, no person's send-back, at most one
delivery), rework per task, blocked share, the audit log's tokens of the
turns whose usage the provider reported (estimated or unavailable turns are
counted apart, never added) and the cost in cents (`run_cost`; a task whose
cost is unknown is counted apart, never as 0), and sums the same numbers
`by_difficulty_band`, `by_model` and `by_policy` (the label each task ran
under), so a policy is kept or reverted on numbers, the way procedures are;
`GET /v1/companies/{id}/routing_record` (Viewer, `routers/model_settings.py`)
serves it with the effective policy's band beside each row
(`policy_label`, `policy_source`), and the Sprints page shows it beside the
learning tiles (`components/RoutingRecord.tsx`, the split view
`components/RoutingSplit.tsx`, `services/routing.ts`). The bench records the
same split per specification (below). **Architect and editor mode**
(`services/loop/edit_plan.py`, taken from Aider's architect/editor split), off
unless `ARCHITECT_EDITOR_MODE=true`: a Developer task whose difficulty is at
least `ARCHITECT_EDITOR_MIN_DIFFICULTY` (4) consults the Architect once per
attempt, asking for an edit plan in a fixed format (`EDIT PLAN`, numbered
`<relative path> — <what changes>` steps, `END`; `parse` reads nothing else
and refuses an absolute or `..` path), and the Developer gets a readable plan
as `## Edit plan to apply` and runs on the policy's cheapest band model
(`model_routing.cheapest_model`, unless a person chose its model); an answer
without a plan stays the Architect's guidance on the routed model. The plan is
a claim, prompt context, never evidence.
`GET /v1/companies/{id}/model_suggestions` (Viewer, `services/models/model_suggestions.py`)
suggests one per role by a fixed rule over the catalogue (the deciding roles: a
model that reasons and calls tools, largest context, then cheapest; Developer, QA
and the Reviewer: tools, largest context; TechWriter: the cheapest with tools),
only among what a validated provider listed, never applied on its own. Once
the company has run a candidate on at least three of its tasks in a role, more
done than not, its own record decides that role (`services/models/model_record.py`:
the model of each task's last delivery, done and rework from the boards of
completed sprints; least rework per task first), and the suggestion carries
the numbers (`measured`). The
company's **Models** page (`/company/models`, `pages/Models.tsx`,
`components/ModelConnections.tsx`, `components/ModelChoices.tsx`) is where a
Manager connects, validates and removes providers and picks the default and
per-role models with the suggestions beside them; a Viewer reads it.

### Calibration: the estimate scored against observed effort

Issue #534. The difficulty estimate above is a prediction; nothing checked it
until this. Taken from Plane, design only (AGPL-3.0-only, commit
`290383d7f92fa8995ea2adbd12442220b3b9b525`): its estimates
(`db/models/estimate.py`, an estimate stored on the issue) and the points
variant of `burndown_plot` (`utils/analytics_plot.py`, progress read against
the estimate). Plane has no observed side; the observation and the scoring
are ours.

- **The prediction** is the task's first `estimated` event (no second
  estimator). A later `estimated` event, a rework attempt's or a person's
  change, is a new event in the chain and never replaces the one scored; a
  task from before #319 is recomputed from its plan and counted as
  `predicted_recomputed`.
- **The observation** (`services/models/effort.py`) is read by the backend
  from the task's own records when its sprint closes
  (`services/loop/worker/close.py`, `_observe_effort`, after `sprint.closed`;
  a failure is logged and never stops the close): `tokens`, the audit log's
  turns the proxy reported (`tokens: reported`); when any turn was estimated
  or unavailable, or there was no turn, `tokens` is `null` with
  `tokens_unknown_reason` (`unreported_turns`, `no_turns`) and the reported
  part apart in `tokens_reported`, never 0; `wall_seconds`, the first
  `claimed` event to the last event that left the task `done`; `deliveries`
  and `rework_cycles` (each delivery after the first); `rework_requests`
  (`reviewed` events with a `needs_work` verdict); `send_backs` (a person's);
  `review_failures`; `files_written`, the distinct paths the daemon reported
  writing on each run's latest verification with `workspace_writes` (`null`
  without any such report; a run's `claims.files` is never read). Beside it,
  the attribution: the last `AgentRun`'s role (else the assignee), producer
  and the model the proxy reported. It is stored as an **`effort_observed`
  task event** (actor `system`, no comment, no change to the task's state:
  `task_projection._ADVICE_KINDS`), not a table: the observation belongs to
  the task's record, and the event chain is append-only, hashed and verified,
  so it is never overwritten. A second close after a reopening writes a new
  event only when the observation changed; readers take the latest. No
  migration: `task_events.event_type` is free text checked in Python.
- **The report** (`services/models/calibration.py`, pure apart from
  `record`) is deterministic, over the company's completed sprints, overall
  and `by_predicted_band`, `by_role`, `by_producer`, `by_model` and
  `by_sprint_id`: `over_one_rework_share` (tasks with more than one rework
  cycle; for `low`, the easy tasks that needed repeated rework),
  `tokens_spread` (min, quartiles by linear interpolation, max) over the
  tasks whose tokens are known with `tokens_unknown` counted apart,
  `spearman` (predicted score against observed tokens, average ranks for
  ties; `null` under three tasks with known tokens or without variance),
  `off_by_two_bands_share` (predicted `low` and observed `high`, or the
  reverse; the observed band is the task's tercile by the average rank of
  its known tokens among every task in the report, unknown tokens having
  none), the median wall time and files written with their unknown counts. A
  task closed before the event existed is read from its records at request
  time (`observed_computed`). `GET /v1/companies/{id}/calibration` (Viewer,
  `routers/model_settings.py`) serves it, and the Models page shows it under
  the routing policy (`components/CalibrationReport.tsx`,
  `services/calibration.ts`).
- **Routing reads it only as information.** `signals` names what the numbers
  suggest, each with a floor of five tasks: `low_band_reworked` (a quarter or
  more of the `low` tasks needed more than one rework), `high_band_not_costlier`
  (the `high` band's median tokens not above the `low` band's) and
  `no_rank_agreement` (Spearman at or below 0). Nothing changes the
  difficulty weights, the band ranges (`difficulty.BAND_RANGES`) or a policy
  from them (`information_only: true`). How the thresholds could adjust,
  later and by a person's decision or a procedure measured like the others:
  a persistent `low_band_reworked` suggests moving score 2 into `medium`
  (narrowing `low` to 1); `high_band_not_costlier` suggests raising the
  `high` floor to 5 or weighting modules and files more; a rank correlation
  that stays at or below 0 across sprints says the weights themselves do not
  predict effort and a new `difficulty.VERSION` is due. Any such change would
  be a new estimate version, scored by this same report before and after,
  never auto-tuned.
- **Not in the bench or the procedures.** The bench's `scorecard.py` is at
  the 600-line cap; the report can be read there through the same endpoint in
  a later change. The procedures round keeps reading rework and blocked share
  (`services/learning/procedures.py`); calibration measures the estimator,
  not a procedure, so it is not one of their metrics.

Token budgets are enforced per company at the proxy layer — the backend never calls a model provider directly.

("The bench" that this text points at is described in [testing](../TESTING.md).)

## Described elsewhere

- Architect and editor mode (`services/loop/edit_plan.py`) is described in the model routing text below; the module belongs to [loop](loop.md).
- `LLM_PROXY_INTERNAL_TOKEN`, `MODELS_CATALOG_URL` and the provider keys: [configuration](../CONFIGURATION.md).
- The proxy's own test suite and the tests of the catalogue, connections, suggestions and routing record: [testing](../TESTING.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-4).

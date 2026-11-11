# The models context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/models/`.

- **Folder:** `backend/app/services/models/`
- **Responsibility:** Which model a role runs and what it costs: the models.dev catalogue, prices, the company's provider connections and model settings, difficulty routing, the suggestions per role and the records over completed sprints, and, after the migration, the role defaults from backend/llm/model_factory.py. It never calls a provider; the LLM proxy does.
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md) (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Modules:** `company_models`, `model_catalog`, `model_factory`, `model_prices`, `model_record`, `model_routing`, `model_suggestions`, `provider_connections`, `routing_record`, `run_cost`.

## Components

### The LLM proxy client

**`backend/app/llm_proxy_client.py`** — Thin HTTP client wrapping the LLM proxy for token-budget-aware requests. `chat()` / `chat_async()` take a `model` id (default `"gemini-pro"`), a `role`, and an optional `bu_id`.

### The LLM proxy

**`llm-proxy/app.py`** — Flask gateway that enforces per-company token budgets, routes by model-id prefix to the wired providers (Nebius Token Factory, Gemini, DeepSeek, Qwen), and refreshes each provider's catalogue hourly. Each answer says how its token count is known (`llm-proxy/usage.py`): `usage_status: reported` with the provider's figure in `usage`, or `estimated` with a characters/4 estimate apart in `usage_estimate` (charged to the quota, never shown as usage); the backend's `usage_status` keeps estimates out of the KPI averages.

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
Difficulty routing (`services/models/model_routing.py`): `estimate_difficulty` is a
1..5 function of the plan (criteria covered, the modules they map to, a
rework), and `AGENT_MODEL_POLICY` maps bands (`{"bands": {"1-2": …, "3-5":
…}}`) to models, a proxy tier name falling back to the role default.
Whether it pays is counted, never claimed: `services/models/routing_record.py`
splits the company's completed sprints by the difficulty each task was routed
by (recomputed from its stored `covers` with no rework, the first attempt's
input, so nothing new is stored and no migration was needed) and the model the
proxy reported for its last delivery, with tasks, done, done at the first
attempt (done, no `NEEDS WORK:`, no person's send-back, at most one
delivery), rework per task, blocked share and the audit log's tokens of the
turns whose usage the provider reported (estimated or unavailable turns are
counted apart, never added); `GET /v1/companies/{id}/routing_record` (Viewer,
`routers/model_settings.py`) serves it with the policy's band beside each
row, and the Sprints page shows it beside the learning tiles
(`components/RoutingRecord.tsx`, `services/routing.ts`). The bench records the
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

Token budgets are enforced per company at the proxy layer — the backend never calls a model provider directly.

("The bench" that this text points at is described in [testing](../TESTING.md).)

## Described elsewhere

- Architect and editor mode (`services/loop/edit_plan.py`) is described in the model routing text below; the module belongs to [loop](loop.md).
- `LLM_PROXY_INTERNAL_TOKEN`, `MODELS_CATALOG_URL` and the provider keys: [configuration](../CONFIGURATION.md).
- The proxy's own test suite and the tests of the catalogue, connections, suggestions and routing record: [testing](../TESTING.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-4).

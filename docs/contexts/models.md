# The models context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/models/`.

- **Folder:** `backend/app/services/models/`
- **Responsibility:** Which model a role runs and what it costs: the models.dev catalogue, prices, the company's provider connections and model settings, difficulty routing, the suggestions per role and the records over completed sprints, and, after the migration, the role defaults from backend/llm/model_factory.py. It never calls a provider; the LLM proxy does.
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md) (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Interface** (`public` in `docs/architecture/contexts.json`, ADR 0018, checked by `scripts/check_architecture.py`): `company_models`, `edit_format` (the edit format per model and the format a delivery was observed in; `loop/worker` reads it), `effort`, `model_factory`, `model_profile` (a model's profile; `loop/worker/execute` reads the Developer's limits from it), `model_routing`, `run_cost` (the reported tokens and cost of a task, a run or a sprint; `management/rsi_cycle` reads a cycle sprint's tokens through it). Another context imports only these modules and the package; the rest are internal. Nothing is allowlisted: `loop/task_routing` gets a task's estimate from its plan and board (`model_routing.estimate_task`, the architecture passed in) and the company's policy (`model_routing.effective_policy`) through `model_routing`, so `difficulty` and `routing_policy` stay internal.
- **Modules:** `bench_schema`, `bench_schema_blocks`, `bench_schema_edit_format`, `bench_schema_swebench`, `bench_scorecards`, `calibration`, `company_models`, `difficulty`, `edit_format`, `effort`, `model_catalog`, `model_factory`, `model_prices`, `model_profile`, `model_record`, `model_routing`, `model_suggestions`, `prompt_weight`, `provider_client`, `provider_connections`, `routing_policy`, `routing_record`, `run_cost`, `thinking`, `usage_csv`, `usage_report`.
- **HTTP adapters** (`http_adapters` in `docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`, issue #511): `model_catalog` (the models.dev fetch) and `provider_client` (`list_models`, raising `ProviderUnreachable`; `provider_connections.validate` calls it). Only these modules of the context may import an HTTP library; each returns data and raises named errors, and the rest of the context calls it.

## Components

### The LLM proxy client

**`backend/app/llm_proxy_client.py`** — Thin HTTP client wrapping the LLM proxy for token-budget-aware requests. `chat()` / `chat_async()` take a `model` id (default `DEFAULT_MODEL`, `"deepseek-flash"`), a `role`, and an optional `bu_id`. Every request names its thinking mode (see **Thinking mode** below; `client.structured = True` for a call whose answer is parsed). It waits `LLM_UPSTREAM_TIMEOUT_SECONDS` plus 15 s; only a request that never reached the proxy (`proxy_unreachable`) is sent again, while a read timeout (`proxy_read_timeout`) or a dropped connection (`proxy_connection_lost`) comes back as a named error.

### The LLM proxy

**`llm-proxy/app.py`** — Flask gateway that enforces per-company token budgets, routes by model-id prefix to the wired providers (Nebius Token Factory, Gemini, DeepSeek, Qwen), and refreshes each provider's catalogue hourly. Each answer says how its token count is known (`llm-proxy/usage.py`): `usage_status: reported` with the provider's figure in `usage`, or `estimated` with a characters/4 estimate apart in `usage_estimate` (charged to the quota, never shown as usage); the backend's `usage_status` keeps estimates out of the KPI averages.

The proxy is a set of sibling modules, each with one job; `app.py` is the entry point gunicorn starts (`app:app`) and every module is copied by name in `llm-proxy/Dockerfile`, so a new one is added there too.

| Module | Its job |
|---|---|
| `app.py` | The configuration (each provider's key, read with `os.getenv`, and URL; `LLM_UPSTREAM_TIMEOUT_SECONDS`), the live catalogue sets and their hourly refresh, and the HTTP routes (`/health`, `/v1/providers`, `/v1/models`, `/v1/chat/completions`, `/v1/embeddings`). It passes this state to the modules below at call time, so a test that replaces a key, a catalogue set, a refresh or `track_usage_in_db` on `app` is seen everywhere. |
| `routing.py` | Which provider and model serve a requested id (prefix and catalogue, the deprecated Gemini aliases) and the fallback on 429/5xx: one catalogue model per other provider within the request's time budget. |
| `catalogue.py` | The provider prefixes, tier rules and deprecated ids; fetching one provider's model list (a failed refresh keeps the previous one); the `/v1/models` entries and the recommended orchestrator. |
| `providers.py` | One chat completion on a deployment provider: Gemini through `gemini.py`, the OpenAI-compatible ones (DeepSeek, Qwen, Nebius) directly; a structured DeepSeek call whose reasoning exhausted the budget is retried once with thinking off. |
| `upstream.py` | The one POST every provider call goes through, read within the call's deadline, and `UpstreamError`: what counts as a failure, its named `reason` (`upstream_timeout`, `empty_content`, `empty_content_reasoning_exhausted`) and whether another provider may be tried. |
| `thinking.py` | DeepSeek's `thinking` field on the way out: explicit for DeepSeek, absent elsewhere, `LLM_THINKING` applied. |
| `timeouts.py` / `gunicorn.conf.py` | The upstream budget and the figures derived from it; gunicorn's settings (gthread workers, worker timeout) read from the environment at start. |
| `gemini.py` | The Gemini request and answer shapes, tools included. |
| `connections.py` | A company's own provider connection, honoured only with `LLM_PROXY_INTERNAL_TOKEN`, with no fallback to the deployment's providers. |
| `usage.py` | How an answer's token count is known (`reported` or `estimated`). |
| `quota.py` | Per-business-unit token quotas in Postgres: the 429 once a unit has used its limit, `X-Quota-Warning` from 80 %, and charging each call. |
| `embedding.py` | `/v1/embeddings` by `EMBEDDING_PROVIDER`: the local semantic model (`local`, the default), the deterministic hash vector (`local-hash`, and the stand-in when the model cannot load), or Gemini's embedding model when chosen and its key is set; every answer names its `model`, padded to 1536 values. |
| `local_model.py` / `embedding_model.json` / `fetch_embedding_model.py` | The local semantic model (issue #755): `intfloat/multilingual-e5-small` (MIT) as int8 ONNX, run with ONNX Runtime on CPU, never downloaded at run time. The manifest pins its revision and each file's sha256; the fetch script runs at image build time and fails on a mismatch; `local_model` checks the hashes again, loads once per process and runs a self-check in English and Spanish, and otherwise leaves the reason for `/health`. |
| `provider_keys.py` | Asking each provider whether its key works, at startup and on `GET /v1/providers?refresh=1` (5 s, at most once every 30 s): a one-token chat call for DeepSeek and Qwen (key and credit), a model listing for Gemini and Nebius (the key only). An empty key or an `.env.example` placeholder is `not_configured` and never sent; the proxy starts whatever the outcome, with one log line per provider and never the key. |
| `provider_status.py` | What the proxy last observed of each provider, as an observation: `status` (`ok`, `invalid_key`, `no_credit`, `rate_limited`, `unreachable`, `unexpected`, `not_configured`, or `not_observed`), `observed_at`, `http_status`, `source` (`startup`, `refresh`, `request`), `checked` (`key` or `key_and_credit`) and a redacted `detail`. A request refused for lack of credit or for its key is recorded, a served one records `ok`; the fallback decides as before (`upstream.CREDIT_PHRASES`). Served by `GET /v1/providers` and in `/health` as `providers`. |
| `telemetry.py` | OpenTelemetry: spans for Flask and outbound `requests`, exported only when an OTLP endpoint is configured. |

### What a run, a task and a sprint cost

**`backend/app/services/models/run_cost.py`** / **`model_prices.py`** / **`token_usage.py`** / **`routers/costs.py`** — What a run, a task and a sprint cost, in tokens and US cents, after Aixle Flow's cost per session (Apache 2.0: one usage record per call as the only source, the cost summed from it, unrounded cents until shown, the models as a sorted set). The usage record is the audit log's KPI turn row (`execute_task`, `qa_review`, `architect_consult`, `interaction_id` `task-<id>`); no second ledger and no migration: `record_agent_turn` adds to `details` the provider's input and output tokens (`input_tokens`, `output_tokens`, only when the usage is `reported`), the estimate apart (`estimated_tokens`) and the proxy's `provider` (`token_usage.usage_fields`; `llm-proxy/gemini.py` now keeps Gemini's prompt/candidates split, thinking as output). `model_prices.price_for` reads the price per million input and output tokens from the model catalogue: the provider the proxy named (`gemini`→`google`, `qwen`→`alibaba`…) or a company connection's models.dev provider, else a `<provider>/<model>` id, else the one price every provider listing the model agrees on (`price_ambiguous` when they differ); a known provider that does not price the model is `price_unknown`, never another's price. Only `reported` turns are priced; estimated tokens are counted apart and never enter the cents; a reported turn without a price or without its split (rows from before the split: `token_split_unknown`) makes `cost_cents` `null` with the `reason`, never 0, and with no turns or no reported usage it is `null` too (`no_turns`, `no_reported_usage`). A task's turns go to the first version of its task-worker run ingested at or after them (the turns after the latest version, its QA review, to the latest); a task's cost is all its turns, a sprint's all its tasks'. `GET /v1/agent_runs/{id}/cost?company_id=`, `GET /v1/tasks/{id}/cost` (with `runs`), `GET /v1/sprints/{id}/cost` (with `tasks`), all Viewer and company-scoped, and `cost` on each sprint of `GET /v1/sprints`: `{turns, reported_turns, estimated_turns, unavailable_turns, reported_tokens, input_tokens, output_tokens, estimated_tokens, cost_cents, reason, unpriced_models, models, price_source}`. The run page, the task page and the sprint list show it (`components/CostSummary.tsx`, `services/cost.ts`: currency such as `$0.42`, "price unknown" with the reason, estimated tokens labelled apart).

**Peak and off-peak prices** (issue #736). The catalogue's price per model is one price, read as the provider's peak rate: the conservative value. A provider that bills off-peak hours at less (DeepSeek: half the peak price outside 01:00–04:00 and 06:00–10:00 UTC on weekdays, weekends and Chinese public holidays in full, per its pricing page read 2026-11-19) is cheaper than the report says for work done off-peak; the cost report keeps showing what the catalogue prices and never guesses the hour's rate. A company that wants that work to start off-peak sets a run window ([loop](loop.md#the-run-window), preset `provider_off_peak:deepseek`). Time-of-day pricing in `model_prices` is a follow-up.

### Who used what: the usage report

**`backend/app/services/models/prompt_weight.py`** — Where a prompt's tokens go (issue #443, "Show"). The task worker measures every prompt it sends (`loop/worker/prompt_budget`) and keeps the measure on the run's evidence, its latest `Verification`, as `prompt_sections`. This module only adds those measures up, never measures again: per section its characters, estimated tokens (characters / 4, never the provider's count), share of the sections' tokens, the prompts that carried it and the prompts whose budget left it out, heaviest first, plus the number of prompts and the system prompt's tokens. The three cost routes carry it as `prompt_weight`: a run's prompt, a task's (every task-worker run version) and a sprint's (every task's). The task and run cost cards fold it under the cost, and each sprint on the Sprints page loads it on demand (`components/PromptWeight.tsx`).

**`backend/app/services/models/usage_report.py`** / **`usage_csv.py`** / **`routers/usage.py`** — The usage report (issue #512, phase 5; [`docs/design/TEAMS_AND_USAGE.md`](../design/TEAMS_AND_USAGE.md)). `GET /v1/companies/{id}/usage` (Manager, so a team can grant it) and `GET /v1/companies/{id}/usage.csv` (Manager, audited `usage_report_exported` with the range, period and generation time) take `since` and `until` (UTC dates, inclusive, at most 366 days; the last 30 days by default) and `period` (`day`, `week` from Monday, `month`), and return `totals` and rows `by_period`, `by_person`, `by_team`, `by_role`, `by_producer`, `by_model`, each with `tasks`, `rework`, `runs`, `verified`, `refuted`, `unverified`, `approvals`, `changes_requested`, `turns`, `reported_turns`, `estimated_turns`, `unavailable_turns`, `input_tokens`, `output_tokens`, `reported_tokens`, `estimated_tokens`, `cost_cents` and `cost_unknown_turns`. Every number is read back from the records: tasks created in the range (rework from their `NEEDS WORK:` requests; producer and model of their latest run), runs ingested in it (the model the proxy reported, the latest verdict of `verifications`), the LLM turns' audit rows priced by `run_cost.turn_cost` (only `reported` turns carry tokens and cents; estimated, unavailable and unpriced turns are `cost_unknown_turns`, never 0; `cost_cents` is `None` when no turn was priced) and people's reviews (`run_reviews` by `author_user_id`). A person is who started the task's sprint (`sprints.started_by_user_id`, from the session) or who reviewed; anything else is unattributed (`key: null`). A person's teams are those they were in at the fact's own observed time (`usage_report.teams_at` over `teams.membership_periods`, issue #702); work from before the history started (`teams.history_started_at`, migration `0052`) keeps today's teams (`teams.teams_of`), and `team_history` (`started_at`, `current_teams_cover`) and the CSV's `# teams:` row say which period that covers; a person in two teams counts in both. The same records and `now` give the same JSON and CSV (sorted keys, unattributed last, cents rounded to six places). `usage_csv.render` writes comment rows naming the company, range, period, time zone and generation time, then a header and one row per dimension and key, prefixing `'` to every text cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return (CSV injection). The **Usage** page (`/company/usage`, `pages/Usage.tsx`) shows it with the export.

### The default team

The default team runs on DeepSeek alone, through the proxy, so a deployment
needs only `DEEPSEEK_API_KEY` (`docs/CONFIGURATION.md`). Qwen, Gemini and
Nebius stay wired and optional: an agent, a role variable, a company choice or
a policy may still name their models.

| Role | Default model |
|---|---|
| PO, Architect, SecOps | DeepSeek Pro, `deepseek-v4-pro` (`PRO_MODEL`) |
| Reviewer agent (judges a run; reads QA's settings) | DeepSeek Pro (`REVIEWER_MODEL`) |
| ScrumMaster, Developer, QA, TechWriter, BackendDeveloper, FrontendDeveloper, DevOps, Designer, DataEngineer, EngramProcessor | DeepSeek Flash, `deepseek-flash` (`FLASH_MODEL`) |
| Manager agent (as the ScrumMaster), Support agent (as the TechWriter), the support chat, the discovery orchestrator's fallback, `LLMProxyClient`'s default | DeepSeek Flash |
| The proxy's recommended orchestrator (`/v1/models`, whatever the catalogue lists) | DeepSeek Flash |
| A suggested team in New company (`discovery_team.model_for_discovery_role`, the LLM and the rules alike) and the agent settings' recommendation | Each role's default above |

`tests/test_model_factory.py` pins each role's default and checks that no
default needs a key other than DeepSeek's.

**The model ids.** From DeepSeek's pricing page
(https://api-docs.deepseek.com/quick_start/pricing, read 2026-11-19):
`deepseek-flash` is DeepSeek-V4.1-Flash-1017 and `deepseek-v4-pro` is
DeepSeek-V4-Pro-0813; both have a 1M-token context, up to 384K output tokens,
JSON output and tool calls (vision: Flash only). The older names
`deepseek-v4-flash` and `deepseek-v4-flash-vision-exp` are still accepted but
retired: DeepSeek serves them with DeepSeek-V4.1-Flash-1017 at the Flash price,
and `model_prices` prices `deepseek-v4-flash` as `deepseek-flash`. The proxy
sends a model only when DeepSeek's live `/models` list has it
(`llm-proxy/routing.py`, `resolve`); an id missing there is refused as
`unknown_model`, never replaced, so if DeepSeek ever drops a name, set
`AGENT_MODEL_<ROLE>` (or `AGENT_MODEL`) to an id `GET /v1/models` lists.

**Thinking mode** (issues #739, #748, and the fix that followed). DeepSeek
thinks unless told not to: "When `thinking` is omitted or `null`, the mode
follows `reasoning_effort`; if `reasoning_effort` is also omitted, thinking is
enabled" (https://api-docs.deepseek.com/api/create-chat-completion). The
chain of thought comes back in `reasoning_content`, beside `content`
(https://api-docs.deepseek.com/guides/thinking_mode), and `max_tokens`
defaults to 8K without thinking and 64K with it, so the reasoning counts
against the cap. Left to that default, the planner (4096 tokens), the QA
review (600) and the engram extraction (2048) spent their budget reasoning and
got an empty answer. So no call is left to the default; the policy is in one
place, **`services/models/thinking.py`**, and every call sends
`thinking: {"type": "enabled" | "disabled"}`:

| Call | Thinking |
|---|---|
| Structured: the answer is parsed (`LLMProxyClient.structured = True`): the PO's sprint plans and foundation plan, the QA review's verdict, the Reviewer agent's decision, the discovery team suggestion, the specification drafts, the one-token credit probe; and every call of the EngramProcessor (JSON extraction) | off |
| Free-form, for PO, Architect, SecOps, Developer and the coding roles, Designer | on, with `LLM_THINKING_RESERVE_TOKENS` (16384) added to the call's `max_tokens` |
| Free-form, for every other role (QA, ScrumMaster, TechWriter, Manager, Support, Reviewer…) | off |

`LLM_THINKING=on|off` forces every call (`auto` by default). The client sends
the field in the body and `X-LLM-Purpose: structured|free`; the LangChain
clients (`ModelFactory.get_model`) send it as `extra_body`. The proxy
(`llm-proxy/thinking.py`) forwards it to DeepSeek only, sets `disabled` on a
DeepSeek request that has none, applies the operator's override, and drops the
field for Qwen, Nebius, Gemini and a company connection that is not DeepSeek's
API. Thinking tokens are billed as output.

**An empty answer is a named error, never a success.** A 2xx answer with no
text and no tool call becomes a `502` from the proxy with `reason`
`empty_content_reasoning_exhausted` (stopped at `finish_reason: length` after
the reasoning used the budget) or `empty_content` (anything else, JSON mode's
blank content included); the reasoning is never returned as the answer. Both
are retriable: the other DeepSeek model stands in, and another provider only
when `LLM_FALLBACK_PROVIDERS` names one. A structured call that came back
`empty_content_reasoning_exhausted` is first retried once on the same model
with thinking off, within the request's budget; the served answer then
carries `thinking_retry` with the first attempt's reason and tokens. The
backend's readers carry the reason on: `loop/worker/answers.llm_failure`, and
the planner's `planner_fallback` (`{"reason": "model_error", "detail":
"empty_content_reasoning_exhausted: …"}`, [planning](planning.md)), whose
detail starts with the named reason, an empty 2xx answer included.

**Tool calls written as text.** DeepSeek's models sometimes write a tool
call into `content` in their own markup (`<｜DSML｜tool_calls>`,
`<｜DSML｜invoke name="…">`, `<｜DSML｜parameter …>`) and leave `tool_calls`
empty; the backend's tool loop reads only `tool_calls`, so it took that text
for the final answer and delivered a first exploration step as the task's
work (sprint 3 of the self-sprint: tasks 12 and 16, and the Architect's
consult answers). `llm-proxy/inline_tool_calls.py` reads such an answer back
before the empty-answer check: when the request offered tools the calls go to
`tool_calls` (ids `call_inline_<n>`, `string="false"` values as JSON), the
prose before them stays as `content`, `finish_reason` becomes `tool_calls`
and the answer carries `inline_tool_calls` (`{"format": "dsml", "calls":
n}`). When the request offered none, the model asked for tools it does not
have (`exec_command`, `Read`): the call is asked once more on the same model
with `NO_TOOLS_NOTE` appended, within the request's budget, and the answer
carries `inline_tool_calls_retry`; a second such answer is the retriable
named error `inline_tool_calls_without_tools`, so the fallback model may
answer.

**Timeouts.** One setting, `LLM_UPSTREAM_TIMEOUT_SECONDS` (300 s), bounds a
chat call's provider calls; the proxy enforces it as a deadline on the whole
body (DeepSeek keeps a slow non-streaming call alive with empty lines,
https://api-docs.deepseek.com/quick_start/rate_limit, so a socket read timeout
never fired and gunicorn killed the worker mid-read), answering
`reason: upstream_timeout`. gunicorn (`llm-proxy/gunicorn.conf.py`, `gthread`
workers) allows that plus 30 s and the backend's client waits it plus 15 s; a
client wait that still runs out (`proxy_read_timeout`) or a dropped connection
(`proxy_connection_lost`) is a named error and is not sent again.
`llm-proxy/timeouts.py` derives the figures.

**Prices.** `model_prices.PUBLISHED_PRICES` holds DeepSeek's published price
per million tokens for the two models, which models.dev lags: Flash $0.30
input (cache miss) and $1.20 output, Pro $1.32 and $3.96. These are the **peak**
prices, so a cost report never under-states. Peak hours are 01:00–04:00 and
06:00–10:00 UTC, Monday to Friday, except Chinese public holidays; every other
hour (weekends and those holidays in full) is off-peak at half the price
(Flash $0.15 / $0.60, Pro $0.66 / $1.98). A cache hit costs Flash $0.006 and
Pro $0.044 per million input tokens at peak (half off-peak); the cost report
does not split cache hits and prices all input as a miss. Concurrency limits:
Flash 2500, Pro 500 requests. Time-of-day pricing and scheduling around
off-peak hours are not built here.

**Where the data goes.** DeepSeek's API processes requests in China. For
personal or client data that is a transfer to a third country (GDPR arts.
44–49) and likely calls for a DPIA (art. 35); the open weights served on an EU
or US host, or self-hosted, through a provider connection are the alternative
(`docs/CONFIGURATION.md`, `DEEPSEEK_API_KEY`).

### Model routing

Every LLM call goes through the proxy client (`backend/app/llm_proxy_client.py`).
`LLMProxyClient.chat()` / `chat_async()` take a `model` id (default
`"deepseek-flash"`), a `role`, and an optional `bu_id`. There is no
`model_preference` parameter: the `fast` / `powerful` tiers are an internal
fallback in the proxy (`_PREFERENCE_TIERS`). Role→model defaults live in
`backend/app/services/models/model_factory.py` (`ModelFactory.ROLE_MODEL_MAP`); every caller
reads them through `default_model_for_role` → `ModelFactory.model_for_role`,
which applies, most specific first, `AGENT_MODEL_<ROLE>` (for BackendDeveloper,
FrontendDeveloper, DevOps and DataEngineer, then `AGENT_MODEL_DEVELOPER`; their
map default is the Developer's), `NEMOTRON_ROLES`, `AGENT_MODEL` (one model for
the whole team), then the map (the Reviewer reads QA's settings, then its own
default, `REVIEWER_MODEL`). Above those sit the company's own choice and the agent's: an agent's
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
task worker calls it through `services/loop/task_routing.py`, which reads the
approved architecture (planning's), asks `model_routing.estimate_task` for the
estimate from the task's plan and board (its `delivered` events, the
`NEEDS WORK:` requests and send-backs on its board) and
`model_routing.effective_policy` for the policy, and stores the
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

### A model's profile

`model_profile.py` (issue #766) says what a model can take and return and
the Developer's limits for it. Before it the catalogue knew each model's
context, output limit and tool calling, but only the suggestions read them;
the tool rounds, the size of a tool's result and the answer's cap were one
value for every model. After the OpenHands SDK (capabilities from a
registry, a caller's overrides first, the source of each recorded) and its
named `[llm.<name>]` profiles, `profile(model)` takes each field from the
first source that has it:

1. `MODEL_PROFILES`, the operator's overrides by model name (a JSON object;
   `docs/CONFIGURATION.md`);
2. the catalogue (`model_catalog`), for `context`, `output`, `tool_call` and
   `reasoning`; the provider of the name's prefix first (`deepseek-*` in
   `deepseek`, `qwen*` in `alibaba`), then the others;
3. the settings for the limits (`DEVELOPER_TOOL_ROUNDS`,
   `MCP_RESULT_MAX_CHARS`, `DEVELOPER_MAX_OUTPUT_TOKENS`) and
   `DEFAULT_CAPABILITIES` (131072 of context, 8192 of output) for a model
   nobody describes.

`sources` names the one that decided each field (`override`,
`catalog:<provider>`, `setting`, `default`), and the Developer's run keeps
the whole profile as `model_profile` beside its routing. The limits stay
under `CEILINGS` (50 rounds, 100000 characters, the model's own output
limit for the answer's cap): an operator chooses within a range the code
holds, and a malformed override is ignored with a warning rather than
lifting a limit. Only the deployment's settings set a profile; nothing an
agent can reach does. With no override the limits are the settings', as
before.

### The edit format per model

**`backend/app/services/models/edit_format.py`** — The format a model writes
file changes in (issue #689; #359, A.8; the method of Aider's edit-format
benchmark, no code taken). `EDIT_FORMAT_BY_MODEL` (JSON, `{"<model>":
"<format>", "*": "<format>"}`) maps the model the task worker requests, after
difficulty routing, to `auto` (the default: whole files, with SEARCH/REPLACE
edits allowed for an existing file, the prompt every task had before),
`whole` (whole files only) or `search_replace` (SEARCH/REPLACE edits for an
existing file, whole files for new ones); a model it does not name takes
`*`, else `auto`, and a value that cannot be read is dropped with a warning.
`loop/worker/execute` puts the format's instructions in the Developer
prompt's `output_format` section (`prompt.file_instructions`), and
`loop/worker/record` puts `edit_format` on the run's evidence: the format
asked for and where it came from, and what the backend read in the answer's
file blocks and its own tool calls (`observe`): whole files, edit blocks,
malformed edit blocks, blocks off the format (an edit block under `whole`),
`workspace__edit` calls and their errors, the formats used and
`well_formed`. What the model says about its format is a claim and is never
read; a format a model could not use is recorded as such. The daemon's
refusals (`workspace_writes` with `rejected_edit`) stay beside it. No
column: it lives in the evidence JSON. The bench varies it per run
(`scripts/bench/edit_formats.sh`, `bench/README.md`); choosing defaults from
real-model numbers comes later.

### The bench's scorecards: the /bench page

**`backend/app/services/models/bench_scorecards.py`** / **`bench_schema.py`** / **`bench_schema_blocks.py`** / **`routers/bench.py`** (issue #685, split from #316) — The bench's committed scorecards (`bench/scorecards/*.json`, `real/`, `ablation/`; an ablation's arms are not read), read-only from the checkout `BENCH_REPOSITORY_DIR` names (empty: the backend's own; [configuration](../CONFIGURATION.md)) and served newest first by `GET /v1/bench/scorecards` to any signed-in caller (the cards describe the factory, not a company). Each card is checked with `bench_schema.validate`, a pure copy of `scripts/bench/schema.py` (versions 1 to 7, the `run`, `graders`, `sections_off`, `ablation` and v5 `humaneval` blocks in `bench_schema_blocks`, v6 `swebench` in `bench_schema_swebench`, v7's edit-format fields in `bench_schema_edit_format`, which reads the formats from `edit_format.FORMATS`; a change to `scripts/bench/schema.py` updates this copy in the same pull request), since the backend never imports the bench's scripts (protected paths, not in its image); `tests/test_scorecard_schema_parity.py` holds the two in step problem for problem. A card is `valid` (its numbers shown as written), `invalid` (the schema's problems, no number) or `unreadable` (broken JSON, a symbolic link, over 2 MB), never dropped. Every number is the scorer's observation; nothing asks a model or reads the database. Where the card says a metric was not measured it stays `{"measured": false, "reason"}`, never 0: the criteria when the sandbox could not grade (`criteria.unmeasured`) or a spec has none, and in the totals when any spec's grade is unmeasured; the reported tokens when no turn reported usage (the unreported turns counted apart); the cost unless every routing row carries `cost_cents` (the bench prices no turn yet, so it is never measured today); the prompt weight when no prompt was measured; a field the card's schema version does not hold (`not_in_card`); in v5's `humaneval` section, a problem whose grade could not run (the card's reason) and pass@1 while any problem is unmeasured or none was graded (`no_problems_graded`); in v6's `swebench` section, an instance whose environment could not be built or did not reproduce it (the card's reason, left out of the resolve rate as the card counts it), its FAIL_TO_PASS and PASS_TO_PASS counts when its tests did not run (`tests_not_run`), and the resolve rate when no instance was measured (`no_instances_measured`); in v7's edit-format section, a row the run could not measure (its outcome and criteria, `format_unmeasured` with the card's reason) and the well-formed share with no delivery observed (`no_delivery_observed`), while an `unusable` row keeps its outcome and is never shown as a pass. The page is `pages/Bench.tsx` (`/bench`), with `components/bench/` and `services/api/bench.ts` ([frontend](frontend.md)).

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

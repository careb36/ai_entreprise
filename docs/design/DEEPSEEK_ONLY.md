# DeepSeek only: the audit

Issue #751. The owner's standing decision (2026-11-20): "La Fabrique runs on
DeepSeek; no other configuration may stop the work."

## The standard applied

Where options differed, the strictest of these decided:

1. **Graceful degradation with a named, visible reason.** An optional
   integration that is missing or misconfigured turns its feature off (or
   degraded), says why once (the startup log, `/health/deep`, the Models
   page), and never crashes, refuses a start or blocks a sprint.
2. **No silent failure.** A stand-in (a local vector, another model) is
   reported as a stand-in, not as the real thing.
3. **No surprise third-party processors** (GDPR art. 25, data protection by
   design and by default; art. 28, processors). Data goes only to a provider
   the operator chose for that purpose. A key configured for one role does
   not make that provider a processor of every other role's prompts or of the
   company's memory.

Rule 3 is why the failover and the embeddings changed default rather than
only reporting: under rules 1 and 2 alone, falling back to Gemini with a
visible note would have been acceptable.

## The configuration audited

Only `DEEPSEEK_API_KEY` among the providers and integrations: no
`GEMINI_API_KEY`, `QWEN_API_KEY` or `NEBIUS_API_KEY`, no `GITHUB_TOKEN`, no
`SMTP_HOST`, `NEO4J_ENABLED` either way. The platform's own secrets
(`POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, and optionally `FERNET_SECRET_KEY`
and `AUDIT_SIGNING_KEY`) are not integrations and are outside this audit,
except that their absence is now named too.

DeepSeek's API reference (api-docs.deepseek.com, read 2026-11-20) lists the
Chat Completions, Responses and Messages APIs, FIM completion (beta), list
models, user balance, files, and tools (search, fetch, OCR, text to speech,
speech to text). It lists no embeddings endpoint. Its current model ids are
`deepseek-flash` and `deepseek-v4-pro`.

## Findings and fixes

(a) needs another provider or service; (b) fails hard or blocks a sprint when
something optional is missing; (c) falls back to another provider.

| # | Path | Kind | Before | Fix in #751 |
|---|------|------|--------|-------------|
| 1 | Proxy failover after 429, 5xx or an empty answer (`llm-proxy/routing.py`) | (c) | Skipped the failed provider entirely and tried Qwen, then Gemini, whenever their key and catalogue were present. With DeepSeek alone, a failed Pro request was not retried on Flash at all. | The provider's other model first (`SAME_PROVIDER_ALTERNATES`: DeepSeek Pro and Flash stand in for each other). Another provider only when the operator names it in `LLM_FALLBACK_PROVIDERS` (default empty). `/health` reports `capabilities.failover`. |
| 2 | Embeddings (`llm-proxy/embedding.py`, `/v1/embeddings`; used by `memory_client.py` and `engram_processor.py`) | (a), (c) | Gemini whenever `GEMINI_API_KEY` was set, so memories went to Google as a side effect of a key set for a role. Gemini's 768 values did not fit `vector_memory.embedding` (1536), so the insert failed once Gemini answered. Without the key, a deterministic local vector, reported nowhere. | **Degrade cleanly, local model as a follow-up (#755).** `EMBEDDING_PROVIDER=local` by default: the local vector, no key, no outside call. `gemini` is an explicit choice, padded with zeros to 1536 (the cosine distance between two Gemini vectors is unchanged). `/health` and `/health/deep` report `llm.embeddings: degraded` with the reason ("finds near-identical text, not meaning"). Memory never errors: the backend's `embed()` returns `None` on any failure and the store records `not_recorded`. |
| 3 | Knowledge graph (Neo4j) | (b) checked | Already optional (issue #328): off by `NEO4J_ENABLED=false`, or enabled and unreachable with a background retry; graph reads raise `GraphNotRecorded`, which every caller catches; `/health/deep` shows it `disabled` or `down` (degraded, not down). | Named as a capability: `knowledge_graph` off (`NEO4J_ENABLED=false`) or degraded (enabled, not reachable, retried). No behaviour change. |
| 4 | Memory context in a task's prompt (`services/loop/worker/prompt.py`) and the retrospective engram (`worker/close.py`) | (b) checked | Both already wrapped: a failure logs and returns nothing; never blocks a task or a close. | Covered by tests with no proxy answer and no graph. |
| 5 | Research and grounding tools (`services/tools/research_*`) | (a) checked | Public documentation hosts and registries, fetched without credentials; a host that does not answer is a tool error. Not a data processor (no prompt content is sent, only the URL the host allowlist permits). | Named as capabilities (`research_tools`, `grounding_lookups`). No change. |
| 6 | Model catalogue refresh from models.dev (`services/models/model_catalog.py`) | (a) checked | A GET every `MODELS_CATALOG_REFRESH_HOURS` (default 24); a failure keeps the bundled snapshot. Nothing is sent. | Named as `model_catalog_refresh`. No change. |
| 7 | Notification webhooks (`services/integrations/webhooks.py`) | (b) checked | Per company, none by default; delivery is its own outbox row, so a webhook that is down never holds a sprint. | Named as `webhooks`. No change. |
| 8 | Mail (the Manager's brief, `services/management/brief_mail.py`) | (b) checked | Started only when `SMTP_HOST` is set. | Named as `mail` off with the reason. |
| 9 | Error reports (`ERROR_REPORT_URL`) | (a) checked | Off unless set. | Named as `error_reports`. |
| 10 | GitHub token (`services/producers/github_adapter.py`, `routers/github.py`) | (a) checked | Optional: pull-request backfill runs unauthenticated (GitHub's lower rate limit; a rate limit answers 503 with the reset time on that endpoint only). Publishing uses the workspace daemon's own Git credentials, and only under forge policy `publish` (each company starts at `none`, issue #727). | Named as `github_token` and `forge`. No change. |
| 11 | SSO | (b) checked | Per company; nothing at startup depends on it. Needs `JWT_SECRET_KEY` like every login. | Named as `sso`. |
| 12 | Manager, Support and Reviewer agents | (a) checked | Off by default; when on they run on the DeepSeek defaults (`model_factory`: Reviewer on Pro, Manager and Support on Flash). | Named as capabilities. |
| 13 | Discovery (New company's suggested team) | (c) checked | Already DeepSeek only (PR #739): every agent's model is its role's default. | Covered by a test. |
| 14 | Planning, execution, QA review, the engram processor | (a) checked | Role defaults are DeepSeek Pro or Flash for every role (`ROLE_MODEL_MAP`). | A test drives a task from the board to review through a fake proxy that refuses any non-DeepSeek model. |
| 15 | Startup checks | (b) checked | Neither the backend (`lifecycle.startup`: NATS, Neo4j, keys all degrade with a warning) nor the proxy (the key check never stops it) refuses to start. | A startup self-check in both logs the effective capability set, names and reasons only. |
| 16 | `JWT_SECRET_KEY` missing | (b) platform secret | The backend starts; login answers 503. | Not weakened (authentication off by default would break rule 3's spirit). Named as `auth: off` with the reason. |
| 17 | `FERNET_SECRET_KEY`, `AUDIT_SIGNING_KEY` missing | (b) platform secret | Start with a warning; credentials stored `plaintext:`, audit rows `unsigned:`. | Named as `credential_encryption` and `audit_signing` degraded. |
| 18 | Console assistants (`CONSOLE_ASSISTANTS_ENABLED`, default true) | (a), rule 3 | When GitHub Copilot CLI or Claude CLI is installed on the daemon's computer, a task may be sent to it for advice: a provider other than DeepSeek, engaged by default. | **Reported, not changed here**: another pull request is changing the console assistants. Named as the `console_assistants` capability with the reason and how to turn it off. The owner should decide whether the default becomes `false`. |
| 19 | The bench's real-model job (`.github/workflows/bench.yml`, `bench-real`) | (a) | Requires `GEMINI_API_KEY` as well as `DEEPSEEK_API_KEY`. | **Follow-up #756**: workflow files are outside this pull request. |
| 20 | Proxy catalogue at startup | (b) checked | A provider without a key is skipped; an empty catalogue keeps the proxy up. `/v1/models`'s orchestrator is always `deepseek-flash` (PR #739). | No change. |
| 21 | DeepSeek itself out of credit | (b) by design | The proxy keeps the primary's 402; the backend pauses the sprint with the reason (`sprint_control`) instead of failing tasks one by one. With DeepSeek the only provider, that is the visible reason, not a failure to hide. | No change; documented in OPERATIONS.md. |

## Where the capabilities are shown

- **Startup:** the backend logs `Effective capabilities: name=state, …` and
  one line per capability that is not on, with its reason
  (`lifecycle.log_capabilities`, `services/platform/capabilities.py`); the
  proxy logs its own (`providers_configured`, `embeddings`, `failover`).
- **`/health/deep`:** a `capabilities` section, the backend's beside the
  proxy's `llm.*` entries; `llm: off` when the proxy said nothing. It never
  changes the status.
- **The Models page:** the Capabilities panel lists the same section.

No capability carries a setting's value: a host, an address or a key never
leaves `capabilities.py` (a test sets values and checks they are absent).

## What was taken, and from where

Nothing was taken from the reference implementations for this change; the
capabilities section extends the `/health/deep` aggregation already in place
(`services/platform/healthcheck.py`).

## Follow-ups

- #755: a local semantic embedding model (small, permissively licensed,
  pinned by hash, CPU-only) as the default instead of the hash vector.
- #756: `bench-real` should require only `DEEPSEEK_API_KEY`.
- Item 18: the owner's decision on the console assistants' default.

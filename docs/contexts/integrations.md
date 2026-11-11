# The integrations context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/integrations/`.

- **Folder:** `backend/app/services/integrations/`
- **Responsibility:** Telling the outside world: the company's notification webhook and the outbox processor that posts it. It listens to changes of the task record and the evidence; nothing below it imports it.
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [evidence](evidence.md) (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Modules:** `outbox_service`, `webhooks`.

## Components

### The notification webhook

**`backend/app/services/integrations/webhooks.py`** / **`routers/webhooks.py`** — The company's notification webhook (issue #357): one per company (`CompanyWebhook`: an https URL, a Fernet-encrypted secret never returned, the events among `task_done`, `task_blocked`, `sprint_closed`, `run_refuted`, on or off; `GET`/`PUT`/`DELETE /v1/companies/{id}/webhook`, Viewer / Manager, `POST .../webhook/ping`). A `before_flush` listener over every session (`install`, called by `main.py`) sees a task's status change to done or blocked (with `block_reason` and `waits_for_a_person`), a sprint's to completed and a new refuted `Verification`, and adds an `OutboxEvent` (`webhook_post`) in the same transaction, so a change that rolls back sends nothing and no caller has to know about webhooks. The outbox processor posts it (`deliver`) with `X-Fabrique-Event`, `X-Fabrique-Delivery` and `X-Fabrique-Signature-256: sha256=<HMAC of the body>` (GitHub's shape); a non-2xx answer or an error is recorded on the webhook (`last_status`, `last_error`) and retried with backoff (the outbox waits `retry_count² × 10 s` between attempts, five at most). A webhook that is down never holds up a sprint. The URL's host must resolve to public addresses unless `WEBHOOK_ALLOW_PRIVATE_HOSTS`.

## Described elsewhere

- `outbox_service` (the outbox processor that stores `task.assign` and posts webhooks): step 1 of [the sprint loop](../SPRINT_LOOP.md) and the paragraph below.
- `WEBHOOK_ALLOW_PRIVATE_HOSTS`: [configuration](../CONFIGURATION.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-13).

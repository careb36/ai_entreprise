# The integrations context

Telling the outside world: the notification webhook, chat approvals and
regressions, personal notifications and audit anchors. Read this before
changing `backend/app/services/integrations/`; each module's docstring has the
detail.

- **Folder:** `backend/app/services/integrations/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [evidence](evidence.md). Nothing imports it.
- **Interface:** none.
- **Modules:** `anchor_settings`, `anchoring`, `chat_approvals`, `chat_client`, `chat_connections`, `chat_links`, `chat_messages`, `chat_regression_messages`, `chat_regressions`, `chat_signatures`, `outbox_service`, `person_notices`, `tsa_client`, `webhook_client`, `webhooks`.
- **HTTP adapters:** `webhook_client`, `chat_client`, `tsa_client`. Only these import an HTTP library; they return data and raise named errors.

## How it works

A `before_flush` or `after_flush` listener sees a change on the record (a task
done or blocked, a sprint closed, a refuted verification, a risk hold) and adds
an `OutboxEvent` in the same transaction, so a rolled-back change sends
nothing. `outbox_service.py` delivers it later and retries with backoff. A
receiver that is down never holds up a sprint.

## Rules

- Every outbound message is built from the record at delivery time, never from
  what an agent claimed.
- Credentials (webhook secret, bot tokens, chat keys) are Fernet-encrypted and
  never returned; responses say `has_*`.
- A chat decision is held to the same role and segregation-of-duties checks as
  the web review.

## Components

### The notification webhook

`webhooks.py`, `webhook_client.py`: one webhook per company
(`/v1/companies/{id}/webhook`), https only and public hosts unless
`WEBHOOK_ALLOW_PRIVATE_HOSTS`. Bodies are signed
`X-Fabrique-Signature-256` (GitHub's shape).

### Approvals in Slack and Teams

- `chat_connections.py`, `chat_links.py`: one Slack or Teams connection per
  company, managed by a signed-in Owner. A chat user is linked by a one-time
  code, never by email.
- `chat_signatures.py`, `chat_client.py`: inbound requests checked by HMAC
  within 300 s; outbound calls to Slack's Web API or a Teams webhook.
- `chat_approvals.py`, `chat_messages.py`: a risk hold posted to chat and
  decided there through the same `run_review.submit_review` as the web, with
  the Manager role. A replayed interaction does nothing; only the record is
  shown. Design: [`CHAT_APPROVALS.md`](../archive/design/CHAT_APPROVALS.md).
- `chat_regressions.py`, `chat_regression_messages.py`: information only, for
  an integration whose tests now fail or a done task whose verification went
  from verified to refuted.

### Personal notifications

`person_notices.py`: `review_requested` and `integration_waiting` written to
the members who may act, plus an optional email that names the kind and
nothing else. The rows themselves: [platform](platform.md).

### Audit anchors

`anchoring.py`, `anchor_settings.py`, `tsa_client.py`: the audit chain's head
recorded outside the factory, as a signed webhook statement (kept as a claim)
or an RFC 3161 timestamp checked against the pinned certificate. Design:
[`AUDIT_ANCHORS.md`](../archive/design/AUDIT_ANCHORS.md).

### The outbox

`outbox_service.py`: stores `task.assign` and delivers every event above.

## See also

- Settings (`WEBHOOK_ALLOW_PRIVATE_HOSTS`, `CHAT_APP_URL`, `CHAT_RATE_LIMIT`, `PUBLIC_API_URL`): [configuration](../CONFIGURATION.md).
- Setting up Slack or Teams and rotating secrets: [operations](../OPERATIONS.md#approvals-in-slack-and-teams).
- The intake requests sent from chat: [planning](planning.md#the-intake-queue).

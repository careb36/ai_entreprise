Added

* **A support admin can replay a failed or stuck outbox event.** `POST /v1/admin/outbox-events/{event_id}/replay` (accounts in `SUPPORT_ADMIN_EMAILS`) puts a `failed` row, or a `pending` row waiting out its retry backoff, back to a due `pending` row and returns its id and correlation id. The payload and correlation id stay as committed, so the outbox poller republishes the same message under the same `Nats-Msg-Id`; nothing that created the row runs again, so no second task is made. A `completed` row or a row that is already due is refused with `409`, a missing one with `404`.

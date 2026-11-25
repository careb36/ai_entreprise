### Outbox replay for failed or stuck async runs (admin)

Operators can recover an async run whose outbox event gave up or stalled
without editing the database or the NATS stream by hand.

- `POST /v1/admin/outbox-events/{event_id}/replay` (support admin only)
  returns a `failed` or stuck `pending` outbox event to `pending` under a row
  lock and returns its id. The committed payload and correlation id are
  untouched, so the running poller republishes the same message with the same
  `Nats-Msg-Id`.
- A replay never re-runs the domain logic that created the event, so no second
  sprint task or backlog row is produced. A `completed` row, or a `pending`
  row that is already due, is refused with `409`.
- The publish path is factored into `outbox_service.dispatch_event`, shared by
  the poller and the replay, so both deliver through one code path.

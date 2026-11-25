Correlation id on task runs
===========================

Every ``task.assign`` now carries a stable ``correlation_id`` generated once at
enqueue time (outbox publish or ``_dispatch_todo_items``). The task worker reads
it, passes it unchanged to ``task_run._run_claimed_task`` (so it reaches the
daemon call and verification), and stores it on ``AgentRun.correlation_id`` and
``OutboxEvent.correlation_id``. It is propagated as an OpenTelemetry span
attribute ``task.correlation_id`` and appears in structured worker logs.

The id is distinct from ``Nats-Msg-Id`` (deduplication only) and is never
regenerated on a redelivery: a message that arrives without one is run under a
freshly generated id so its trace still lines up, but one that carries an id
keeps it byte for byte from publish to AgentRun.

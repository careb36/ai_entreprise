# Operations runbook (issue #328)

## Backups
- Postgres: `pg_dump` the `factory` database daily; keep 14 days.
- Neo4j: optional; export the engram graph weekly if Neo4j is enabled.

## Retention
- `agent_runs` and `verification`: keep 90 days, then archive.
- `audit_log`: keep 1 year.
- `outbox_events` completed: prune after 7 days.

## Horizontal workers
- The JetStream consumer supports multiple replicas; use `FOR UPDATE SKIP LOCKED`.
- Never run two workers against the same NATS stream without distinct consumer names.

## Neo4j
- Optional: set `NEO4J_ENABLED=false` to skip; the backend starts without it.

## Upgrade
- Run `alembic upgrade head` before starting the backend (the container entrypoint does).
- Roll back with `alembic downgrade -1`.
# Operations runbook (issue #328)

## Backups
- Postgres: `pg_dump` the `factory` database daily; keep 14 days.
- Neo4j: optional; export the engram graph weekly if Neo4j is enabled.

## Retention
- `agent_runs` and `verification`: keep 90 days, then archive.
- `audit_log`: keep 1 year; never delete a row of a company's hash chain without archiving the chain with it (`GET /v1/audit/chain` would show the gap).
- `outbox_events` completed: prune after 7 days.

## Horizontal workers
- Task workers share one durable JetStream consumer (`task-worker`): replicas split the assignments. Distinct consumer names would make every replica process every assignment.
- The outbox processor takes its rows with `FOR UPDATE SKIP LOCKED`, so several replicas never send one event twice.
- Run one worker for now: the review cycle and other timers still assume a single process until issue #328 makes them safe for several.

## Neo4j
- Optional: set `NEO4J_ENABLED=false` to skip; the backend starts without it.

## Upgrade
- Run `alembic upgrade head` before starting the backend (the container entrypoint does).
- Roll back with a database restore taken before the upgrade: the baseline migration is irreversible on purpose.

# Operations runbook

How to run La Fabrique from this repository: deploy, upgrade, keys, health,
the usual incidents, backups, retention and how many workers may run (issue
#328). The page follows the shape of OpenHands' self-hosted deployment docs:
one section per concern, commands first. Where the workspace daemon runs,
on the person's computer or on a server, is the operator's choice, the way
Factory leaves local or cloud execution of its Droids to the operator.

## Deploy

1. Copy `.env.example` to `.env` and set at least `POSTGRES_PASSWORD`,
   `JWT_SECRET_KEY`, `FERNET_SECRET_KEY`, `AUDIT_SIGNING_KEY` and the model
   keys (`GEMINI_API_KEY`, `DEEPSEEK_API_KEY`), or plan a provider connection
   per company. `CLAUDE.md` lists every variable.
2. `docker compose up -d` starts Postgres, NATS (JetStream), the LLM proxy,
   the backend, the frontend, the janitor and Neo4j. Neo4j is optional and
   on by default: the backend does not wait for it and connects once it
   answers; the Neo4j section below says how to run without it.
3. Start the workspace daemon on the computer that holds the projects:
   `./scripts/start-daemon.sh` (or `.ps1` on Windows). That is the local
   choice. The daemon may instead run on a server that holds the checkouts
   (`docker compose --profile docker-daemon up`, Linux, `PROJECTS_ROOT`
   mounted). Agent-written code then runs on that server, so set
   `DELIVERY_SANDBOX=container` and `FABRIQUE_SANDBOX_REQUIRED=container`
   there. Keep `NATS_BIND=127.0.0.1` unless the daemon is on another
   machine: anyone who reaches NATS can ask the daemon to run commands.
4. Check `GET /health` (below), register the first user in the frontend and
   create a company.

The backend container runs `alembic upgrade head` before it starts. When you
run the backend by hand, run the migrations yourself (`cd backend && alembic
upgrade head`) or set `DB_AUTO_UPGRADE=true`. The backend refuses to start on
a database behind head and names both versions.

## Upgrade

1. Back up with `scripts/backup.sh` (below). The baseline migration is
   irreversible on purpose, so rolling back means restoring that backup.
2. Stop the backend replicas and the janitor (`docker compose stop backend
   janitor`). Work in flight returns to `todo` on the next review cycle;
   assignments wait in JetStream.
3. `git pull`, then `docker compose build backend janitor llm-proxy frontend`.
4. `docker compose up -d`. The backend's entrypoint migrates to head. With
   several replicas, start one first and the others once it is up: Alembic
   takes no lock of its own.
5. Check `GET /health/deep` and the Glassroom of a running sprint.

To roll back: stop everything, run `scripts/backup.sh restore <file> --yes`
(it replaces the whole database, below), check out the previous release,
then build and start it.

## Rotate keys

| Key | Where | What rotating does | How |
|-----|-------|--------------------|-----|
| `JWT_SECRET_KEY` | backend | Every session and Glassroom ticket stops working, and people log in again. API tokens (`/v1/api_tokens`) are stored as hashes and keep working. | Change it in `.env` and restart every backend replica at once: a replica with the old key refuses the new tokens. |
| `FERNET_SECRET_KEY` | backend | Nothing re-encrypts yet. Values encrypted under the old key (project credentials, MCP server secrets, provider connection keys, run secrets) can no longer be read. MCP servers and run secrets still list by name; an unreadable run secret is left out of the run and logged. | Before the change, note what each company stored (Models page, MCP servers, run secrets). Change the key, restart, and enter the values again. |
| `AUDIT_SIGNING_KEY` | backend | Rows keyed with the old key no longer verify: `GET /v1/audit/chain` reports `hash_mismatch` from the first of them. The rows are intact; only the key that proves it changed. | Rotate only when the key leaked. First archive each company's chain (Archiving the audit chain, below) and keep the old key with that archive in your secret store, so the old part can still be verified offline. Then change the key, restart, and record the date. |
| `LLM_PROXY_INTERNAL_TOKEN` | backend and llm-proxy | While the two values differ, company provider connections (`conn:<id>:<model>`) fail and say why. The deployment's own models keep working. | Set the same new value on both and restart both. |
| Model keys (`GEMINI_API_KEY`, …) | llm-proxy | Nothing beyond the restart. | Change the key and restart `llm-proxy`. A company changes its own provider key on its Models page. |

`CLAUDE.md` (Required Environment Variables) has the command that generates
each key.

## Read `/health` and `/metrics`

- `GET /health` is the cheap, public liveness check. It returns `status`,
  `nats` (its `state`: `connecting` while the backend retries the broker,
  `connected` once its workers started; with `connected`, `jetstream`,
  `authenticated` and `tls`, Securing the bus below) and `jetstream`. With `jetstream: false` the
  backend runs on core NATS: assignments published while no worker runs are
  lost, and two replicas both get every assignment, which the task claim
  below makes harmless.
- `GET /health/deep` probes Postgres, the LLM proxy, NATS and Neo4j. It says
  `down` when Postgres or the proxy is unreachable (no agent work at all),
  `degraded` when NATS is, or Neo4j while it is enabled (API reads work, but
  without NATS no sprint moves), and `healthy` otherwise. Each component
  carries its `reason`. Neo4j switched off (`NEO4J_ENABLED=false`) is
  reported as `disabled`, is not probed and degrades nothing.
- `GET /metrics` is Prometheus text: `fabrique_requests_total` and
  `fabrique_request_duration_seconds` by route template,
  `fabrique_agent_runs_ingested_total` by producer,
  `fabrique_agent_runs_verified_total` by verdict,
  `fabrique_sprints_created_total`, `fabrique_sprints_completed_total`,
  `fabrique_errors_total` and `fabrique_active_connections`. Each replica
  exposes its own counters, so scrape every replica and add them up.

## When something is wrong

**The daemon is absent.** `GET /v1/workspace/status` says `absent` (NATS
answers and nothing listens) or `unknown` (NATS is unreachable or nothing
answered in time; that never means stopped). Start the daemon on the
projects' computer (`./scripts/start-daemon.sh`); it reconnects on its own.
Meanwhile the files a task delivers are queued in JetStream
(`workspace.deliver`), the run waits for that job and the review loop holds
the task. When the daemon connects it writes them and reports on
`workspace.delivered`, and the run is verified against the hashes recorded
at delivery. There is nothing to replay by hand.

**A sprint is stuck.** Check in this order:
1. Its status. `planned`, `paused` and `cancelled` sprints do not move. A
   planned sprint starts with `POST /v1/sprints/{id}/start`; a paused one
   shows its `pause_reason` and resumes with `/resume`.
2. `GET /v1/inbox`: tasks blocked until a person acts, high-risk holds, and
   tasks waiting on a dependency held by one of those. A Manager accepts,
   sends back or carries them over.
3. Tasks in `todo` that never start: `POST /v1/sprints/{id}/dispatch`
   queues them again. Check that `/health` says `connected` and that the
   backend's log says `Task Worker consuming task.assign`.
4. Tasks left in `in_progress` by a crashed replica return to `todo` at
   the next review cycle of any live replica (every 30 seconds), which
   finds that no live replica holds them and queues them again.
5. A delivery waiting for the daemon: see above.

**A provider is out of credit.** The worker pauses the sprint, with
`pause_reason` naming the provider (a 402, or a 429 that names a quota or a
balance), and puts the task back to `todo` instead of blocking it. Add
credit, or change the role's model on the company's Models page, then call
`POST /v1/sprints/{id}/resume`. The company gets no follow-up sprint of its
own while one is paused.

## Backups

`scripts/backup.sh` writes `backups/postgres-<db>-<UTC time>.dump`
(`pg_dump -Fc`, checked with `pg_restore --list`) with a `.sha256` file
beside it, and removes backups older than `BACKUP_KEEP_DAYS` (14). It runs
`pg_dump` inside the `factory-postgres` container when that container is
running. `PG_CONTAINER` names another container; set it empty to use the
`pg_dump` on PATH with `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER` and
`POSTGRES_PASSWORD`. Unset values are read from `.env`. With
`NEO4J_ENABLED=true` and the `factory-neo4j` container running, it also
exports the graph as Cypher through APOC (`neo4j-<time>.cypher`). The
APOC export is streamed (APOC's file export is not enabled in
`docker-compose.yml`), each batch base64-encoded by the query and decoded
by the script, so the file holds plain Cypher statements and not
`cypher-shell`'s quoted result table.

Everything is written with `umask 077`, readable only by the account that
runs the script: a backup holds every company's data and the stored
credentials. `.gitignore` leaves out `/backups/`, `*.dump`,
`*.dump.partial`, `*.dump.sha256` and `neo4j-*.cypher`, so a backup in the
checkout is never committed; keep `BACKUP_DIR` outside it where you can.

Run it daily from cron on the host, for example
`17 3 * * * cd /srv/la-fabrique && scripts/backup.sh >>backups/backup.log 2>&1`,
and copy `backups/` to another machine. CI backs up the e2e database and
restores it on every run (`.github/workflows/e2e.yml`).

**Restore.** Stop the backend replicas and the janitor, then run
`scripts/backup.sh restore backups/postgres-factory-<time>.dump --yes`. It
checks the checksum, then replaces the whole database rather than editing
it in place (`pg_restore --clean` drops only what the archive holds, and a
table a later migration added would block it or be left behind):
1. creates an empty database `<db>_restore_<time>` (from `template0`,
   UTF8), connected to `PG_MAINTENANCE_DB` (default `postgres`);
2. restores the dump into it with `pg_restore --single-transaction
   --exit-on-error --no-owner`: a restore that fails leaves nothing, the
   empty database is dropped and `POSTGRES_DB` is untouched;
3. renames `POSTGRES_DB` to `<db>_pre_restore_<time>` and the restored
   database to `POSTGRES_DB`. A rename needs no session on either database,
   so it stops here while a backend is still connected.

Drop `<db>_pre_restore_<time>` once you have checked the restore. By hand,
the same is: `CREATE DATABASE <new> TEMPLATE template0`, `pg_restore
--single-transaction -d <new> <file>`, then `DROP DATABASE <db>` (or rename
it) and `ALTER DATABASE <new> RENAME TO <db>`. Then start the backend: it
checks that the schema is at head and migrates a backup from an older
release. Load a Neo4j export with `cypher-shell < neo4j-<time>.cypher` into
an empty database. Always restore the whole Postgres database, never single
tables: the audit chain would show the missing rows.

## Retention

The janitor applies these settings once a day (`JANITOR_INTERVAL_SECONDS`).
Each is a number of days; `0` keeps everything (`backend/janitor/retention.py`).
Retention runs in transactions; only the vector index rebuild (`REINDEX
CONCURRENTLY`) runs in autocommit. Superseded run versions go in batches,
each one transaction: the runs are locked (`FOR UPDATE`), the guards
checked again under the lock, so a review, comment, approval or
publication recorded meanwhile keeps its run, and the verifications are
deleted with their runs or not at all.

| Setting | Default | Deletes | Never deletes |
|---------|---------|---------|---------------|
| `RETENTION_OUTBOX_DAYS` | 7 | delivered outbox events | pending and failed events |
| `RETENTION_AGENT_RUNS_DAYS` | 0 | superseded versions of a run, with their verifications | the latest version of each run (the evidence of record), and any version a person reviewed, commented on, approved or published |
| `RETENTION_KPI_TURNS_DAYS` | 0 | KPI turn rows of `audit_log` outside a hash chain (rows without a company, or older than the chain) | any row of a company's chain, and any row a run, verification, approval, review or publication points at |
| `RETENTION_GLASSROOM_DAYS` | 0 | stored task events of completed and cancelled sprints | the events of open sprints. The live Glassroom keeps its last 50 events per sprint in memory and replays the board, which is never deleted |

Once the database grows, `RETENTION_AGENT_RUNS_DAYS=90` and
`RETENTION_GLASSROOM_DAYS=90` are reasonable. The janitor logs how many
chained KPI turns it kept because the chain needs them.

There is **no audit retention**, on purpose: every row of a company's audit
log is a link of its hash chain, and deleting one shows as a gap in
`GET /v1/audit/chain`.

**Archiving the audit chain.** To keep old audit rows outside the live
database, archive a company's whole chain, never part of it:
1. Back up (above).
2. Export the company's rows with every column the chain hashes:
   `\copy (SELECT * FROM audit_log WHERE company_id = <id> ORDER BY chain_seq) TO 'audit-<id>.csv' CSV HEADER`.
   Keep with it the `GET /v1/audit/chain` answer of that moment (its
   `head`) and, if the chain is keyed, which `AUDIT_SIGNING_KEY` keyed it.
3. Keep the archive read-only. Deleting the archived rows from the live
   database is not supported yet, because the chain would report the gap.
   Until the chain can start from a checkpoint, the archive is a copy and
   the database keeps the rows.

## How many workers may run

Several backend replicas may run against one Postgres and one NATS. Each
replica serves the HTTP API and runs the workers. Give each its own host
port, or put them behind a load balancer: the Glassroom works through any
replica, since each one subscribes to every sprint's stream. All replicas
need the same `.env` and the same `NATS_NAMESPACE`. In `docker-compose.yml`
the backend has a fixed `container_name` and port, so a second replica is a
second service or a second host; `docker compose up --scale backend=2` does
not work as the file stands.

What makes several replicas safe (`backend/app/services/replicas.py`,
`backend/app/services/locks.py`):

- **Assignments.** Replicas share one durable JetStream consumer
  (`task-worker`), so each stored assignment goes to one of them. A second
  assignment for the same task (from another dispatch) does no harm: a
  replica claims a task with a conditional update from `todo`, and only the
  replica whose update matched runs it.
- **Restarts and crashes.** The replica working on a task holds the
  task's Postgres advisory lock, which is its lease: a crashed replica's
  locks go with its connections. Every review cycle (the startup one
  included) first returns to `todo` the `in_progress` tasks nobody holds,
  and its dispatch queues them again, so a surviving replica picks up a
  dead one's task within a cycle (a survivor acknowledges the redelivered
  assignment itself, since the task is no longer `todo`). Postgres notices
  a killed process at once; a host that vanished is noticed when its TCP
  connection times out, so set the server's `tcp_keepalives_idle` (for
  example 60) to bound that.
- **The review cycle** (QA reviews, the delivery gate, rework, dispatch,
  dependency holds) runs under one advisory lock: one replica at a time,
  and the others skip that round. The dispatch cooldown is kept per
  replica; a task queued by two replicas is stored once (JetStream's
  duplicate window on `assign-<task>-<updated_at>`) and claimed once
  anyway.
- **Closing a sprint** runs under the company's lock and closes the sprint
  with a conditional update from `active`. There is one close, one
  retrospective, one measurement of the procedures, one proposal round and
  one follow-up.
- **The ScrumMaster's triage** runs once per escalation: it takes the
  escalation's lock and checks that no decision answers it yet.
- **The Support and Reviewer agents' cycles** run under their own locks.
- **The outbox** takes its rows with `FOR UPDATE SKIP LOCKED` and publishes
  with `outbox-<id>` as the message id.
- **Assistant cooldowns** are stored in `assistant_runtime_state`. The
  daemon capability probe and the Glassroom buffer are per replica and need
  nothing shared.

A replica holds one more Postgres connection while it works on a task, and
one for each cycle it leads. Size `max_connections` for about
`replicas × (pool 5 + overflow 10 + 3)`.

What is tested: `backend/tests/test_replicas.py` races two workers (threads
on a file-backed SQLite database) on one task and one sprint. With
`REPLICAS_POSTGRES_URL` set it runs the races on Postgres, which the e2e
workflow does against its database. What is not tested yet: two backend
processes driving the whole e2e loop together. That CI job is the next
step.

## Neo4j

Neo4j (the knowledge graph) is optional and on by default (issue #328):
`docker compose up -d` starts it with `NEO4J_PASSWORD` (and
`NEO4J_AUTH=neo4j/<password>`) from `.env`. The backend does not depend on
it: it starts without waiting, and while Neo4j is enabled but not reachable
yet it tries again every 15 seconds for ten minutes and records the graph
from the moment it connects. If Neo4j stays unreachable longer, memory
search keeps working, `memory_graph` stays false until a restart with Neo4j
up, and `/health/deep` reports the stack `degraded`.

To run without it (less memory; the Enterprise image in compose runs under
an evaluation licence, so a production deployment either licenses it, moves
to the Community image, or turns the graph off):

```bash
NEO4J_ENABLED=false docker compose up -d --scale neo4j=0
```

With `NEO4J_ENABLED=false` the backend never connects to it:

- memory search and store (`/v1/memory/*`) run on Postgres with pgvector
  alone; the store's answer says what each store kept (`vector`, `graph`:
  `stored` or `not_recorded`);
- engrams (sprint retrospectives, the kickoff) are kept in pgvector only,
  and the agents' memory of past decisions comes from there;
- file lineage (`GET /v1/enterprise/lineage`) answers `recorded: false`
  with the reason, which the Lineage page shows as "not recorded" rather
  than "no decisions";
- `/v1/capabilities` says `memory_graph: false` beside `memory`, and
  `/health/deep` shows `neo4j` as `disabled`.

Nothing recorded while the graph was off is backfilled into it.
`scripts/backup.sh` exports it only while it is enabled.

## Securing the bus
Anything that can publish on NATS can ask the workspace daemon to run commands, so give each component its own user and, when the bus leaves the machine, TLS (issue #327; SECURITY.md §8.6).

1. Credentials. In `.env`, set two long random passwords (`python -c "import secrets; print(secrets.token_urlsafe(32))"`):
   `NATS_BACKEND_PASSWORD=...` and `NATS_DAEMON_PASSWORD=...`. `docker compose up -d` then starts the broker with `nats/nats-server.conf` and the backend connects as `backend`. Start the daemon with the daemon's password: `NATS_DAEMON_PASSWORD=... ./scripts/start-daemon.sh` (or `NATS_USER=daemon NATS_PASSWORD=...`; with a `.creds` file, `./scripts/start-daemon.sh --creds FILE` or `-Creds FILE` on Windows). Setting only one password stops the broker with a message; setting neither keeps the old unauthenticated bus.
2. Check. `curl -s localhost:8000/health` shows `"nats": {"state": "connected", "connected": true, "jetstream": true, "authenticated": true, "tls": false}`. The daemon logs `auth: password`.
3. Permissions. After changing the subjects in `backend/app/nats_client.py` or `vscode-daemon/nats_scope.py`, run `python3 scripts/nats_config.py` and commit `nats/nats-server.conf`; `--check` fails while it is stale. A deployment with `NATS_NAMESPACE` writes its own: `python3 scripts/nats_config.py --namespace <ns> --output nats/nats-server.conf`.
4. TLS with a self-signed CA. On the broker's machine:

   ```bash
   mkdir -p nats/certs && cd nats/certs
   openssl req -x509 -newkey rsa:4096 -sha256 -days 825 -nodes \
     -keyout ca.key -out ca.crt -subj "/CN=Fabrique NATS CA"
   openssl req -newkey rsa:4096 -nodes -keyout server.key -out server.csr -subj "/CN=nats"
   printf 'subjectAltName=DNS:nats,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > server.ext
   openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
     -days 825 -sha256 -extfile server.ext -out server.crt
   ```

   Add the broker's public name or address to `subjectAltName` if the daemon connects from another machine. Keep `ca.key` offline; `nats/certs` is ignored by git. Restart the broker (`docker compose up -d nats`): it requires TLS once `server.crt` and `server.key` exist, and with `NATS_TLS=true` in `.env` it refuses to start if either is missing, rather than run in the clear (likewise `NATS_TLS_VERIFY=true` without `ca.crt`). Set `NATS_TLS=true` in `.env` (the backend container reads the CA from `nats/certs/ca.crt`) and start the daemon with `NATS_TLS=true NATS_TLS_CA=/path/to/ca.crt`. `/health` then shows `"tls": true`.
5. Client certificates (optional). Sign one per component like `server.crt` with `extendedKeyUsage=clientAuth` and set `NATS_TLS_VERIFY=true` for the broker. For a daemon started by `scripts/start-daemon.*`, set `NATS_TLS_CERT` and `NATS_TLS_KEY` to the files' paths. In the containers, `nats/certs` is mounted at `/etc/fabrique-nats/certs`: put the files there and name them in `.env` with `NATS_BACKEND_TLS_CERT` / `NATS_BACKEND_TLS_KEY` (backend) and `NATS_DAEMON_TLS_CERT` / `NATS_DAEMON_TLS_KEY` (the opt-in daemon container), e.g. `NATS_BACKEND_TLS_CERT=backend.crt`; compose passes `NATS_TLS_CERT=/etc/fabrique-nats/certs/backend.crt` and so on, and nothing when they are empty.
6. Rotate a password by changing it in `.env` and restarting the broker, the backend and the daemon together; a component with the old one is refused and keeps retrying.

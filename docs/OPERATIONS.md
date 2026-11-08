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

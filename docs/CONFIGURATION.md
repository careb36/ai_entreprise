# Configuration

Every environment variable of the backend, the LLM proxy, the broker and the workspace daemon. Moved whole from `CLAUDE.md`, whose index is [here](../CLAUDE.md).

## Required Environment Variables

Copy `.env.example` to `.env` and fill in:
- `GEMINI_API_KEY` — Google AI Studio key (used by Architect/PO agents)
- `DEEPSEEK_API_KEY` — DeepSeek key (used by Developer/QA agents)
- `POSTGRES_PASSWORD`, `NEO4J_PASSWORD` — database credentials
- `NEO4J_AUTH=neo4j/<password>` must match `NEO4J_PASSWORD`
- `NEO4J_ENABLED` — optional, default `true` (issue #328). The knowledge
  graph (Neo4j) is optional: the backend never depends on it (compose starts
  it, but the backend does not wait for it; enabled and not reachable yet,
  the backend tries again every `GRAPH_RETRY_SECONDS` for ten minutes,
  `lifecycle._connect_graph_later`, and records the graph from then on).
  Off (`NEO4J_ENABLED=false docker compose up -d --scale neo4j=0`), the
  backend never connects to it, memory
  search and store run on pgvector alone (`POST /v1/memory/store` answers
  what each store kept, `vector` and `graph`, `stored` or `not_recorded`),
  an engram is kept in pgvector with `graph: not_recorded`, the task
  worker's memory of past decisions comes from pgvector, every graph reader
  answers "not recorded" with the reason (`GET /v1/enterprise/lineage`:
  `recorded: false`; `services/platform/memory_graph.py`), never a 500 or a silent
  empty list, `/v1/capabilities` reports `memory_graph` apart from `memory`,
  and `/health/deep` shows `neo4j` as `disabled` without degrading the stack
  (it degrades only when the graph is enabled and unreachable). The
  frontend links a claimed file to its lineage only with `memory_graph`.
- `LLM_PROXY_INTERNAL_TOKEN` — optional, shared by the backend and the LLM proxy.
  An agent whose model is `conn:<id>:<model>` uses that company provider
  connection: the backend sends its URL and key in `X-Provider-Connection`, and
  the proxy (`llm-proxy/connections.py`) honours it only with this token, with
  no fallback to the deployment's own keys. Empty disables company connections.
- `MODELS_CATALOG_URL`, `MODELS_CATALOG_REFRESH_HOURS` — optional. The model
  catalogue (models.dev, `services/models/model_catalog.py`) starts from the snapshot in
  `backend/app/data/models_dev.json` (`scripts/update_models_snapshot.py`) and
  refreshes in the background every 24 hours by default; `0` never calls out.
- `FERNET_SECRET_KEY` — optional. Encrypts `ProjectCredential.encrypted_value`
  at rest. Without it those values are stored with a `plaintext:` prefix and the
  backend warns at startup. Generate one with:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `JWT_SECRET_KEY` — required for authentication. Login returns 503 until configured.
  Generate with: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `AUTH_REQUIRED` — set to `true` to enforce authentication on sensitive routes
- Browser sessions (issue #524; `docs/design/SESSIONS.md`). Login and the SSO
  exchange set HttpOnly cookies instead of returning a token:
  - `ACCESS_TOKEN_EXPIRE_MINUTES` — default `15`. The access token's life, in
    the `fabrique_session` cookie or, for a script that logs in with
    `"token_delivery": "bearer"`, in the response body.
  - `REFRESH_TOKEN_ABSOLUTE_DAYS` — default `7`. A session ends this long after
    sign-in however often it is refreshed; the person signs in again.
  - `REFRESH_REUSE_GRACE_SECONDS` — default `10`. A refresh token presented
    again this soon after its rotation, while its successor is unused (two tabs
    refreshing at once), is answered 409 without revoking; any other reuse
    revokes the session's family.
  - `REFRESH_RATE_LIMIT` — default `30/minute`, per client address, on
    `POST /v1/auth/refresh` (slowapi, `RATE_LIMIT_STORAGE_URI`).
  - `SESSION_COOKIE_SECURE` — default `true`. The cookies are `Secure`, so the
    browser sends them over https only (browsers also treat
    `http://localhost` as secure). Set `false` only to develop over plain
    http on another host name; the backend warns at startup.
  - `CORS_ORIGINS` must list the frontend's origins explicitly: with `*` the
    backend allows no credentials, so a browser on another origin cannot sign
    in (and warns at startup).
- Single sign-on over OpenID Connect (issue #512; `docs/design/SSO.md`; setup in
  `docs/OPERATIONS.md`, Single sign-on). Each company's connection is stored in
  the database by its Owner, not in the environment; these settle the deployment:
  - `SSO_CALLBACK_URL` — default `http://localhost:8000/v1/auth/sso/callback`.
    The redirect URI every company registers at its provider; it must be the
    address the browser reaches the backend's `/v1/auth/sso/callback` at. With
    an `https://` value the binding cookie is `Secure`.
  - `SSO_FRONTEND_URL` — default `http://localhost:5173`. Where the callback
    sends the browser afterwards (`/login/sso`, with a one-time code or an
    error code).
  - `SSO_RATE_LIMIT` — default `20/minute`, per client address, on the start,
    callback and exchange routes (slowapi, `RATE_LIMIT_STORAGE_URI`).
  - `SSO_BREAK_GLASS_EMAILS` — optional, comma-separated. Owners listed here
    may still sign in with a password when their company requires SSO; each
    such sign-in writes an `sso_break_glass_login` audit row.
  - `SSO_ALLOW_PRIVATE_ISSUERS` — default `false`. An issuer (and its token
    and JWKS endpoints) must be https on a host that resolves to public
    addresses; set this for a provider on a private network.
  - `SSO_HTTP_TIMEOUT_SECONDS` (10) and `SSO_CLOCK_SKEW_SECONDS` (60, the leeway
    on the ID token's `exp` and `iat`).
  - The client secret is encrypted with `FERNET_SECRET_KEY`; without that key
    it is stored with a `plaintext:` prefix, as other credentials are.
- Approvals in Slack and Teams (issue #512, phase 4; `docs/design/CHAT_APPROVALS.md`,
  set up per company on the Chat approvals page; `docs/OPERATIONS.md`):
  - `CHAT_APP_URL` — default `http://localhost:5173`. The frontend address the
    chat messages link to (the run's evidence, the task).
  - `CHAT_RATE_LIMIT` — default `120/minute`, per client address, on the
    public inbound endpoints (`/v1/integrations/slack/…`, `/v1/integrations/teams/…`).
  - `FERNET_SECRET_KEY` is required: a connection is refused without it, since
    its bot token, webhook URL and signing secret are stored encrypted.
  - Teams incoming webhook URLs must be https on a Microsoft webhook host
    (`*.webhook.office.com`, `*.logic.azure.com`, `*.powerplatform.com`,
    `*.powerautomate.com`) that resolves to public addresses
    (`WEBHOOK_ALLOW_PRIVATE_HOSTS` relaxes only the address check).
- API tokens (`/v1/auth/tokens`, `services/platform/api_tokens.py`, issue #327): a
  Manager creates one per company, accepted beside the JWT, with a `role`
  (`Viewer` by default, `Manager` at most, never above the creator's own) and
  an expiry (`expires_in_days`, 90 by default, 1 to 365, or `null` for none);
  only its sha256 is stored and the raw token is shown once. A revoked or
  expired token is a 401, a token cannot create tokens (403), and a request
  made with one acts as `api-token:<id>`, the name its audit rows carry.
  Tokens from before roles (migration `0016`) act as a Manager and never
  expire.
- `SUPPORT_ADMIN_EMAILS` — optional, comma-separated account emails. Those users
  see and triage every support ticket and download the support logs (`/support`
  in the frontend, flagged by `support_admin` in `/v1/auth/me`); everyone else
  sees only the tickets reported with their account email.
- `SUPPORT_AGENT_ENABLED` — optional, default `false`. The Support agent
  (`services/management/support_agent.py`) triages new help-desk tickets every
  `SUPPORT_AGENT_INTERVAL_SECONDS` (60): a suggested severity and category,
  stored beside what the reporter chose, and a first reply the reporter sees.
  Off by default because reporting a bug is public and each ticket costs an
  LLM call. A support admin decides a ticket's severity and category in the
  help desk; the decision is stored apart from the reporter's choice and the
  agent's suggestion (`decided_*`, stamped with who changed it only when it
  changes; an empty value clears it), `GET /v1/support/agent_record` counts
  how often the agent's suggestions matched it (its unusable answers count as
  misses, and are counted) beside how often the reporters' own choices did
  (only where a reporter chose: the form presets no impact and asks for no
  category, so neither is made up), and those numbers go into the agent's
  next prompt.
- `REVIEWER_AGENT_ENABLED` — optional, default `false`. The Reviewer agent
  (`services/management/reviewer_agent.py`) reads the evidence of each run another
  producer sent that waits for a person (the Verifier's checks, the claims,
  the artifact as data) every `REVIEWER_AGENT_INTERVAL_SECONDS` (60) and
  suggests the decision in a fixed format: approve or reject, a category and
  a reason, stored on the run (`suggested_*`, `reviewed_by`) apart from the
  approval a Manager records; an unusable answer leaves the run to people.
  A run not verified yet waits `REVIEWER_VERIFY_GRACE_SECONDS` (60) after
  its ingestion, so a producer that verifies right after is judged with the
  verdict.
  `GET /v1/agent_runs/reviewer_record` counts how often its suggestions
  matched the decisions (unusable answers as misses) beside how often the
  Verifier's verdict alone would have (verified → approved, refuted →
  rejected), and those numbers go into its next prompt. Off by default: each
  run costs an LLM call.
- `MANAGER_AGENT_ENABLED` — optional, default `false`. The Manager agent
  (`services/management/manager_agent.py`) reads the inbox every
  `MANAGER_AGENT_INTERVAL_SECONDS` (60): each task blocked for a person or
  held for acceptance, procedure proposal and undecided outside run, with the
  evidence (verdict, checks, risk tier, files, criteria, the ScrumMaster's
  triage; claims as data), and suggests the decision in a fixed format
  (decision, the item's own category, reason), stored as a `ManagerDecision`
  apart from what the client decides; an unusable answer leaves the item to
  the client. It decides alone only what the company's `ManagerPolicy`
  allows (a Manager writes it, `GET`/`PUT
  /v1/companies/{id}/manager_policy`), through a person's code path, audited
  as `Manager agent` with the rule; the client overrides in one click
  (`POST /v1/manager_decisions/{id}/override`). `GET /v1/manager_record`
  counts agreement and overrides by category, and those numbers go into its
  next prompt. Every action a Manager takes in the product is also a rule a
  policy may name (issue #394: merge locally, undo a delivery, send back or
  carry over a blocked task, answer a draft's question, publish, resume after
  credit, approve a specification or an architecture, accept a high-risk
  delivery), each off until named and decided from what the platform
  observed. Off by default: each item costs an LLM call.
- `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`,
  `SMTP_STARTTLS` (true), `SMTP_SSL` (false), `MANAGER_BRIEF_INTERVAL_HOURS`
  (24) — optional. With the Manager agent on and `SMTP_HOST` set, the
  client's brief (`services/management/brief_mail.py`: the deterministic
  `manager_brief.brief` rendered as plain text) goes by mail to the Owners and
  Managers of each company that has a `ManagerPolicy` (by effective role: a
  member a team makes a Manager receives it too), once per interval; each
  send is audited as `manager_brief_sent` with its window and how many people
  it reached (never an address), the next window starts where the last sent
  one ended, and a failed send is retried from the same start at most once an
  hour. One replica sends at a time (`locks.MANAGER_BRIEF`).
- `PUBLIC_API_URL` — optional. The address GitHub uses to reach the backend
  (public host or tunnel). `/v1/capabilities` reports it as `public_api_url` and
  the GitHub page shows it as the webhook Payload URL. It is also the base of
  the Request URLs the Chat approvals page shows for Slack and Teams
  (`http://localhost:8000` when unset).
- `AUDIT_SIGNING_KEY` — optional. Signs audit rows with HMAC-SHA256, and keys
  each company's audit hash chain (`audit_chain.py`: every company row carries
  `chain_seq`, `prev_hash` and `chain_hash`; `GET /v1/audit/chain` shows where
  a deleted, inserted, reordered, altered or moved row breaks it). Without a
  key the chain is unkeyed and says so.
  Generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
- `NATS_BIND` — optional, default `127.0.0.1`. The host address NATS listens on;
  the workspace daemon on this computer connects there. `0.0.0.0` exposes it to
  other machines, which can then ask the daemon to run commands; the backend
  warns at startup when it is not loopback and the bus has no credentials.
- `NATS_BACKEND_PASSWORD`, `NATS_DAEMON_PASSWORD` — optional, both or neither
  (issue #327). With both, `nats/start.sh` starts the broker with
  `nats/nats-server.conf`, one user per component, written by
  `scripts/nats_config.py` (`--check` fails when the committed file is stale,
  `--namespace X` for a namespaced deployment): `backend` publishes
  `workspace.>`, `task.>`, `glassroom.>`, `audit.>`, `system.>` and the
  JetStream API, its replies under `_INBOX.backend`; `daemon` subscribes to
  `workspace.>` and `_INBOX.daemon.>`, answers under `_INBOX.backend.>`
  (never `allow_responses`, which would let it reply to a `workspace.*`
  subject it set itself) and publishes only `workspace.delivered`, `glassroom.*`
  and its own consumer's JetStream calls on the deliveries stream, so it
  cannot send a request (`workspace.execute`…), assign a task, read the
  backend's replies or create, change or delete a stream (it waits for the
  backend's). Only one set stops the broker; neither keeps the old
  unauthenticated bus. The passwords stay in the environment
  (`$NATS_BACKEND_PASSWORD` in the file). Compose gives the backend
  `NATS_USER=backend`; `scripts/start-daemon.*` turn `NATS_DAEMON_PASSWORD`
  into the daemon's user.
- `NATS_USER` / `NATS_PASSWORD`, `NATS_CREDS_FILE` (wins), `NATS_NKEY_SEED`
  (a seed file) — how the backend (`app/nats_auth.py`) and the daemon
  (`vscode-daemon/nats_connect.py`, the same rules) authenticate;
  `start-daemon.sh --creds FILE` / `.ps1 -Creds FILE`, and `nkeys` is installed
  only for a creds file or a seed. Unset, no authentication as before. No log
  line or `/health` field carries a secret.
- `NATS_TLS`, `NATS_TLS_CA`, `NATS_TLS_CERT`, `NATS_TLS_KEY`, `NATS_TLS_VERIFY`
  — optional. The broker requires TLS once `nats/certs` holds `server.crt` and
  `server.key` (`ca.crt` plus `NATS_TLS_VERIFY=true` also requires client
  certificates); `NATS_TLS=true` or `NATS_TLS_VERIFY=true` on the broker make
  those files required (`nats/start.sh` refuses to start without one, never
  falling back to plain text); `NATS_TLS=true` makes the backend and the daemon connect with
  TLS, `NATS_TLS_CA` pins the CA (the backend container reads
  `nats/certs/ca.crt`), `NATS_TLS_CERT`/`_KEY` present a client certificate.
  In the containers `nats/certs` is `/etc/fabrique-nats/certs`, and
  `NATS_BACKEND_TLS_CERT`/`_KEY` and `NATS_DAEMON_TLS_CERT`/`_KEY` name a
  client certificate's files there (compose turns them into those paths).
  `docs/OPERATIONS.md`, "Securing the bus", generates a self-signed CA.
- `FABRIQUE_HEARTBEAT_SECONDS` — daemon side, optional, default `20`, bounded
  to 1..25. How often the daemon publishes `workspace.heartbeat` with its id,
  protocol version and a sequence number (`vscode-daemon/daemon_status.py`;
  the protocol is [`vscode-daemon/protocol/PROTOCOL.md`](../vscode-daemon/protocol/PROTOCOL.md)).
  The broker configuration `scripts/nats_config.py` writes allows the subject;
  a deployment with an older hand-written configuration must allow the daemon
  user to publish `workspace.heartbeat`, or the backend falls back to asking
  `workspace.status`.
- `DAEMON_HEARTBEAT_STALE_SECONDS` / `DAEMON_HEARTBEAT_OFFLINE_SECONDS` —
  backend, optional, defaults `60` / `90`. With no heartbeat observed for that
  long, on the backend's own clock, `GET /v1/workspace/status` reports the
  daemon `stale`, then `offline` (`heartbeat` and `liveness`;
  `services/workspace/daemon_liveness.py`). The offline window also bounds how
  long a daemon that reported an incompatible protocol keeps deliveries
  refused after it was last heard.
- `PROJECTS_ROOT` — only for the optional Linux daemon container (`--profile
  docker-daemon`): the host folder holding your projects, mounted at the same path.
- `DELIVERY_TESTS_ENABLED` — optional, default `false`. After an agent's files are
  written, the daemon runs the project's tests (`DELIVERY_TEST_COMMAND`, or detected
  `npm test` / `pytest`) and the exit status is recorded as evidence; a failing run
  sends the task back for rework. It executes agent-written code on the daemon's machine,
  where `DELIVERY_SANDBOX` says: `host` (default, as before), `container` (an
  ephemeral container per run: worktree mounted, network off unless
  `DELIVERY_SANDBOX_NETWORK`, limits `DELIVERY_SANDBOX_CPUS` / `_MEMORY_MB` /
  `_PIDS`, image `DELIVERY_SANDBOX_IMAGE`, every capability dropped) or `bwrap`.
  A company sets its own (`Company.delivery_sandbox`, and `sandbox_env`: the
  variable names a sandboxed run may see) with `GET`/`PUT
  /v1/companies/{id}/sandbox` (Viewer / Manager). The daemon reports what it
  applied (`test_run.sandbox`) and the Verifier records it as the `sandbox`
  check (`services/workspace/sandbox.py`; `host` is a finding, never a refutation);
  `FABRIQUE_SANDBOX_REQUIRED=container` on the daemon's machine refuses the host
  for everything (`vscode-daemon/sandbox.py`), a request that names no sandbox
  included (#525). The console assistants (`CONSOLE_ASSISTANTS_ENABLED`) run in
  the company's sandbox too, with purpose `assistant`: in `container` or
  `bwrap` they need `DELIVERY_SANDBOX_NETWORK` on (off by default; otherwise
  they are skipped with the reason), the CLI in `DELIVERY_SANDBOX_IMAGE`, and
  the provider's key listed by name in the company's `console_env`. In container mode the report
  names the image that ran (`image_identity`: the content ID and registry
  digest from `image inspect`, observed by the daemon before the run, and the
  container started from that ID; after the run for an image the run pulled;
  host and bwrap give the reason there is none; `vscode-daemon/sandbox_image.py`).
  `DELIVERY_SANDBOX_CACHE` (default `false`) adds a **dependency cache** in
  container mode (`vscode-daemon/sandbox_cache.py`): a volume per project,
  `fabrique-cache-<key>` labelled `dev.fabrique.sandbox-cache=<key>`, mounted
  at `/fabrique-cache` with `PIP_CACHE_DIR`, `npm_config_cache`,
  `YARN_CACHE_FOLDER`, `GOMODCACHE`, `GOCACHE`, `CARGO_HOME` and
  `XDG_CACHE_HOME` pointing into it; kept after a run, removed after a failed
  install, and after three failed installs in a row the project's cache is
  quarantined (not mounted, the reason in each report and in
  `GET /v1/workspace/status`) until `POST /v1/companies/{id}/sandbox/cache/reset`
  (Manager, audited). Only a volume with both the daemon's name and label is
  mounted or removed. Daemon side, `FABRIQUE_SANDBOX_CACHE=off` refuses the
  cache and `FABRIQUE_SANDBOX_CACHE_STATE` (default
  `~/.fabrique-daemon/sandbox-cache`) holds each project's failed installs and
  quarantine. The application check's container mounts the same cache.
- `DELIVERY_BROWSER_IMAGE` — optional, default empty (the application check
  runs in `DELIVERY_SANDBOX_IMAGE`, as before). In container mode, the image the
  application check runs in when its e2e command drives a browser (QA's smoke
  suite, or the e2e command of a profile that serves pages): one built from
  `vscode-daemon/images/browser` (Node, Playwright 1.64.1 from its lockfile and
  the Chrome Headless Shell it launches, each download checked by sha256 for
  amd64 and arm64; `docker build vscode-daemon/images/browser`, and behind a
  TLS-intercepting proxy `--secret id=extra_ca,src=<CA bundle>`). Pin it by
  digest (`registry/name@sha256:…`); the run records the content ID and digest
  it started from either way. Before a browser e2e command the daemon also
  probes for a browser (`vscode-daemon/browser_probe.py`) and `e2e_run` says
  whether one was present.
- `DELIVERY_FULL_LOGS` — optional, default `true`. The install, the test run
  and the application check ask the daemon for their whole output: it uploads
  each stream (up to `FABRIQUE_FULL_LOG_MAX_MB` on the daemon's machine,
  default `50`, 1 to 1024; beyond it the object is cut and says `truncated`)
  to the JetStream object store `FABRIQUE_RUN_LOGS` (prefixed with
  `NATS_NAMESPACE`), and the backend reads each object back and records its
  size and sha256, computed here, as `full_log` on the evidence. Needs
  JetStream; without it, with the setting off or with an older daemon the
  evidence says `full_log: {observed: false, reason}`.
  `DELIVERY_FULL_LOG_MAX_MB` (default `50`) is the largest object the backend
  reads back. Retention is the bucket's, set when the backend starts:
  `FULL_LOG_RETENTION_DAYS` (default `30`; `0` keeps objects) and
  `FULL_LOG_BUCKET_MAX_MB` (default `4096`; beyond it the oldest objects go
  first; `0` for no limit). The janitor does not touch the bucket: the
  evidence keeps each object's sha256 after the object itself expires.
  The daemon also applies a **command policy** before `workspace.execute`
  runs anything (`vscode-daemon/command_policy.py`, issue #357): the
  request's `command_policy` (`{"mode": "allow"|"deny", "allow": [...],
  "deny": [...]}`) and the daemon machine's own `FABRIQUE_COMMAND_DENY`
  (comma-separated patterns, which the request cannot relax). It splits a
  command into its simple commands, `$(...)`/backtick/`<(...)` substitutions
  (checked too, at most `MAX_DEPTH` deep) and redirections, honouring quotes;
  `allow` matches a pattern's words as a prefix of each command's words (never
  a substring) and refuses a redirection other than `2>&1`-style duplication
  or `/dev/null`; `deny` refuses a pattern found in the text or in the
  unquoted words; unreadable text or an unknown mode is refused. A refusal is
  `{"success": false, "refused": true, "error": <reason>}` and nothing runs.
  With neither configured every command runs as before. The backend does not
  send a company policy yet.
  `FABRIQUE_DAEMON_STATE` (daemon side, default `~/.fabrique-daemon/deliveries`)
  is where the daemon keeps each queued delivery's result until the job is
  acknowledged (`vscode-daemon/delivery_journal.py`): a redelivered job replays
  it instead of applying SEARCH/REPLACE edits a second time.
  `FABRIQUE_SNAPSHOT_STATE` (daemon side, default `~/.fabrique-daemon/snapshots`)
  holds the snapshot taken before each delivery without the Git workflow
  (`vscode-daemon/delivery_snapshot.py`, from opencode's snapshots): one bare
  Git store per project, named by the sha256 of its path, never the
  project's `.git`; a Manager undoes the delivery from it
  (`services/release/delivery_undo.py`). A queued delivery replays its snapshot id
  with the journalled result.
  In a sprint planned from a specification it also decides the **acceptance
  tests per criterion** (`services/evidence/acceptance.py`): the runner's per-test lines
  are read back into criterion ids (pytest gets `-v`; name a verbose command
  here for another runner, e.g. `jest --verbose`, `vitest --reporter=verbose`;
  Playwright's list reporter is read too).
- **Stack profiles and the application check** (issue #315,
  `services/workspace/stack_profiles.py`, `services/evidence/app_check.py`,
  `vscode-daemon/app_run.py`). Per detected stack (Node, Python, Go, Rust,
  Java via Maven or Gradle, .NET, PHP, Ruby) a profile names the install,
  test, lint and start commands, the port and the probe path, from the
  daemon's snapshot; `DELIVERY_INSTALL_COMMAND`, `DELIVERY_LINT_COMMAND`,
  `DELIVERY_START_COMMAND`, `DELIVERY_E2E_COMMAND`, `DELIVERY_APP_PORT` (0:
  the profile's), `DELIVERY_APP_PROBE_PATH` (`/`) and `DELIVERY_TEST_COMMAND`
  override it for the deployment, and the company's own
  (`Company.stack_profile`, `GET`/`PUT /v1/companies/{id}/stack_profile`,
  Viewer / Manager) override both; an empty command turns that step off.
  The delivery tests run the resolved profile's test command (a company
  naming only `stack` gets that stack's runner); a detected profile has none
  without a suite (npm's placeholder `test` script, nothing for pytest to
  collect, no `phpunit.xml` or `tests/`, no `Rakefile` or `spec/`), and then
  the older detector looks for another runner's suite. Poetry and Pipenv
  projects run their test, lint and start commands through `poetry run` /
  `pipenv run`. With
  `DELIVERY_TESTS_ENABLED`: `DELIVERY_INSTALL_ENABLED` (true) installs the
  dependencies before the tests when a manifest the daemon wrote has a sha256
  other than the last successful install's in that folder
  (`Company.dependency_installs`, written only after an exit 0 and only for
  the manifests that install covers, `stack_profiles.installed_by`: a
  detected command its own stack's manifests in the project's root, a
  command the company or deployment named every one; the rest are kept as
  `not_covered`, and a delivery that changed only those runs no install and
  says why), through `workspace.execute` in the same sandbox, bounded by
  `DELIVERY_INSTALL_TIMEOUT_SECONDS` (600; the daemon caps a requested
  `timeout_seconds` at 900), with the network as `DELIVERY_INSTALL_NETWORK`
  says (true, for the registry) whatever `DELIVERY_SANDBOX_NETWORK` says:
  the Verifier's `dependencies_installed` check. `DELIVERY_APP_CHECK_ENABLED` (false: it runs agent code) has the
  daemon (`workspace.app.check`) start the application in the sandbox (host
  or container; bwrap and a port already in use are refused, a required
  sandbox is never replaced by the host, the command policy applies), wait
  for its port (`DELIVERY_APP_READY_TIMEOUT_SECONDS`, 60), GET the probe path
  (below 500 passes), send it the security probe (headers, cookies, CORS,
  methods, error bodies, `/.git/HEAD` and `/.env`, at most 40 s; the
  `security_probe` check, `docs/contexts/evidence.md`), run the profile's e2e
  command with `APP_URL` (`DELIVERY_E2E_TIMEOUT_SECONDS`, 300), stop it and
  reply with the log's tail: the `application_started` and `e2e_run` checks.
  The backend waits for the reply beyond those bounds for the container's
  launch (120 s), the security probe (60 s) and teardown; the container is removed by name whatever happens after its
  launch; on the host the application's process group is stopped (on
  Windows the Job Object it was started suspended into, created with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, which also holds the children of a
  wrapper that already exited; `vscode-daemon/win_job.py`, through `ctypes`;
  `taskkill /T /F` when no job could be made), `stopped` holds only once the port
  no longer answers, and a host e2e command runs in a group of its own that
  is killed at its timeout and when it ends. It is skipped after a failed
  install or failed tests, and for QA's acceptance-test task (it changes no
  application code), and a sprint's integration runs the tests alone. A refuted install, start or e2e run sends
  the task back like failed tests (`delivery_gate.CODE_PROBLEMS`). In a
  container an install keeps only what it writes into the worktree.
  **Browser smoke tests per criterion** (`services/evidence/browser_smoke.py`): a
  profile's `web` says the project serves pages a browser renders (detected:
  an `.html` file in the root, `public/`, `static/`, `src/`, `templates/` or
  `www/`, or a browser framework among `package.json`'s dependencies,
  `stack_profiles.serves_pages`; a company sets `"web": true` for a project
  that has neither yet). When the check is on and the resolved profile has a
  start command, a port and `web` (`serves_web`), QA's test-writing task in
  a sprint planned from a specification is also asked (`## Browser checks to
  write`, through `spec_planning.task_requirements_context`) for one
  Playwright test per criterion a person sees in the browser,
  `e2e/req_1_2.spec.ts`, each test titled with the criterion id, opening
  `process.env.APP_URL`. Once QA's deliveries wrote such files (as the daemon
  read them back, `acceptance["test_hashes"]`) and no e2e command is
  configured (an empty company `e2e` turns it off), the check runs
  `npx --no-install playwright test e2e --reporter=list` against the running
  app (`app_run["e2e_source"] == "smoke"`); the daemon keeps the head of a
  long e2e output too (`output_head`), where the per-test lines are, and
  `read_back` maps them to criterion ids (`criteria`, only those a test
  named; a failing one fails the criterion in `criteria_tests`, and failures
  of criteria not implemented yet are excused as for the unit tests, but only
  when the failure set is known whole, `failures_complete`: the daemon's
  bound did not cut the stream (`stream_truncated`), at most `FAILING_CAP`
  (50) failed, and the output was kept whole or every test Playwright
  announced (`Running N tests`) has its line; otherwise a non-zero exit is
  refuted). Only test files are guarded as acceptance tests
  (`criteria_tests.is_test_file`: `test_*`, `*_test`, `*.test.*`,
  `*.spec.*`…); a `package.json` or `playwright.config.*` QA delivered stays
  the implementation's to change. A browser runtime missing on the daemon's
  machine (the launch errors themselves: `Executable doesn't exist at`,
  `browserType.launch:`, missing system libraries, `@playwright/test` not
  found, `npx --no-install` refusing a package the project lacks, no `npx`,
  exit 127; never a mere mention of the installer) is
  `runtime_missing`: `e2e_run` is unverifiable with the reason, never a pass
  and never a refutation, and the task QA approves waits for a person at
  every autonomy level (`sprint_control.person_acceptance_reason`,
  `browser_smoke.UNOBSERVED_REASON` in the hold note; the history check
  wants a person's acceptance after it), never sent back for rework.
- `CONSOLE_PRODUCER_TIMEOUT_SECONDS` (600; the daemon caps a request at 900)
  — a console producer's wall clock (`services/producers/console_producer.py`). Nothing
  to enable globally: a Manager names a CLI for a role on the Models page. It
  runs only in a `container` or `bwrap` sandbox with `DELIVERY_SANDBOX_NETWORK`
  on, and sees only the variables in the company's `sandbox_env` (list the
  provider's key there).
- `MCP_MAX_TOOL_ROUNDS` (6), `MCP_TOOL_TIMEOUT_SECONDS` (60),
  `MCP_CONNECT_TIMEOUT_SECONDS` (20), `MCP_RESULT_MAX_CHARS` (4000) — the
  MCP tool loop (`services/tools/mcp_tools.py`): how many rounds of tool calls a
  task may make before the model must answer, how long one call and one
  connection may take, and how much of a result the model is shown. Nothing
  to enable: a company with no MCP server configured runs as before.
- `DB_AUTO_UPGRADE` — optional, default `false`. Runs the Alembic migrations
  to head when the backend starts, for a local database run by hand. The
  container's entrypoint and the e2e run `alembic upgrade head` themselves,
  and the backend refuses to start on a database behind head
  (`app/migrations.py`, `CONTRIBUTING.md` §5).
- `RETENTION_OUTBOX_DAYS` (7), `RETENTION_AGENT_RUNS_DAYS`,
  `RETENTION_KPI_TURNS_DAYS`, `RETENTION_GLASSROOM_DAYS` (0, keep all) — what
  the janitor deletes (`backend/janitor/retention.py`): delivered outbox
  events; superseded run versions with their verifications (never the latest
  version, nor one a person reviewed, approved, commented on or published);
  KPI turn rows of `audit_log` outside a company's hash chain (a chained row
  is never deleted, and counted as kept); task events of closed sprints.
  There is no audit retention: `docs/OPERATIONS.md` says how to archive a
  chain with its rows. `BACKUP_DIR`, `BACKUP_KEEP_DAYS` (14) and
  `PG_CONTAINER` steer `scripts/backup.sh`.
- `DEVELOPER_TOOLS_ENABLED` (true), `DEVELOPER_TOOL_ROUNDS` (8) — the
  Developer's workspace tools (`services/tools/developer_tools.py`): the roles that
  deliver files may list, read and search the project through the daemon, and
  run its tests with `DELIVERY_TESTS_ENABLED`, for that many rounds before
  delivering; every read is evidence.
- `REPO_MAP_MAX_CHARS` (3000) — the repository map (`services/tools/repo_map.py`,
  daemon `vscode-daemon/repo_map.py`): how many characters of the project's
  definitions, ranked by use, go into the Developer's, QA's and the
  Architect's prompts beside the tree; `0` leaves them out.
  `FABRIQUE_MAP_STATE` (daemon side, default `~/.fabrique-daemon/maps`) is
  where the daemon caches the map per file set.
- `CODE_GRAPH_MAX_CHARS` (2500) — the `## Related code` section of the
  Developer's prompt (`services/tools/code_graph.py`, daemon
  `vscode-daemon/code_graph.py`, issue #438): the files the task's approved
  architecture modules and earlier writes lead to, what they use and what
  uses them (the daemon's `workspace.related`, from the repository map's
  parse cache), and the recent runs that wrote them, cut at that many
  characters; `0` leaves the section out and asks the daemon nothing.
- `SPRINT_BOARD_MAX_CHARS` (2000) — the `## Sprint board` section of every
  task prompt (`services/loop/worker/sprint_notes.py`, issue #439;
  `docs/design/SPRINT_BOARD.md`): the sprint's active notes relevant to the
  task (by the files, modules and requirements it touches), newest first,
  each with what the Verifier's records observed about the files it names,
  then how to write one; the whole section stays within that many
  characters (the header and the format take about 800 and are always
  there, the notes get the rest); `0` leaves the section out. Prompt priority 4: `PROMPT_BUDGET` cuts
  it before the repository, the related code and the requirements.
- `WIKI_ENABLED` (true) — the project wiki (issue #445,
  `docs/design/PROJECT_WIKI.md`): the `## Project wiki` section of every
  Developer-path prompt and the compilation and lint at sprint close. Off, the
  prompt reads the vector memory alone (the bench's baseline) and no page is
  compiled; the pages stay and the API serves them.
- `WIKI_MAX_CHARS` (3000) — the wiki section's size: the pages in force of the
  task's modules and concepts, then the decisions and the glossary, whole pages
  that fit (the first one cut short rather than left out); `0` leaves it out.
  Prompt priority 4, placed before the memory: `PROMPT_BUDGET` cuts it after the
  memory and before the repository. When a page applies, the vector memory is
  not searched.
- `WIKI_COMPILE_MAX_PAGES` (5), `WIKI_PAGE_MAX_CHARS` (6000) — at sprint close,
  at most that many pages compiled by a model (the decisions page, written
  without one, is apart), each answer at most that many characters (a longer
  one is rejected and recorded). Whether pages are also written to
  `docs/wiki/` is a company setting (`PUT /v1/companies/{id}/wiki-settings`,
  Manager, off by default), not an environment variable.
- `EDIT_LINT_ENABLED` (true), `EDIT_LINT_TIMEOUT_SECONDS` (60) — after a
  `workspace__edit`, the stack profile's lint command on that file
  (`services/tools/edit_lint.py`), its errors in the same turn as the edit's result.
- `TOOL_HISTORY_KEEP_ROUNDS` (3) — the tool loop (`services/tools/tool_history.py`):
  results older than the last that many rounds go to the model as one line
  each; the evidence keeps them whole. `0` keeps every result whole.
- `PROMPT_BUDGET_<ROLE>`, `PROMPT_BUDGET` — optional, unset by default (#443).
  The Developer's task prompt in estimated tokens (characters / 4) for that
  role (upper-cased, other characters as `_`: `PROMPT_BUDGET_BACKENDDEVELOPER`),
  else for every role (`services/loop/worker/prompt_budget.py`). Over it,
  whole sections are left out from the least needed (console assistant notes,
  then memory and the sprint's completed tasks, lessons, the repository, the
  requirements, standards and previous attempt); the format, the task, the
  Architect's brief and review feedback never are, and the prompt names what
  was left out. Unset or `0`: nothing is cut. Either way each section's weight
  is recorded on the run's evidence as `prompt_sections`.
- `AGENT_MODEL_POLICY` — optional, empty by default (issue #319). The
  deployment's routing policy by task difficulty, JSON:
  `{"bands": {"low": "<model>", "medium": "fast", "high": "<model>"}, "label": "cheap-easy"}`.
  A band key is a difficulty band of `services/models/difficulty.py` (`low`
  1-2, `medium` 3, `high` 4-5) or a range of scores (`"1-2"`, `"3-5"`); a
  value is a model id, a company connection (`conn:<id>:<model>`) or a proxy
  tier name (`fast`, `powerful`…), which runs on the role default since the
  proxy serves no model by that name. `label` names the policy in the routing
  record and the bench (else a fingerprint of the bands). A company's own
  policy (`Company.model_policy`, the Models page, `PUT
  /v1/companies/{id}/model_settings` with `model_policy`) replaces it for
  that company. A policy that cannot be read is ignored with a warning (the
  role defaults apply), a band that cannot be read is dropped with a warning;
  an agent's, a role's or the company's chosen model is never re-routed.
  Unset: every task runs on its role's model, as before.
- `WEBHOOK_ALLOW_PRIVATE_HOSTS` — optional, default `false`. A company's
  notification webhook (`services/integrations/webhooks.py`) must be an https URL whose host
  resolves to public addresses, checked when it is saved and before every post;
  `true` allows a private, loopback or link-local host (a receiver on the LAN).
- `RESEARCH_TOOLS_ENABLED` (true), `RESEARCH_ROLES` (Architect, PO, SecOps,
  TechWriter, Designer), `RESEARCH_ALLOWED_HOSTS` (empty), `RESEARCH_TOOL_ROUNDS` (6) —
  the research tools (`services/tools/research_tools.py`): the roles that decide
  read official documentation, the package registries and OSV through the
  backend, from the default hosts plus these and the company's own; every
  source is evidence. `RESEARCH_PYPI_URL`, `RESEARCH_NPM_URL`,
  `RESEARCH_CRATES_URL`, `RESEARCH_GO_URL` and `RESEARCH_OSV_URL` may point
  at a mirror (the e2e points PyPI at its fake).
- `GROUNDING_LOOKUPS_ENABLED` (true), `GROUNDING_MAX_LOOKUPS` (20) — the
  Verifier's `grounded` check (`services/evidence/grounding.py`): when a
  delivery writes a manifest, the backend asks each registry for its
  dependencies through `services/tools/registry_lookup.py` and records the
  answers on the evidence as `registry_lookups`; verification then reads them
  and never reaches the network. Off, or over the bound, a dependency without
  an answer is a finding, never a refutation.

## The desktop app

Read by the Electron main process (`frontend/electron/`) when it starts, not
by the backend:

- `BACKEND_URL` — default `http://localhost:8000`. The backend the packaged
  app's local server forwards `/v1/*` to (issue #524; `docs/design/SESSIONS.md`,
  Desktop app). It must be https, or plain http only on this machine
  (`localhost`, 127.0.0.0/8, `[::1]`), since the session cookies travel on that
  connection; anything else stops the app at launch with the reason. The app serves its page and that API from one origin,
  `http://127.0.0.1:<port>` with a port picked at each launch, so the backend
  needs no `CORS_ORIGINS` or `SSO_FRONTEND_URL` entry for it and
  `SESSION_COOKIE_SECURE` stays on. For SSO, `SSO_CALLBACK_URL` must keep the
  path `/v1/auth/sso/callback`.
- `ELECTRON_DEV_URL` — default `http://localhost:5173`. The Vite dev server
  `npm run electron:dev` loads.

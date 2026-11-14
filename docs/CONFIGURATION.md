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
  Managers of each company that has a `ManagerPolicy`, once per interval; each
  send is audited as `manager_brief_sent` with its window and how many people
  it reached (never an address), the next window starts where the last sent
  one ended, and a failed send is retried from the same start at most once an
  hour. One replica sends at a time (`locks.MANAGER_BRIEF`).
- `PUBLIC_API_URL` — optional. The address GitHub uses to reach the backend
  (public host or tunnel). `/v1/capabilities` reports it as `public_api_url` and
  the GitHub page shows it as the webhook Payload URL.
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
  for everything (`vscode-daemon/sandbox.py`).
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
  (below 500 passes), run the profile's e2e command with `APP_URL`
  (`DELIVERY_E2E_TIMEOUT_SECONDS`, 300), stop it and reply with the log's
  tail: the `application_started` and `e2e_run` checks. The backend waits
  for the reply beyond those bounds for the container's launch (120 s) and
  teardown; the container is removed by name whatever happens after its
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

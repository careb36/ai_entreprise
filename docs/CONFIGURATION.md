# Configuration

Every environment variable of the backend, the LLM proxy, the broker and the workspace daemon. Moved whole from `CLAUDE.md`, whose index is [here](../CLAUDE.md).

## Minimal configuration: DeepSeek only

The owner's standing decision (2026-11-20, issue #751): La Fabrique runs on
DeepSeek, and no other configuration may stop the work. Among the providers
and integrations, `DEEPSEEK_API_KEY` is the only one a sprint needs:

```bash
cp .env.example .env
# Set DEEPSEEK_API_KEY, and the platform's own secrets: POSTGRES_PASSWORD,
# NEO4J_PASSWORD (or NEO4J_ENABLED=false), JWT_SECRET_KEY.
docker compose up -d
```

With nothing else set:

- every role plans, executes and reviews on DeepSeek Pro or Flash
  (`services/models/model_factory.py`);
- a request that DeepSeek answers with 429 or 5xx (or an empty answer) moves
  to DeepSeek's other model (Pro to Flash, Flash to Pro) and never to another
  provider, even when another provider's key is set, unless
  `LLM_FALLBACK_PROVIDERS` names it (below);
- memory embeds with a local semantic model run on CPU in the proxy
  (multilingual-e5-small; DeepSeek has no embeddings endpoint): no key, no
  outside call, meaning found in Spanish and English; the hash vector stands
  in, reported as degraded, when the model cannot load;
  `EMBEDDING_PROVIDER=gemini` chooses Gemini instead;
- the knowledge graph, GitHub token, mail, error reports, the Manager,
  Support and Reviewer agents are off with a named reason, and nothing waits
  for them.

What is on, off or degraded, and why, is logged once at startup by the
backend and by the proxy (`Effective capabilities: …`, names and reasons, never
a value), served by the `capabilities` section of `/health/deep` and shown on
the Models page (Capabilities). The audit behind this is
[`docs/design/DEEPSEEK_ONLY.md`](design/DEEPSEEK_ONLY.md).

`POSTGRES_PASSWORD` and `JWT_SECRET_KEY` are the platform's own secrets, not
integrations: without `JWT_SECRET_KEY` the backend still starts but login
answers 503, and the Capabilities list says so (`auth: off`).

## Required Environment Variables

Copy `.env.example` to `.env` and fill in:
- `DEEPSEEK_API_KEY` — secret, set in `.env`; read by the LLM proxy
  (`llm-proxy/app.py`), which compose gives it to (the backend never holds a
  provider key). The only key the default team needs: the role defaults in
  `services/models/model_factory.py` (`ROLE_MODEL_MAP`) put the PO, Architect
  and SecOps, and the Reviewer agent, on DeepSeek Pro (`deepseek-v4-pro`), and
  every other role (ScrumMaster, Developer, QA, TechWriter, BackendDeveloper,
  FrontendDeveloper, DevOps, Designer, DataEngineer, the EngramProcessor) and
  the Manager and Support agents on DeepSeek Flash (`deepseek-flash`). Unset,
  the proxy logs `DEEPSEEK_API_KEY is not set` at startup and lists none of
  DeepSeek's models, so no default role can run.

  **Where the data goes.** DeepSeek's API is operated from, and processes
  requests in, China. A prompt can carry what the agents read: specifications,
  code, tickets. Where that includes personal data (yours, your users' or a
  client's), sending it is a transfer to a third country under GDPR arts.
  44–49 (a transfer impact assessment and a transfer mechanism), and a data
  protection impact assessment (art. 35) is likely to apply. DeepSeek's models
  are open weights: the alternative is to serve them on a host in the EU or
  the US, or on your own hardware, and give the company a provider connection
  to it (the Models page; `AGENT_MODEL_<ROLE>` or an agent's model then names
  it), which keeps the prompts under your control.
- `QWEN_API_KEY` — optional secret, set in `.env`; read by the LLM proxy. No
  default role runs on Qwen. It serves `qwen*`/`qwq-*`/`qvq-*` model ids an
  agent, a role variable or a policy names.
- `GEMINI_API_KEY` — optional secret, set in `.env`; read by the LLM proxy. No
  default role runs on Gemini. It serves `gemini-*`/`gemma-*` model ids an agent,
  a role variable or a policy names, and the proxy's `/v1/embeddings` only when
  `EMBEDDING_PROVIDER=gemini` (below). The key alone sends no memory and no
  failed request to Google.
- `NEBIUS_API_KEY` — optional secret, set in `.env`; read by the LLM proxy. The
  Nebius Token Factory key, needed only for the roles `NEMOTRON_ROLES` names or
  a model id with a vendor segment (`nvidia/…`; README.md, the routing table).
  `NEBIUS_PROXY_URL` — default `https://api.tokenfactory.nebius.com/v1` (in the
  proxy and in compose): the OpenAI-compatible base URL it is sent to, for a
  Serverless Endpoint of your own. Not a secret.
- `POSTGRES_PASSWORD`, `NEO4J_PASSWORD` — database credentials (secrets, set in
  `.env`)
- `POSTGRES_USER` (default `factory`), `POSTGRES_DB` (default `factory`),
  `POSTGRES_HOST` (default `localhost`), `POSTGRES_PORT` (default `5432`) — where
  the backend (`config.py`, `app/database.py`), the janitor
  (`backend/janitor/main.py`) and the LLM proxy's quota store
  (`llm-proxy/quota.py`) find Postgres. Compose sets `POSTGRES_HOST=postgres`
  and `POSTGRES_PORT=5432` for each of them and takes the user and database
  name from `.env` (`${POSTGRES_USER:-factory}`, `${POSTGRES_DB:-factory}`),
  which also create them in the `postgres` container. Not secrets.
- `NEO4J_URI` (default `bolt://localhost:7687`; `.env.example`
  `bolt://neo4j:7687`) and `NEO4J_USER` (default `neo4j`) — where and as whom
  the backend reaches the knowledge graph (`config.py`, `app/database.py`,
  `services/platform/engram_processor.py`). Not secrets; the password is
  `NEO4J_PASSWORD`.
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
  A key shorter than the algorithm's hash (32 bytes for `HS256`, 48 for
  `HS384`, 64 for `HS512`) counts as unset (issue #541, WSTG-SESS-10).
- `AUTH_REQUIRED` — set to `true` to enforce authentication on sensitive routes
- `JWT_ALGORITHM` — default `HS256`; only `HS256`, `HS384` or `HS512` is
  accepted, anything else (`none`, an asymmetric name) refuses to sign or
  verify. `JWT_ISSUER` (default `la-fabrique`) and `JWT_AUDIENCE` (default
  `la-fabrique-api`; the Glassroom tickets use `<JWT_AUDIENCE>:glassroom`) go
  into every token and are required when one is checked (issue #541). Two
  deployments that share a `JWT_SECRET_KEY` must not share them. Changing
  either ends every access token and stream ticket at once, as rotating the
  key does; browser sessions renew theirs from the refresh cookie.
- Password sign-in and registration (issue #541; SECURITY.md §3.1):
  - `LOGIN_RATE_LIMIT` — default `10/minute`, per client address, on
    `POST /v1/auth/login`; `REGISTER_RATE_LIMIT` — default `5/minute` on
    `POST /v1/auth/register` (slowapi, `RATE_LIMIT_STORAGE_URI`).
  - `LOGIN_FAILURES_BEFORE_BACKOFF` (5), `LOGIN_FAILURE_WINDOW_SECONDS` (900),
    `LOGIN_BACKOFF_MAX_SECONDS` (900) — the per-account backoff: after that
    many failures within the window, each further failure locks the
    account's password sign-in for twice as long as the last (2 s, 4 s, …),
    at most the maximum; a locked account answers 429 with `Retry-After`. Per
    process.
  - `TRUSTED_PROXY_IPS` — default empty (trust no proxy). The reverse proxies
    whose `X-Forwarded-For` is believed: comma-separated addresses or CIDR
    networks (`10.0.0.5`, `172.18.0.0/16`, `2001:db8::1`). A request whose
    connection comes from one of them is treated as coming from the
    right-most address in the header that is not itself listed; from anyone
    else the header is ignored. Every per-address limit above keys on that
    address, so behind nginx or Traefik leave it empty and they key on the
    proxy (one bucket for everybody); set it and they key on each client. A
    malformed entry stops the backend at startup. The image runs uvicorn with
    `--no-proxy-headers` so this is the only rule; how to set it:
    `docs/OPERATIONS.md`, Behind a reverse proxy.
  - `PASSWORD_MIN_LENGTH` — default `12`. The shortest password a person may
    choose, in characters (WSTG-v42-ATHN-07), checked at registration (and
    by any route that sets a password); the bcrypt limit of 72 bytes in
    UTF-8 stays the maximum. Never checked at sign-in: an account whose
    password predates the rule keeps signing in with it.
  - Email addresses are stripped and lower-cased wherever they are compared
    (registration, sign-in, invitations, lookups; `services/contracts/emails.py`),
    so `Ana@x` and `ana@x` are one account. Nothing to configure.
- Invitations (WSTG-v42-IDNT-04; `docs/contexts/platform.md`):
  - `INVITATION_TTL_HOURS` — default `168` (7 days). How long an invitation
    to a company stays acceptable; after that the invitee gets 410 and the
    inviter sends a new one.
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
  - DNS verification of the allowed domains (`docs/design/SSO.md`, Domain
    verification): SSO creates or links accounts only in a domain whose TXT
    record `_la-fabrique-verify.<domain>` holds the token the backend issued.
    - `SSO_LEGACY_DOMAIN_GRACE` — default `true`. Domains listed before the
      upgrade to migration `0049` are `legacy`: while this is on they keep
      creating and linking accounts, unverified, for
      `SSO_LEGACY_DOMAIN_GRACE_DAYS` (default `30`) counted from the upgrade,
      unless another company verified the domain. Set `false` to require
      verification at once. Domains added after the upgrade are never legacy.
    - `SSO_DOMAIN_RECHECK_HOURS` — default `24`. How often every domain is
      looked up again (by one replica, under an advisory lock): a record that
      is definitely gone unverifies its domain, a newly published one verifies
      it. `0` turns the re-check off (the Owner's "Check" still works).
    - `SSO_DNS_TIMEOUT_SECONDS` — default `5`. The whole lookup's deadline.
    - `SSO_DNS_NAMESERVERS` — optional, comma-separated IP addresses of the
      resolvers to ask instead of the system's (`/etc/resolv.conf`). The
      backend does not validate DNSSEC itself; name a validating resolver here
      if you need that. An entry that is not an IP address makes every lookup
      `unavailable`.
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
  expired token is a 401, and so is one above its creator's current effective
  role in the company or whose creator is no longer a member (audited
  `api_token_refused` with the reason, at most once per token and hour; the creator is matched by account id, `created_by_user_id`, migration `0048`), a token cannot create tokens (403), and a request
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
- `AUTO_SELF_IMPROVEMENT_ENABLED` — optional, default `false` (decision 8 of
  `docs/design/rsi-loop.md`). At autonomy 3, a closed sprint gets a follow-up
  sprint planned by a model (`services/loop/worker/close.py`). It runs with no
  protected-path guard, so it is off unless a company turns it on, and never
  runs for a company with protected paths on (below); the finding-driven cycle
  of that design replaces it.
- Protected paths are a company setting, not an environment variable:
  `companies.protected_paths_enabled` (migration `0041`, false for every
  company) marks the company that hosts the factory itself. A signed-in Owner
  turns it on or off (`PUT /v1/companies/{id}/protected_paths`, never an API
  token or the Manager agent; audited `protected_paths_setting_changed`), on
  the Duties and access page. With it on, a delivery whose observed paths
  match `services/contracts/protected_paths.py` (the running backend's list)
  is high risk with the area `grader` and waits for a person; a local merge
  of a sprint's integration whose git diff touches a protected path is refused
  until a signed-in Owner names each such path in an approving review of that
  commit (`protected_paths_ack`, migration `0042`); and the model-planned
  follow-up above does not run for such a company, whatever
  `AUTO_SELF_IMPROVEMENT_ENABLED` says (audited `follow_up_sprint_skipped`).
- `RSI_ENABLED` — optional, default `false`. The self-improvement cycle in the
  backend (`services/management/rsi_cycle.py`, PR 10 of
  `docs/design/rsi-loop.md`): off, no cycle starts and a running one stops at
  its next step, its sprint with it (REQ-24). It also needs the company to be
  marked as hosting the factory (protected paths on, above); there is no
  second company flag. With it on, the backend enforces decisions 4 and 7,
  each refusal audited `rsi_cycle_refused` with its reason:
  `RSI_CYCLES_PER_DAY` (1) cycles started per company and UTC day, one at a
  time; `RSI_STOP_AFTER_CYCLES_WITHOUT_KEEP` (3): after that many cycles in a
  row without a keep nothing starts until a signed-in Owner resumes
  (`POST /v1/companies/{id}/rsi/resume`); `RSI_MAX_DIFF_FILES` (20) and
  `RSI_MAX_DIFF_LINES` (600): the cap on C's diff, read from git through the
  workspace daemon, over which the cycle is unmeasured and waits for a
  person; `RSI_FIRST_SPRINT_TOKENS` (0, unknown): the reported tokens of the
  first manual iteration's sprint, without which no cycle starts, and
  `RSI_CYCLE_TOKEN_BUDGET` (0: the cap), a cycle's budget in reported tokens,
  capped at twice `RSI_FIRST_SPRINT_TOKENS`; passing it ends the cycle
  `budget_exceeded` and stops its sprint. `RSI_FILES_ROOT` (empty) is the
  folder holding the cycles' work folders (`<root>/<cycle_id>/`, or the root itself), under which the backend reads a cycle record's kept files to hash
  them itself when an Owner imports the record
  (`POST /v1/companies/{id}/rsi/cycles/import`); empty, every hash stays the
  runner's claim. A kept cycle is merged by a person (`merge_self_change` is
  in `ALWAYS_ESCALATE`), recorded by a signed-in Owner at C's sha.
- `BENCH_REPOSITORY_DIR` — optional, default empty. The checkout whose
  `bench/scorecards` (and its `real/` and `ablation/` folders) the /bench page
  reads, read-only, through `GET /v1/bench/scorecards`
  (`services/models/bench_scorecards.py`, issue #685). Empty is the backend's
  own checkout when it runs from one; in the backend's image there is none, so
  the page says no scorecards are available until this names a folder holding
  a checkout's `bench/scorecards` (for example a read-only mount of it). Only
  `*.json` files directly in those folders are read, a symbolic link or a file
  over 2 MB is reported unreadable, and nothing is written.
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
  `MANAGER_BRIEF_CHECK_SECONDS` (300) is how often that loop looks for a due
  brief, and `SMTP_TIMEOUT_SECONDS` (20) bounds one connection to the mail
  server. `SMTP_PASSWORD` is a secret: set it in `.env`.
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
  The factory holds this key, so the chain alone does not stop the factory
  rewriting itself: anchor its head outside (issue #542,
  `docs/design/AUDIT_ANCHORS.md`). Anchors have no environment variable; an
  Owner sets them per company (`PUT /v1/audit/anchors/settings`, the Audit
  anchors page): cadence (15 to 1440 minutes, 60 by default), posting to the
  company's webhook, the RFC 3161 authority's URL and certificate, and the
  customer's Ed25519 public keys.
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
  `start-daemon.sh --creds FILE` / `.ps1 -Creds FILE` (`nkeys`, which signs
  with a creds file or a seed, is in the daemon's hash-pinned lock). Unset, no authentication as before. No log
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
  `NATS_BACKEND_TLS_CERT` / `NATS_BACKEND_TLS_KEY` and `NATS_DAEMON_TLS_CERT` /
  `NATS_DAEMON_TLS_KEY` (compose only, default empty: none) name a client
  certificate's files there (compose turns them into those paths). The key
  file is a secret: keep it in `nats/certs`, never in `.env`.
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
- `NATS_URL` — default `nats://localhost:4222` in the backend (`config.py`) and
  the daemon (`vscode-daemon/daemon.py`). The broker's address. Compose sets
  `nats://nats:4222` for the backend and the daemon container;
  `scripts/start-daemon.*` leave it at localhost. Not a secret: credentials
  go in the variables above, never in the URL.
- `PROJECTS_ROOT` — only for the optional Linux daemon container (`--profile
  docker-daemon`): the host folder holding your projects, mounted at the same
  path. Unset, compose mounts `$HOME` (or `/tmp` when `HOME` is unset too).
- `CONTAINER_WORKSPACE_ROOT` — compose only, default `/workspace`. The path
  inside the backend and daemon containers where compose mounts this
  repository; compose passes it on as the two variables below.
- `AGENT_WORKSPACE_ROOT` — backend, default unset (compose:
  `${CONTAINER_WORKSPACE_ROOT:-/workspace}`). Where a company's files are
  written when it has no discovered project folder (a self-improvement sprint
  on this repository): `services/loop/worker/deliver.py` and
  `services/release/integration.py` use the company's `ProjectDiscovery` path
  first. Unset and no project, the files are not sent and the evidence says
  `workspace path not configured`.
- `WORKSPACE_ROOT` — daemon side, default `~/factory-workspaces`
  (`vscode-daemon/daemon.py`; compose's daemon container:
  `${CONTAINER_WORKSPACE_ROOT:-/workspace}`). The folder a request that names
  no `workspace_root` writes into; the daemon creates it when missing.
- `FABRIQUE_EDIT_STATE` — daemon side, default `~/.fabrique-daemon/edits`
  (`vscode-daemon/workspace_edit.py`). Where the first edit of a file in a
  task's tool loop keeps the file's original bytes until
  `workspace.edit.finish` puts them back.
- `FABRIQUE_EXEC_SHELL` — daemon side, default unset (`vscode-daemon/shell_exec.py`):
  the shell a host-mode command runs in. Unset: `/bin/zsh` when it exists, else
  `/bin/sh`; on Windows `COMSPEC` (else `cmd.exe`), and a `powershell`/`pwsh`
  path runs with `-NoProfile -NonInteractive -Command`.
- `FORGE_POLICY_MAX` — optional, default `publish`, read by the backend
  (`config.py`, `services/platform/forge_policy_setting.py`) and, separately,
  by the workspace daemon (`vscode-daemon/forge_gate.py`; unset there is
  `publish`). The highest forge policy any company may have: `none`, `fetch`
  or `publish` (issue #727). On the backend it caps each company's
  `forge_policy` (the stored value is kept; the lower one is in force) and
  refuses a wider value with 409. On the daemon it caps whatever a request
  asks for, so `none` on a computer means it never fetches, pushes or reads a
  request from a forge. An unknown value reads as `none`. An air-gapped
  install sets `none` on both. `GET /v1/capabilities` names it
  (`forge_policy_max`), so the New company form offers nothing above it.
- `GIT_CHECK_RATE_LIMIT` — optional, default `10/minute`, read by the
  backend (`routers/company_sync.py`): how often one account may call
  `POST /v1/workspace/git_check`, the New company form's folder check and
  "Check remote" (issue #745; slowapi, `RATE_LIMIT_STORAGE_URI`).
- `RUN_WINDOW_RECHECK_SECONDS` — optional, default `300`, read by the backend
  (`config.py`, `services/loop/run_window_gate.py`). The longest the run-window
  worker sleeps between two looks at the sprints whose work waits for their
  company's run window (issue #736). It wakes at the earliest window boundary
  when that comes sooner, and never looks more often than once every 60
  seconds (a smaller value reads as 60). The window itself is a company
  setting, not a variable: an Owner sets it on the Models page or with
  `PUT /v1/companies/{id}/run_window`.
- `FABRIQUE_SANDBOX_RUNTIME` — daemon side, default unset
  (`vscode-daemon/sandbox.py`): the container runtime of `DELIVERY_SANDBOX=container`,
  a command name or path. Unset, the first of `docker` and `podman` on the
  `PATH`; none found makes the run unavailable with the reason.
- `CONSOLE_ASSISTANT_COMMANDS` — daemon side, default `copilot,claude`
  (`vscode-daemon/daemon.py`). The CLIs the daemon reports on
  `workspace.capabilities`, kept for older backends: since the console
  assistants were removed (issue #750) the backend never asks.
- `DELIVERY_TESTS_ENABLED` — optional, default `false`. After an agent's files are
  written, the daemon runs the project's tests (`DELIVERY_TEST_COMMAND`, or detected
  `npm test` / `pytest`) and the exit status is recorded as evidence; a failing run
  sends the task back for rework. The backend waits `DELIVERY_TEST_TIMEOUT_SECONDS`
  (default `130`; the daemon stops a command after 120 s) for the run's answer.
  It executes agent-written code on the daemon's machine,
  where `DELIVERY_SANDBOX` says: `container` (the default since issue #766:
  an ephemeral container per run, worktree mounted, network off unless
  `DELIVERY_SANDBOX_NETWORK`, limits `DELIVERY_SANDBOX_CPUS` (2),
  `DELIVERY_SANDBOX_MEMORY_MB` (2048) and `DELIVERY_SANDBOX_PIDS` (512),
  every capability dropped), `host` (needs an Owner's `host_sandbox`
  acknowledgement per company) or `bwrap`. The container's image is the
  project's own when `DELIVERY_SANDBOX_IMAGE` is empty (the default): the
  daemon builds it from the project's stacks, read from marker files, with
  fixed templates (Python with Poetry and pytest, Node with npm, Go, git;
  `vscode-daemon/stack_image.py`), tags it `fabrique-stack:<sha256 prefix>`,
  and reuses it; the backend asks for it before a delivery's commands and
  waits `STACK_IMAGE_BUILD_TIMEOUT_SECONDS` (`1900`; the daemon's own limit is
  `FABRIQUE_STACK_BUILD_SECONDS`, `1800`). A value in
  `DELIVERY_SANDBOX_IMAGE` runs every project in that one image, as before.
  A company sets its own (`Company.delivery_sandbox`, and `sandbox_env`: the
  variable names a sandboxed run may see) with `GET`/`PUT
  /v1/companies/{id}/sandbox` (Viewer / Manager). The daemon reports what it
  applied (`test_run.sandbox`) and the Verifier records it as the `sandbox`
  check (`services/workspace/sandbox.py`; `host` is a finding, never a refutation);
  `FABRIQUE_SANDBOX_REQUIRED=container` on the daemon's machine refuses the host
  for everything (`vscode-daemon/sandbox.py`), a request that names no sandbox
  included (#525). The daemon also reports it when a company's standards are
  imported (`workspace.git.read_at`, `sandbox_required`): a standards set that
  holds a `command` standard is activated only when this floor or the
  company's own sandbox is `container` or `bwrap` (issue #700,
  `docs/design/COMPANY_STANDARDS.md` §12 decision 4); no new variable. In container mode the report
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
  `DELIVERY_SANDBOX_ENV` (default `false`) adds an **environment volume** in
  container mode (#315): what an install leaves outside the worktree (pip's
  user installation through `PYTHONUSERBASE`, Poetry's and Pipenv's
  virtualenvs, Go's `GOPATH`, module and build caches) kept at
  `/fabrique-env` in `fabrique-env-<key>`, keyed by the project, the company,
  the image and the sha256 of the Python and Go manifests, so it is never
  shared between projects or companies. Only an install writes it; every other
  run mounts it read-only. A failed install removes it, three in a row
  quarantine the project for that company, and the cache's reset above removes
  every environment of the project and company and makes the next delivery
  install again; at most three are kept per project and company. Daemon side,
  `FABRIQUE_SANDBOX_ENV=off` refuses it; its state sits beside the cache's in
  `FABRIQUE_SANDBOX_CACHE_STATE`. An install run as root inside the container
  (a daemon run as root) writes the image's own `site-packages`, which is not
  kept.
- `DELIVERY_BROWSER_IMAGE` — optional, default empty (the application check
  runs in `DELIVERY_SANDBOX_IMAGE`, as before). In container mode, the image the
  application check runs in when its e2e command drives a browser (QA's smoke
  suite, or the e2e command of a profile that serves pages): the published one,
  or one built locally from `vscode-daemon/images/browser` (Node, Playwright
  1.64.1 from its lockfile and the Chrome Headless Shell it launches, each
  download checked by sha256 for amd64 and arm64; `docker build
  vscode-daemon/images/browser`, and behind a TLS-intercepting proxy
  `--secret id=extra_ca,src=<CA bundle>`). Every `v*` tag publishes it for
  `linux/amd64` as `ghcr.io/<owner>/<repo>/browser-sandbox:X.Y.Z` (#529;
  `images.yml`; an arm64 host builds it locally from the same Dockerfile, which
  pins the arm64 browser too, but its browser has not been seen to run on
  arm64 yet), signed by digest with keyless cosign and carrying
  its CycloneDX SBOM as a signed attestation (`docs/OPERATIONS.md`,
  Releasing): set `DELIVERY_BROWSER_IMAGE=ghcr.io/<owner>/<repo>/browser-sandbox@sha256:…`
  with the digest the publish run's summary printed. Pin it by digest
  (`registry/name@sha256:…`); the run records the content ID and digest it
  started from either way. Before a browser e2e command the daemon also
  probes for a browser (`vscode-daemon/browser_probe.py`) and `e2e_run` says
  whether one was present.
- `DELIVERY_BROWSER_IMAGES` — optional, default empty (one browser image,
  `DELIVERY_BROWSER_IMAGE`, for every project, as before). In container mode,
  the browser sandbox image of each Playwright release, as
  `1.63.0=ghcr.io/<owner>/<repo>/browser-sandbox@sha256:…,1.64.1=ghcr.io/<owner>/<repo>/browser-sandbox@sha256:…`
  (commas or spaces between entries). `images.yml` builds one image per
  release of `vscode-daemon/images/browser/playwright_versions.json` (today
  1.63.0 and 1.64.1, the default), tagged `X.Y.Z-playwright-<release>`, and
  its summary prints each entry to paste. Every image must be named by its
  digest (`…@sha256:<64 hex>`, or a local content ID `sha256:…`): an entry
  with a tag, an inexact version or a release listed twice is refused, kept
  on the run's evidence (`app_run.browser_image.refused`) and never run.
  Before a browser e2e command the backend asks the daemon for a fresh
  snapshot of the project (`workspace.inspect`), in which the daemon reports
  the Playwright the project pins, read from its `package.json` and lockfile
  (`vscode-daemon/playwright_pin.py`; npm, pnpm, Yarn, Bun), never from what
  the producer reported. A listed release runs in its image (`match:
  exact`); a release not listed runs in `DELIVERY_BROWSER_IMAGE` (or
  `DELIVERY_SANDBOX_IMAGE` when that is empty) with the mismatch recorded
  (`match: fallback`); a project that declares no Playwright runs in
  `DELIVERY_BROWSER_IMAGE` (`not_declared`); a version nobody could tell (a
  range no lockfile resolves, a lockfile with several versions, a daemon
  from before this setting) starts the application but not the browser
  suite, and `e2e_run` is unverifiable with the reason (`unknown`), never a
  pass. The choice is kept as `app_run.browser_image`.
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
  **Per folder** (`services/workspace/stack_roots.py`, self-hosting
  retrospective F5): a repository whose root has neither a stack nor a named
  test command is searched for sub-folders with their own manifest, two
  deep; the company's profile may name folders (`"folders": {"backend":
  {"stack": "python", "install": "poetry install", "test": "poetry run
  pytest"}}`, whatever the root holds) and commands every delivery runs
  (`"every_delivery": ["python3 scripts/check_architecture.py", {"command":
  "make lint", "folder": "backend"}]`, at most 10); the deployment's
  `DELIVERY_*` settings stay the root's. A delivery runs the suites of the
  folders it wrote in (the daemon's write report), every folder's for a file
  outside them when the root has no suite, each as `cd <folder> && <command>`
  in the same sandbox (an allow-list command policy must list `cd`), then
  the every-delivery commands; each run is the Verifier's `required_commands`
  check. A stored profile without `folders` is the root's alone. No setting
  is added.
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
- `MCP_STDIO_ALLOWED_COMMANDS` — optional, default empty, which **disables
  stdio MCP servers**. Comma-separated absolute, normalized paths of the
  commands a company's stdio server may spawn (`/opt/mcp/bin/github-server`);
  an entry that is not one is ignored. A server's `command` must equal an
  entry exactly (no bare name, relative path, `..`, other case or symlink
  under another name); it is spawned without a shell, as the backend's user.
  Checked when a server is saved or enabled and again before every start, so
  removing an entry stops the servers that used it (they list with
  `start_refused`). Set it in the environment only: no API changes it.
  Allowing an interpreter or a package runner (`npx`, `uvx`, `python`) allows
  any code (SECURITY.md §8.7; how to enable one: `docs/OPERATIONS.md`, MCP
  servers).
- `MCP_HTTP_ALLOWED_HOSTS` — optional, default empty. Comma-separated host
  names whose MCP server may be reached over plain http; every other URL must
  be https. It does not relax the address check: an HTTP or SSE server's host
  must resolve only to public addresses, checked when it is saved and before
  each connection, which goes to the checked address, without an environment
  proxy (`services/tools/mcp_policy.py`, `services/platform/egress.py`). The
  backend logs both settings at startup, and warns when a stdio entry is an
  interpreter, a package runner or a shell.
- `DB_AUTO_UPGRADE` — optional, default `false`. Runs the Alembic migrations
  to head when the backend starts, for a local database run by hand. The
  container's entrypoint and the e2e run `alembic upgrade head` themselves,
  and the backend refuses to start on a database behind head
  (`app/migrations.py`, `CONTRIBUTING.md` §5).
- `RETENTION_OUTBOX_DAYS` (7), `RETENTION_AGENT_RUNS_DAYS`,
  `RETENTION_KPI_TURNS_DAYS` (0, keep all) — what
  the janitor deletes (`backend/janitor/retention.py`): delivered outbox
  events; superseded run versions with their verifications (never the latest
  version, nor one a person reviewed, approved, commented on or published);
  KPI turn rows of `audit_log` outside a company's hash chain (a chained row
  is never deleted, and counted as kept). Task events are never deleted:
  each is a link of its task's hash chain. `RETENTION_GLASSROOM_DAYS`, which
  deleted those of closed sprints, was removed (issue #766); the janitor
  warns when it is still set.
  There is no audit retention: `docs/OPERATIONS.md` says how to archive a
  chain with its rows. `BACKUP_DIR`, `BACKUP_KEEP_DAYS` (14) and
  `PG_CONTAINER` steer `scripts/backup.sh`.
- `RETENTION_NOTIFICATIONS_DAYS` (90) — how long the janitor keeps a
  personal notification (issue #683), read or not; a person's notifications
  in a company they left go at the next run whatever their age. Never
  unbounded: `0`, a negative or a malformed value keeps 90, and anything
  over 365 is cut to 365. The email copy of a notification uses the `SMTP_*`
  settings above and is sent only when `SMTP_HOST` is set; without it the
  notification is still shown in the app and nothing waits on mail.
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
- `CODE_GRAPH_STORE` (`postgres`) — where the code graph the daemon reports
  is stored (`services/tools/code_graph_store.py`, issue #438, second
  slice): `postgres` (the `code_graph_*` tables), `neo4j` (the knowledge
  graph, used only while `NEO4J_ENABLED`; otherwise Postgres) or `off`
  (nothing stored; the related code is computed on demand, as before). The
  task worker updates it before a Developer's related code, from the files
  whose sha256 changed, and the section reads it when it holds the task's
  starting files.
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
  whole sections are left out from the least needed (memory and the
  sprint's completed tasks, then lessons, the repository, the
  requirements, standards and previous attempt); the format, the task, the
  Architect's brief and review feedback never are, and the prompt names what
  was left out. Unset or `0`: nothing is cut. Either way each section's weight
  is recorded on the run's evidence as `prompt_sections`.
- `PROMPT_SECTIONS_OFF` — optional, empty by default (#443). Comma-separated
  names of the Developer task prompt's sections to leave out of every prompt,
  for a with/without comparison on the bench (`memory,lessons`). The names:
  `standards_and_procedures`, `lessons`, `requirements`, `repository`,
  `related_code`, `wiki`, `memory`, `previous_attempt`, `completed_tasks`,
  `sprint_board` (case does not matter; `console_assistant_notes` went with
  the console assistants, issue #750, and is now an unknown name). They are
  left out before `PROMPT_BUDGET` applies and the prompt does not name them;
  the run's `prompt_sections` records `off` (the names set) and `off_dropped`
  (those the prompt would have carried), so a scorecard can tell the runs
  apart. An unknown name, or a required section's (`output_format`, `task`,
  `architect_brief`, `review_feedback`), is ignored with a warning at startup
  that lists the known names; the startup log also says what is off. Empty:
  nothing is off and the prompt is unchanged.
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
- `EDIT_FORMAT_BY_MODEL` — optional, empty by default (issue #689). The edit
  format each model is asked for, JSON: `{"<model>": "search_replace", "*":
  "whole"}`. The key is the model the task worker requests (after difficulty
  routing); `*` covers every model not named. A format is `auto` (whole files,
  with SEARCH/REPLACE edits allowed for an existing file: the prompt as
  before), `whole` (whole files only) or `search_replace` (SEARCH/REPLACE edits
  for an existing file, whole files for new ones;
  `services/models/edit_format.py`). The run's evidence records the format
  asked for and the one the backend read in the answer (`edit_format`). A
  value that cannot be read is dropped with a warning. The bench sets it per
  run (`BENCH_EDIT_FORMAT`, `scripts/bench/edit_formats.sh`). Unset: every
  model runs on `auto`.
- `AGENT_MODEL` — backend, optional, default empty. One model id for every
  role instead of the DeepSeek defaults (the key of that model's provider is
  then the one needed). Read by
  `ModelFactory.model_for_role` (`services/models/model_factory.py`), the
  single source of role defaults for the task worker, the ScrumMaster, the
  Support and Reviewer agents, the PO's drafts and the LangGraph producer.
  Use ids from the proxy's `GET /v1/models`: the proxy refuses an id its
  provider does not list (`unknown_model`). Not a secret.
- `AGENT_MODEL_<ROLE>` — backend, optional, default empty: one role's model,
  winning over `NEMOTRON_ROLES` and `AGENT_MODEL`. The names the code reads
  are `AGENT_MODEL_PO`, `AGENT_MODEL_ARCHITECT`, `AGENT_MODEL_SCRUM_MASTER`,
  `AGENT_MODEL_DEVELOPER`, `AGENT_MODEL_QA`, `AGENT_MODEL_SECOPS`,
  `AGENT_MODEL_TECH_WRITER`, `AGENT_MODEL_BACKEND_DEVELOPER`,
  `AGENT_MODEL_FRONTEND_DEVELOPER`, `AGENT_MODEL_DEVOPS`,
  `AGENT_MODEL_DESIGNER`, `AGENT_MODEL_DATA_ENGINEER` and
  `AGENT_MODEL_ENGRAM_PROCESSOR`; a coding role without its own (backend,
  frontend, DevOps, data) reads `AGENT_MODEL_DEVELOPER`, and the Reviewer
  agent reads QA's settings (`AGENT_MODEL_QA`, `NEMOTRON_ROLES=qa`) though its
  own default is DeepSeek Pro. `docker-compose.yml`
  passes only `AGENT_MODEL` and the first seven plus
  `AGENT_MODEL_ENGRAM_PROCESSOR` from `.env` to the backend; the others reach
  it only by adding them to the backend's `environment` in compose. The order,
  from most to least specific: an agent's own `model` (the Agents page), the
  company's model for the role, then its default model
  (`services/models/company_models.py`), the difficulty policy
  (`AGENT_MODEL_POLICY`, above), `AGENT_MODEL_<ROLE>`, `NEMOTRON_ROLES`,
  `AGENT_MODEL`, the `ROLE_MODEL_MAP` default.
- `NEMOTRON_ROLES` — backend, optional, default empty. Comma-separated role
  names (`PO`, `Architect`, `ScrumMaster`, `Developer`, `QA`, `SecOps`,
  `TechWriter`, `Designer`…, case-insensitive; `Developer` also covers the
  coding roles) that run on NVIDIA Nemotron through Nebius, winning over
  `AGENT_MODEL`. Such a role runs `NEMOTRON_MODEL_STRATEGIC` (PO, Architect,
  SecOps, Designer) or `NEMOTRON_MODEL_FAST` (TechWriter) when set, else
  `NEMOTRON_MODEL`, else its `ROLE_MODEL_MAP` default; all three are empty by
  default. Read the real ids from `GET /v1/models` (`provider == "nebius"`)
  first; needs `NEBIUS_API_KEY` on the proxy. Not secrets.
- `ARCHITECT_EDITOR_MODE` — backend, default `false` (compose:
  `${ARCHITECT_EDITOR_MODE:-false}`). Architect and editor mode
  (`services/loop/edit_plan.py`, from Aider): on, a Developer task whose
  difficulty is at least `ARCHITECT_EDITOR_MIN_DIFFICULTY` (default `4`, on
  the 1-5 scale of `services/models/difficulty.py`) gets a numbered edit plan
  from the Architect and runs on the policy's cheapest band model. Off, the
  Architect's brief stays free text. Needs `ARCHITECT_CONSULT_ENABLED`.
- `ARCHITECT_CONSULT_ENABLED` — backend, default `true`. The task worker asks
  the Architect before a Developer task that is high priority, touches a
  design-sensitive topic or comes back from QA, once per attempt
  (`services/platform/orchestration_metrics.py`), recorded as an
  `ARCHITECT_CONSULTED:` comment; `false` turns that off, and the editor mode's
  plan with it.
- `API_DOCS_ENABLED` — default `false`. The interactive documentation
  (`/docs`, `/redoc`, `/openapi.json`); off, all three answer 404
  (WSTG-v42-CONF-05). `docker-compose.yml` sets it to `true` for local
  development (`${API_DOCS_ENABLED:-true}`); set `API_DOCS_ENABLED=false` in
  `.env` where the compose stack faces users, and turn it on only where
  publishing the schema is meant (a DAST scan reads `/openapi.json`).
- `SECURITY_HEADERS_ENABLED` — default `true`. The header baseline of
  SECURITY.md §3.3 on every answer (`backend/app/security.py`); `false` only
  behind a proxy that sets the same headers.
- `ERROR_REPORT_URL` — optional, empty by default. An https endpoint the
  operator controls that receives one POST per unhandled error: the error id,
  the exception's class, the HTTP method, the route template and
  `ERROR_REPORT_SOURCE` (optional, a name for this deployment); never a
  traceback, a message, a path or a request body. The answer to the client
  carries only the error id, and the traceback stays in the backend's log
  under it. (Replaces the former `FABRIQUE_VENDOR_SUPPORT_URL`, which sent the
  traceback and is no longer read.)
- `WEBHOOK_ALLOW_PRIVATE_HOSTS` — optional, default `false`. A company's
  notification webhook (`services/integrations/webhooks.py`) must be an https URL whose host
  resolves to public addresses, checked when it is saved and before every post,
  and the post goes to the address that was checked (`services/platform/egress.py`);
  `true` allows a private, loopback or link-local host (a receiver on the LAN).
  The same check applies to a company's RFC 3161 timestamping authority
  (`tsa_url` of the audit anchors), which may also be http.
- `RESEARCH_TOOLS_ENABLED` (true), `RESEARCH_ROLES` (Architect, PO, SecOps,
  TechWriter, Designer), `RESEARCH_ALLOWED_HOSTS` (empty), `RESEARCH_TOOL_ROUNDS` (6) —
  the research tools (`services/tools/research_tools.py`): the roles that decide
  read official documentation, the package registries and OSV through the
  backend, from the default hosts plus these and the company's own; every
  source is evidence. Only a host named in `RESEARCH_ALLOWED_HOSTS` may
  resolve to a private or loopback address (a local mirror); a default host or
  one on a company's list is fetched only on a public address, resolved once
  and pinned (issue #541). `RESEARCH_PYPI_URL`, `RESEARCH_NPM_URL`,
  `RESEARCH_CRATES_URL`, `RESEARCH_GO_URL` and `RESEARCH_OSV_URL` may point
  at a mirror (the e2e points PyPI at its fake). `RESEARCH_TIMEOUT_SECONDS`
  (20) bounds one fetch and `RESEARCH_FETCH_MAX_BYTES` (524288, 512 KiB) cuts
  a page or registry answer there (the result says `cut_at_bytes`).
- `GROUNDING_LOOKUPS_ENABLED` (true), `GROUNDING_MAX_LOOKUPS` (20) — the
  Verifier's `grounded` check (`services/evidence/grounding.py`): when a
  delivery writes a manifest, the backend asks each registry for its
  dependencies through `services/tools/registry_lookup.py` and records the
  answers on the evidence as `registry_lookups`; verification then reads them
  and never reaches the network. Off, or over the bound, a dependency without
  an answer is a finding, never a refutation.
- `AGENT_GRAPH_ENABLED` — backend, `config.py` default `false`, but
  `docker-compose.yml` sets `${AGENT_GRAPH_ENABLED:-true}`, so the compose
  stack has it on unless `.env` says `false` (`.env.example` does). The
  internal LangGraph producer: off, `POST /v1/generate` answers 409 (ingest
  external work through `POST /v1/agent_runs`) and a story on
  `workspace.generate` is ignored with a Glassroom note
  (`services/producers/agent_worker.py`); `/v1/capabilities` reports it as
  `agent_graph`. The sprint's task worker does not depend on it.
- `AUDIT_LOG_PATH` — backend, default `/app/.factory/logs` (compose sets the
  same, on the `factory-logs` volume). The folder of the append-only
  `audit.jsonl` (`services/platform/agent_service.py`), which the support desk
  downloads (`routers/support.py`). The audit hash chain lives in Postgres,
  not here.
- `GITHUB_TOKEN` — backend, optional secret, default unset; set it in `.env`
  (compose passes `${GITHUB_TOKEN:-}`). Sent as the bearer token of the GitHub
  API calls of `services/producers/github_adapter.py` (the backfill's reads of
  pull requests, their commits and diffs) when the call carries no token of
  its own, for rate-limit headroom; `/v1/capabilities` reports `github: true`
  with it. Compose does not give it to the daemon.
- `OPENPROJECT_URL` (default `http://localhost:8080`), `OPENPROJECT_TOKEN_PO`
  (optional secret, default unset) — backend
  (`services/producers/openproject_tools.py`): with the PO's token, the PO's
  backlog tool creates work packages there; without it the ticket is
  simulated locally and the chat says so. `OPENPROJECT_TOKEN_DEV` is declared
  in `config.py` but no code reads it today. Set the tokens in `.env` (or the
  secret store); compose does not pass them.
- `RATE_LIMIT_DEFAULT` — backend, default `100/minute`. The slowapi limit on
  every route without one of its own, per connecting address
  (`app/security.py`, `RATE_LIMIT_STORAGE_URI`).
- `WORKSPACE_WRITE_TIMEOUT_SECONDS` — backend, default `30`. How long the task
  worker waits for the daemon to confirm a delivery's writes (more when the
  Git workflow may fetch the base, `services/workspace/git_workflow.py`); a
  console producer's preparation and collection steps on the daemon get twice
  that.
- `CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES` — backend, default `10` (at least 1)
  (`services/producers/cli_cooldowns.py`): how long a company's console
  producer skips the Claude CLI after it reported a quota without a reset
  time.
- **Removed (issue #750), ignored if still set:** `CONSOLE_ASSISTANTS_ENABLED`,
  `CONSOLE_ASSISTANT_TIMEOUT_SECONDS`, `CONSOLE_ASSISTANT_STRATEGY`,
  `CONSOLE_ASSISTANT_ALLOWED_ROLES`, `COPILOT_CLI_COMMAND_TEMPLATE`,
  `CLAUDE_CLI_COMMAND_TEMPLATE`, `CLAUDE_CLI_ALLOWED_TOOLS`,
  `CLAUDE_CLI_APPEND_SYSTEM_PROMPT` and `CLAUDE_CLI_HIGH_VALUE_ROLES`. They
  configured the console assistants, which asked a terminal CLI on the
  daemon's machine (Copilot CLI, Claude CLI) for advice on a task, under the
  person's own account; the second opinion is now the Architect's
  consultation on the team's own model (`ARCHITECT_CONSULT_ENABLED`). They
  stay declared in `config.py` (`REMOVED_SETTINGS`) so a `.env` that still
  sets one loads, and the backend logs once at startup that each one set no
  longer does anything; remove them from `.env`.

## The LLM proxy

Read by `llm-proxy/` (the provider keys are in the list at the top), and by
the backend where it says so:

- `LLM_PROXY_URL` — backend, default `http://localhost:5100` (`config.py`,
  `app/llm_proxy_client.py`, `services/models/model_factory.py`); compose and
  `.env.example` set `http://llm-proxy:5100`. Where every model call goes.
- `LLM_UPSTREAM_TIMEOUT_SECONDS` — default `300`, read by the proxy
  (`llm-proxy/timeouts.py`, `llm-proxy/gunicorn.conf.py`) and by the backend
  (`app/llm_proxy_client.py`); compose passes it to both. The time budget of
  one chat request's provider calls, fallbacks and the retry without thinking
  included. The proxy stops reading a provider's answer at the budget even
  while DeepSeek keeps the connection alive with empty lines, and answers
  `502` with `reason: upstream_timeout`. gunicorn's worker timeout is this
  plus 30 s and the backend waits this plus 15 s, so the proxy always answers
  first; a backend wait that still runs out is `reason: proxy_read_timeout`,
  and a connection the proxy dropped `proxy_connection_lost`, neither sent
  again (the proxy may be billing it). 300 s because a DeepSeek call that
  reasons first (thinking mode) can take minutes; it was 110.
- `LLM_PROXY_WORKERS` / `LLM_PROXY_THREADS` — defaults `2` and `8`, read by
  `llm-proxy/gunicorn.conf.py`. The proxy runs gunicorn's `gthread` worker
  class: one long call holds one thread, not a worker, and `/health` still
  answers while two thinking calls run.
- `LLM_THINKING` — default `auto`, read by the backend
  (`services/models/thinking.py`) and by the proxy (`llm-proxy/thinking.py`).
  DeepSeek's thinking mode, sent on every DeepSeek call as
  `thinking: {"type": "enabled" | "disabled"}`
  (https://api-docs.deepseek.com/guides/thinking_mode); without it DeepSeek
  thinks by default. `auto` follows the policy in
  [`contexts/models.md`](contexts/models.md) (off for calls whose answer is
  parsed, on for the reasoning roles' free-form calls); `on` or `off` forces
  every call. With `on`, a structured call whose reasoning used the whole
  budget is still retried once with thinking off.
- `LLM_THINKING_RESERVE_TOKENS` — backend, default `16384`
  (`services/models/thinking.py`). Added to a thinking call's `max_tokens`
  for the reasoning, which DeepSeek counts against it, so a role's answer
  budget stays its own.
- `LLM_FALLBACK_PROVIDERS` — default empty, read by the proxy
  (`llm-proxy/routing.py`, issue #751). After a 429, a 5xx or an empty answer
  the proxy first tries the same provider's other model (DeepSeek Pro and
  Flash stand in for each other); then, in order, one catalogue model of each
  provider this comma-separated list names (`deepseek`, `qwen`, `gemini`; an
  unknown name is logged and ignored). Empty, a request never leaves the
  provider it was sent to: configuring a key for one role does not make that
  provider a processor of every other role's prompts (GDPR arts. 25 and 28).
  `/health`'s `capabilities.failover` says which applies.
- `EMBEDDING_PROVIDER` — default `local`, read by the proxy
  (`llm-proxy/embedding.py`; issues #751, #755).
  - `local`: the local semantic model, `intfloat/multilingual-e5-small`
    (`llm-proxy/local_model.py`), run with ONNX Runtime on CPU in the proxy:
    no key, no outside call, nothing leaves the proxy. 384 values per text,
    padded with zeros to the 1536 of `vector_memory.embedding` (zeros change
    neither the norm nor the cosine distance); text past 512 tokens is cut.
    When the model cannot load (a file missing or not matching its pinned
    sha256, the runtime missing, or its startup self-check failing), the hash
    vector stands in and `capabilities.embeddings` is `degraded` with the
    reason.
  - `local-hash`: the deterministic hash vector of #751, on purpose (a
    machine short of memory, say). It finds near-identical text, not meaning,
    and is reported as `degraded`.
  - `gemini`: Gemini's `text-embedding-004` when `GEMINI_API_KEY` is set
    (padded with zeros to 1536); the local model (or the hash vector) when the
    key is missing or the call fails. Google becomes a processor of the
    company's memory (GDPR art. 28), which is why it is never the default.
  - Any other value is `local`, reported as `degraded` with the reason.

  Every answer names its `model`; the backend stores it in
  `vector_memory.embedding_model` (migration 0057), compares only vectors of
  the same model, and at each start re-embeds rows another source wrote once
  the semantic model answers (never towards the hash vector). `/health`'s
  `capabilities.embeddings` says which applies and why, and `/health/deep`'s
  `memory` is `on` only when the embedding is semantic.

  Why this model (issue #755, read 2026-11-20). MIT licence; 118 M
  parameters; the int8 ONNX export the model's own repository publishes is
  118 MB (`onnx/model_qint8_avx512_vnni.onnx`, plus a 17 MB tokenizer). On
  MMTEB's MTEB(Multilingual) (Enevoldsen et al., 2025, arXiv:2502.13595,
  table 2) it scores 55.5 mean over tasks, against 48.8 for
  `paraphrase-multilingual-MiniLM-L12-v2` (Apache-2.0, the same size) and
  52.0 for `paraphrase-multilingual-mpnet-base-v2` (twice the size); on
  MTEB(Europe) 55.0 against 51.7 for the MiniLM. Larger multilingual models
  (`multilingual-e5-base` 57.0, `bge-m3`) cost two to five times the memory
  and latency for a few points. ONNX Runtime (MIT) with `tokenizers`
  (Apache-2.0) and NumPy (BSD-3-Clause) runs it without a deep-learning
  framework: on a 4-core x86 CPU it loads in 0.9 s (hashes and self-check
  included) and embeds a memory in 11 to 12 ms (70 ms at 512 tokens).
- `EMBEDDING_MODEL_DIR` — default `models/multilingual-e5-small` next to
  `llm-proxy/local_model.py`; the image sets `/app/models/multilingual-e5-small`.
  Where the proxy reads `model.onnx` and `tokenizer.json`. The image fetches
  them at build time from the pinned revision in `llm-proxy/embedding_model.json`
  and checks each against its sha256 (`llm-proxy/fetch_embedding_model.py`; a
  mismatch fails the build); the proxy checks them again before loading and
  never downloads anything. Outside the image, run
  `python llm-proxy/fetch_embedding_model.py` once.
- `EMBEDDING_THREADS` — default `2`, read by the proxy: ONNX Runtime's threads
  per proxy worker for one embedding.
- `PROXY_PORT` — default `5100`, read only when the proxy runs as
  `python app.py`. The image runs gunicorn bound to `0.0.0.0:5100`
  (`llm-proxy/gunicorn.conf.py`) and ignores it, so the value compose sets
  changes nothing; change the port there and in the `ports:` mapping instead.
- `LLM_PROXY_INTERNAL_TOKEN` — in the list at the top.
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `OTEL_EXPORTER_OTLP_ENDPOINT` —
  default unset; compose sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`.
  The OTLP/HTTP collector the proxy exports its spans to (`/v1/traces` is
  appended; `llm-proxy/telemetry.py`). `OTEL_EXPORTER_OTLP_ENABLED` truthy with
  no endpoint exports to `http://localhost:4318`; `OTEL_SDK_DISABLED` truthy
  turns export off whatever else is set. Unset, nothing is exported, and the
  `traceparent` header is still propagated. The backend reads the same
  variables the same way (`backend/telemetry.py`, loaded at startup), so the
  compose value sends its spans to Jaeger too; the daemon has no telemetry
  code and ignores it.

## Set by docker-compose.yml for third-party containers

Fixed in `docker-compose.yml`, not read by La Fabrique's code, and not
meant to be set in `.env`:

- `COLLECTOR_OTLP_ENABLED: "true"` — the Jaeger container's OTLP receiver
  (port 4318), which the proxy exports to; the Jaeger UI is on 16686.
- `NEO4J_ACCEPT_LICENSE_AGREEMENT: "eval"` and `NEO4J_PLUGINS: '["apoc"]'` —
  the Neo4j container's licence acceptance and the APOC plugin it loads.
- `HOME` — the host's home folder, the default of `PROJECTS_ROOT` for the
  optional daemon container.

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

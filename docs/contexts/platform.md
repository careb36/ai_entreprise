# The platform context

The infrastructure every context may use: audit writes, locks, Glassroom,
tokens, health, the knowledge graph reader, and the people side (sign-in,
teams, segregation of duties, company settings). Read this before changing
`backend/app/services/platform/`; each module's docstring has the detail. It
knows nothing of tasks, runs or a company's decisions.

- **Folder:** `backend/app/services/platform/`
- **May import:** [contracts](contracts.md). Every context but contracts may import it.
- **Interface:** `acknowledgements`, `agent_service`, `audit_anchors`, `company_limits_setting`, `company_standards_setting`, `egress`, `engram_processor`, `forge_policy_setting`, `glassroom`, `hooks`, `in_process_principal`, `locks`, `mail`, `memory_graph`, `notifications`, `orchestration_metrics`, `protected_paths_setting`, `rework_mentions`, `rfc3161`, `run_window_setting`, `sod`, `standard_exceptions`, `teams`, `wstg_catalogue`.
- **Modules:** `access_review`, `acknowledgements`, `agent_service`, `api_tokens`, `audit_anchors`, `capabilities`, `company_limits_setting`, `company_standards_review`, `company_standards_setting`, `dns_client`, `egress`, `engram_processor`, `forge_policy_setting`, `glassroom`, `healthcheck`, `hooks`, `idempotency`, `in_process_principal`, `invitations`, `locks`, `login_throttle`, `mail`, `memory_graph`, `notifications`, `oidc`, `oidc_client`, `orchestration_metrics`, `password_change`, `protected_paths_setting`, `rework_mentions`, `rfc3161`, `run_secrets`, `run_window_setting`, `service_accounts`, `sessions`, `sod`, `sso_connections`, `sso_domains`, `sso_flow`, `sso_group_approvals`, `sso_group_config`, `sso_group_review`, `sso_group_sync`, `sso_groups`, `sso_link`, `sso_metrics`, `sso_team_control`, `standard_exceptions`, `teams`, `token_usage`, `wstg_catalogue`
- **HTTP adapters:** `healthcheck` (the LLM proxy's probe), `oidc_client`. `dns_client` is the DNS resolver adapter, held to the same shape (returns data, raises `DnsNameNotFound` or `DnsUnavailable`).

## How it works

Routers in `backend/app/routers/` resolve the caller, check the company role
and call these modules. Every write that matters is an audit row
(`agent_service.py`), and a company setting changes only through an audited
setter that records both values and who acted. Work that several backend
replicas could run is guarded by advisory locks (`locks.py`). The platform
also owns the pieces outside `services/` listed under Infrastructure.

## Rules

- All data is scoped by `company_id`. Company ownership is resolved from the
  caller's membership and existing records, never from payload metadata.
- Roles are Owner > Manager > Viewer. Access is checked against the
  **effective role**: the higher of the membership role and the roles of the
  person's teams in that company. A team never makes anyone a member and
  never reaches another company.
- Who may call each route is data, `docs/architecture/authorization.json`;
  `tests/test_authorization_matrix.py` fails on an undeclared route.
- Owner-only settings (SSO, teams, SoD policy, access review, protected
  paths, forge policy, run window, limits, standards) need a signed-in human
  Owner. An API token is refused whatever its role. An API token acts for its
  creator and is refused once it exceeds the creator's current effective role
  or the creator has left.
- Secrets (SSO client secret, run secrets, credentials) are Fernet-encrypted
  and never returned. Refresh tokens, CSRF values, invitation tokens and SSO
  states are stored only as hashes.
- A hash is computed here over the bytes received, never taken from a file,
  request or daemon (standards sets, audit anchors).
- An outgoing request to a URL a company chose goes through `egress.py`:
  resolved once, refused unless every address is public, connected to by
  that address so DNS rebinding cannot steer it.
- Memory (pgvector, Neo4j) is prompt context, never evidence. A reviewer's
  rework text is a claim; metrics built from it say what reviewers wrote.
- A company setting is never inferred from the project (protected paths in
  particular). Closed history rows (team periods, access reviews, audit
  chain) are never edited or deleted.

## Components

### Infrastructure

- `backend/app/main.py`: the FastAPI app, its lifespan, middleware and routers.
- `backend/app/migrations.py`, `backend/alembic/`: Alembic owns the schema.
  Startup refuses a database behind head unless `DB_AUTO_UPGRADE=true`. How
  to write a migration: `CONTRIBUTING.md` §5.
- `backend/app/nats_client.py`: JetStream work-queue streams
  (`task.assign`, `workspace.deliver`, `workspace.delivered`) with durable
  pull consumers; core NATS with a warning when JetStream is missing.
  Credentials and TLS come from `app/nats_auth.py`.
- `backend/janitor/main.py`: the daily maintenance container (retention,
  pgvector reindex). `scripts/backup.sh` backs up and restores Postgres.
- `idempotency.py`: one claim per NATS event, so a redelivery is skipped.
- `healthcheck.py`, `capabilities.py`: `/health` and `/health/deep`, with
  each capability `on`, `off` or `degraded` and a reason, never a setting's
  value. `verification` is `degraded` while delivery tests are off or the
  default sandbox is `host`, whatever a company acknowledged.

### Locks and replicas

`locks.py` holds a Postgres session-level advisory lock that never waits: a
replica that does not get it skips, and a dead replica's lock goes with its
connection. `replicas.py` is a module of [loop](loop.md) that uses it: a task
is claimed by a conditional `UPDATE … WHERE status = 'todo'`, the task lock
is the worker's lease, and only unheld `in_progress` tasks are recovered. A
sprint closes once.

### Memory architecture

- **pgvector** (`vector_memory`): semantic memory for retrieval. Without the
  table the memory routes answer 503. A search compares only rows embedded
  by the query's model.
- **Neo4j** (optional, `NEO4J_ENABLED`): immutable Engram nodes for file
  lineage. Off, nothing is written and `memory_graph.py` answers "not
  recorded". `engram_processor.py` writes the engrams (step 10 of
  [the sprint loop](../SPRINT_LOOP.md)).

### Auth and multi-tenancy

- **Passwords and sessions** (`backend/app/passwords.py`, `sessions.py`,
  `login_throttle.py`): bcrypt, at least `PASSWORD_MIN_LENGTH` at
  registration. Login starts a refresh-token family and sets HttpOnly
  cookies; each refresh token rotates exactly once, and reuse revokes the
  family. A cookie-authenticated unsafe request needs `X-Fabrique-CSRF`.
  Login is rate-limited per address and per account. Design:
  [`SESSIONS.md`](../archive/design/SESSIONS.md).
- **Changing a password** (`password_change.py`): `POST /v1/auth/password`
  for the signed-in person (an API token or an owner action gets 403). The
  current password is checked under the sign-in's backoff, the new one by
  `check_new_password` and must differ. Every session family of the person
  is revoked and a browser session gets a new one; the change, the
  revocations and the audit rows are one commit. A forgotten password is
  reset on the server (`backend/app/reset_password.py`, audited as
  `password_reset_by_operator`). One audit row per company of the person,
  never a password or hash. [Design](../design/change-password.md).
- **Step-up** (`backend/app/step_up.py`): a fresh re-authentication in the
  request that starts a dangerous owner action (a protected `land_branch`):
  the password, checked with bcrypt and the same per-account backoff as the
  sign-in, or for an SSO-only account a session whose SSO sign-in is under
  five minutes old. The password is never stored or audited; the start's
  audit row names the proof (`password`, `sso_recent`).
  [Design](../design/owner-actions.md), threat L1.
- **Email addresses** are compared by one rule, `contracts/emails`.
  Registration answers alike for a new and a taken address.
- **Invitations** (`invitations.py`): an invite adds nobody and answers the
  same whether the address exists. Only acceptance creates a membership; an
  invitation is used once. A Manager invites up to Manager; only an Owner
  invites an Owner.
- **Single sign-on** (`sso_connections.py`, `sso_flow.py`, `oidc.py`,
  `oidc_client.py`, `sso_link.py`): one OIDC connection per company. The ID
  token is validated here (signature against JWKS, `iss`, `aud`, expiry,
  nonce). An IdP is trusted only within its company: an existing account is
  linked by email only when it belongs to this company and no other.
  Requiring SSO leaves break-glass Owners (`SSO_BREAK_GLASS_EMAILS`). Design:
  [`SSO.md`](../archive/design/SSO.md).
- **Domain verification** (`sso_domains.py`, `dns_client.py`,
  `sso_metrics.py`): only a domain proved by a DNS TXT record lets SSO
  create or link an account. A transient DNS failure changes nothing.
  Re-check counts go to `/metrics`, never by company, domain or person.
- **Group mapping** (`sso_groups.py`, `sso_group_sync.py`,
  `sso_group_config.py`, `sso_group_approvals.py`, `sso_team_control.py`,
  `sso_group_review.py`): IdP groups mapped to SSO-managed teams, capped at
  Manager. Only a present empty claim removes; an absent, overage or
  malformed claim changes nothing. A Manager mapping needs a second Owner's approval
  (or the only Owner's reasoned confirmation).
  Design: [`SSO_GROUP_MAPPING.md`](../archive/design/SSO_GROUP_MAPPING.md).
- **Teams** (`teams.py`): `effective_role` is pure. Every membership change
  opens or closes a history period. Design:
  [`TEAMS_AND_USAGE.md`](../archive/design/TEAMS_AND_USAGE.md).
- **API tokens** (`api_tokens.py`): checked against the creator on every use;
  refusals audited `api_token_refused`. Prefer them to Bearer logins for
  automation.
- **In-process principal** (`in_process_principal.py`, issue #701): an owner
  action ([management](management.md#owner-actions)) calls the API in-process
  as the Owner who started it, with no token: the ASGI scope carries an
  `InProcessPrincipal`, which `auth_deps` honours only as an instance of that
  class and only while its run is live. A header cannot forge it; every route
  still checks the person's current roles. Design:
  [`owner-actions.md`](../design/owner-actions.md) (boundary B15).
- **Public routes**: `/health`, the auth and SSO flow endpoints, the signed
  GitHub and chat webhooks, support reporting, `/v1/models`,
  `/v1/capabilities`. Glassroom SSE takes a five-minute ticket for one
  sprint and re-checks membership.
- **Proxies**: `TRUSTED_PROXY_IPS` names the proxies whose
  `X-Forwarded-For` is read; empty trusts none.

### Segregation of duties and access review

- `sod.py`: `decide` is pure. An approval by the account that asked for or
  wrote the work is refused (a committed `sod_refused` row and a 403). The
  single-person exception is an Owner's audited grant, honoured only while
  exactly one member can approve. Design:
  [`SEGREGATION_OF_DUTIES.md`](../archive/design/SEGREGATION_OF_DUTIES.md).
- `access_review.py`: every member's own, team and effective role and every
  working API token, confirmed or removed; removals apply at once.
- `service_accounts.py`: the company's machine credentials with owner,
  scope and age, never a value.

### Company settings

Each is a column read and changed by one module, audited only on a change.

- `protected_paths_setting.py`: whether a company hosts the factory. The
  Manager agent never changes it (`ALWAYS_ESCALATE`). What it does is
  [evidence](evidence.md)'s.
- `forge_policy_setting.py`: `none`, `fetch` or `publish`, capped by
  `FORGE_POLICY_MAX`; a refused publication is a 409 `forge_disabled`.
- `run_window_setting.py`: when work that can wait may start, with holiday
  calendars from `holiday_calendars/<name>/<year>.json` (each cites its
  source). The gate is [loop](loop.md#the-run-window).
- `company_limits_setting.py`: the Developer's limits within the floors and
  ceilings of `contracts/company_limits`.
- `acknowledgements.py`: an Owner's time-bound acknowledgement of a weaker
  guarantee (tests off, host sandbox); withdrawing keeps the row.

### Company standards

`company_standards_setting.py`, `standard_exceptions.py`,
`company_standards_review.py`: imported versions of `.fabrique/standards.toml`
with the sha256 computed here. Activation is four-eyes whatever the SoD mode;
the only Owner may activate alone, flagged in the access review until a
second Owner confirms. A draft with a refusal is refused, and so is a
`command` standard unless commands run isolated. An exception is the only
way around a standard: reasoned, compensated, 1 to 90 days, approved by a
second Owner. Design: [`COMPANY_STANDARDS.md`](../archive/design/COMPANY_STANDARDS.md).

### Personal notifications

`notifications.py`, `mail.py`: one row per receiver who is still a member,
written in the producer's transaction (a rollback notifies no one). A row
never copies a title, reason, domain or address; the link is read again
through routes the receiver may call. A security notice is always shown in
the app and snoozed at most 24 hours. Email is a copy, queued only while
`SMTP_HOST` is set. Producers: [integrations](integrations.md#personal-notifications).

### Audit anchors

`audit_anchors.py`, `rfc3161.py`: the chain's head recorded outside the
factory (webhook, RFC 3161 timestamp, customer Ed25519 signature).
`coverage` re-checks every anchor against the chain as it is now; a webhook
anchor stays a claim. Sending them: [integrations](integrations.md#audit-anchors).
Design: [`AUDIT_ANCHORS.md`](../archive/design/AUDIT_ANCHORS.md).

### Metrics and the rest

- `agent_service.py`: publishes `workspace.generate` and writes audit rows.
- `orchestration_metrics.py`: agent turns, and the gate's refutations by
  problem; never mixed with QA rework.
- `rework_mentions.py`: rework requests naming a missing module, by source.
- `token_usage.py`: cost, see [models](models.md).
- `run_secrets.py`: values that live for one task run, encrypted.
- `glassroom.py`, `hooks.py` (`sprint.closed`), `wstg_catalogue.py` (the
  OWASP WSTG tests for the security plan in [planning](planning.md)).

## See also

- Every setting: [configuration](../CONFIGURATION.md).
- Deploy, proxies, backups, replicas: [operations](../OPERATIONS.md).
- The usage report over teams: [models](models.md#who-used-what-the-usage-report).

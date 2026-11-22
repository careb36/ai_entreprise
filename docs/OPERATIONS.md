# Operations runbook

How to run La Fabrique from this repository, commands first. Every variable
is in [`CONFIGURATION.md`](CONFIGURATION.md).

## Deploy

1. Copy `.env.example` to `.env`. Set at least `POSTGRES_PASSWORD`,
   `JWT_SECRET_KEY`, `FERNET_SECRET_KEY`, `AUDIT_SIGNING_KEY` and
   `DEEPSEEK_API_KEY` (the default team runs on DeepSeek alone;
   `QWEN_API_KEY` is optional). DeepSeek processes requests in China: read
   `DEEPSEEK_API_KEY` in `CONFIGURATION.md` before personal or client data
   goes through it.
2. `docker compose up -d` starts Postgres, NATS, the LLM proxy, the backend,
   the frontend, the janitor and [Neo4j](#neo4j).
3. Start the workspace daemon on the computer that holds the projects:
   `./scripts/start-daemon.sh` (`.ps1` on Windows). On a server instead
   (`docker compose --profile docker-daemon up`), also set
   `DELIVERY_SANDBOX=container` and `FABRIQUE_SANDBOX_REQUIRED=container`.
   Keep `NATS_BIND=127.0.0.1` unless the daemon is on another machine
   ([Securing the bus](#securing-the-bus)).
4. Check `GET /health` and the model keys (below). Register the first user
   and create a company.

Also:

- Serve the frontend and API over https on one site (sessions use
  `Secure; SameSite=Strict` cookies). List the frontend's origins in
  `CORS_ORIGINS`, never `*`.
- The backend container runs `alembic upgrade head` on start. By hand, run
  it yourself or set `DB_AUTO_UPGRADE=true`.
- Set `API_DOCS_ENABLED=false` on a stack that faces users.

### Check the model keys

```bash
curl -s localhost:5100/v1/providers | jq              # status per provider
curl -s 'localhost:5100/v1/providers?refresh=1' | jq  # check again after a top-up
curl -s localhost:5100/v1/models | jq -r '.models[] | "\(.provider) \(.id)"'
curl -s localhost:5100/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model": "<a role model id>", "max_tokens": 1,
       "messages": [{"role": "user", "content": "ping"}]}' \
  | jq '{requested_model, served_model, provider, fallback_from, error, status, message}'
```

- A key works when `served_model` equals `requested_model` and there is no
  `fallback_from`. Try each model the team uses (`deepseek-v4-pro`,
  `deepseek-flash`).
- `/v1/providers` status: `ok`, `no_credit`, `invalid_key`, `rate_limited`,
  `unreachable`, `unexpected` or `not_configured`. The proxy logs the same at
  startup.
- A 429 or 5xx from one DeepSeek model moves to the other. No other provider
  is tried unless `LLM_FALLBACK_PROVIDERS` names one.

### Behind a reverse proxy

Rate limits key on the client address, which behind a proxy is the proxy.

1. Find the proxy's address or subnet:
   `docker network inspect <project>_default`. Never list `0.0.0.0/0`.
2. Make the proxy append the header. nginx:
   `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`.
   Traefik: `forwardedHeaders.trustedIPs` lists only the hops in front of it.
3. Set `TRUSTED_PROXY_IPS=<address or subnet>`, then
   `docker compose up -d backend`.
4. Check: ten wrong passwords from one machine get `429`; another still
   signs in.

Running uvicorn by hand, pass `--no-proxy-headers`.

### The email migration stops on duplicates

Migration `0045` fails with `migration 0045 stopped: … (ana@example.com:
user ids 12, 31)` when two accounts differ only by case. Nothing changed.

1. Back up: `scripts/backup.sh`.
2. Inspect: `SELECT id, email, full_name FROM users WHERE id IN (12, 31)`
   and their `company_members` rows.
3. Keep one. Rename the others
   (`UPDATE users SET email = 'ana+old@example.com' WHERE id = 31`), or move
   their memberships and delete them.
4. `alembic upgrade head`.

### The SSO identity migration stops on duplicates

Migration `0050` fails with `migration 0050 stopped: … (user 7 at
https://idp.example.com: identity ids 3, 9)` when an account has two
identities at one provider. Nothing changed.

1. Back up: `scripts/backup.sh`.
2. Inspect: `SELECT id, subject, email, created_at, last_login_at FROM
   sso_identities WHERE id IN (3, 9)`.
3. Keep the most recent `last_login_at`; delete the rest
   (`DELETE FROM sso_identities WHERE id = 9`).
4. `alembic upgrade head`.

## Upgrade

1. `scripts/backup.sh`. Migrations do not roll back; the backup is the
   rollback.
2. `docker compose stop backend janitor`.
3. `git pull && docker compose build backend janitor llm-proxy frontend`.
4. `docker compose up -d`. With several replicas, start one first: Alembic
   takes no lock.
5. Check `GET /health/deep`, a running sprint, and the startup log for
   `is set but no longer does anything` (remove those settings).

Roll back: stop everything, `scripts/backup.sh restore <file> --yes`, check
out the previous release, build and start.

## Releasing

A release is a `vX.Y.Z` tag on `main`. Only a tag publishes.

1. On a branch from `main`: `python3 scripts/release_notes.py release X.Y.Z`.
   It folds `changelog.d/` into `CHANGELOG.md` under a dated `[X.Y.Z]`.
   Commit with the deleted fragments, fix the link references by hand, read
   the section, and land it as `chore(release): X.Y.Z`.
2. Tag as a person (a tag pushed by `GITHUB_TOKEN` starts no workflow):

   ```bash
   git fetch origin main
   git tag -a vX.Y.Z -m "vX.Y.Z" origin/main
   git push origin vX.Y.Z
   ```

3. Check the `Release` and `Publish Images` runs and the GitHub Release.

`release_notes.py notes vX.Y.Z` previews the notes. Pre-releases
(`vX.Y.Z-rc.N`) are tagged straight from `main` and never move `latest`.

The tag runs `release.yml` (GitHub Release, component SBOMs, `SHA256SUMS`)
and `images.yml` (six images to `ghcr.io/<owner>/<repo>/<service>:X.Y.Z`,
`linux/amd64`, each signed with keyless cosign and with an SBOM attestation).
A new GHCR package is private until you change it.

Verify an image before deploying it, by digest when you can:

```bash
image=ghcr.io/<owner>/<repo>/backend:X.Y.Z
cosign verify "$image" \
  --certificate-identity-regexp '^https://github\.com/<owner>/<repo>/\.github/workflows/images\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation "$image" --type cyclonedx \
  --certificate-identity-regexp '^https://github\.com/<owner>/<repo>/\.github/workflows/images\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  | jq -r '.payload' | base64 -d | jq '.predicate.metadata.component'

gh release download vX.Y.Z --pattern '*.cdx.json' --pattern SHA256SUMS
sha256sum -c SHA256SUMS
```

If verification fails, do not deploy the image.

For the browser sandbox, paste the `DELIVERY_BROWSER_IMAGE` and
`DELIVERY_BROWSER_IMAGES` digests the run's summary prints, never tags.

## Single sign-on

An Owner connects the identity provider at `/company/sso`
([`design/SSO.md`](design/SSO.md)). The operator:

1. Sets `SSO_CALLBACK_URL` (public URL of `/v1/auth/sso/callback`),
   `SSO_FRONTEND_URL` and `FERNET_SECRET_KEY`.
2. Lists at least one Owner per company in `SSO_BREAK_GLASS_EMAILS` before
   anyone turns on **Require SSO**.

| Provider | Setup |
|---|---|
| Google Workspace | OAuth client ID, type *Web application*; *Google* preset. |
| Microsoft Entra ID | Single-tenant app, *Web* redirect URI, client secret, optional `email` claim; preset with the tenant GUID. |
| Other OIDC | *Generic* preset, https issuer; PKCE (S256) and asymmetric ID-token signatures. Private issuer: `SSO_ALLOW_PRIVATE_ISSUERS=true`. |

Then **Test connection**, enable, and sign in once as an Owner. Only then
can Require SSO be turned on.

Verify each allowed domain: publish the TXT record the page shows, press
**Check**, and leave the record in place (a daily re-check unverifies it if
gone). The backend needs outbound DNS.

```
_la-fabrique-verify.acme.com.  TXT  "la-fabrique-domain-verification=<token>"
```

### Groups mapped to teams

Mapped teams change only at SSO sign-in
([`design/SSO_GROUP_MAPPING.md`](design/SSO_GROUP_MAPPING.md)). If nothing
changes, read the last observation's `state` (`absent`, `malformed`,
`overage`, `skipped`). For Entra overage (over 200 groups), use app roles or
*groups assigned to the application*. To remove someone at once, use the
break-glass exclusion on the team page.

### Break-glass: the provider is down or misconfigured

1. An Owner in `SSO_BREAK_GLASS_EMAILS` signs in with a password (audited).
2. If none can, add one and restart the backend.
3. The Owner fixes the connection or turns Require SSO off. Remove the
   address again.

Last resort, not audited (note it in the incident record):
`UPDATE sso_connections SET require_sso = false WHERE company_id = <id>;`

## Personal notifications

Each person's notifications are at `/notifications`. Security notices cannot
be muted. Email copies need `SMTP_HOST`. Failed sends:
`SELECT id, retry_count FROM outbox_events WHERE event_type =
'notification_email' AND status = 'failed';`

## Approvals in Slack and Teams

Managers approve held deliveries from Slack or Teams
([`design/CHAT_APPROVALS.md`](design/CHAT_APPROVALS.md)). Set
`FERNET_SECRET_KEY`, `PUBLIC_API_URL` (public https) and `CHAT_APP_URL`.
Expose only `/v1/integrations/` to the internet.

**Slack** (one app per company):

1. Bot scopes `chat:write` and `commands`. Install; copy the bot token;
   `/invite` the bot to the channel. Copy the Signing Secret.
2. On the Chat approvals page, enter workspace id, channel id, token and
   secret. Save; copy the **Request URL**.
3. Use that URL for *Interactivity* and a `/fabrique` slash command.
4. **Test connection**.

**Teams**:

1. Channel *Workflows* → "Post to a channel when a webhook request is
   received". Copy the URL (a bearer credential).
2. On the page, enter the tenant id and that URL. Save; copy the
   **Request URL**.
3. *Manage team* → *Apps* → *Create an outgoing webhook* with that URL.
   Paste its one-time security token as the signing secret.
4. **Test connection**.

Members link their account with `/fabrique link CODE` (Teams:
`@Fabrique link CODE`).

**Rotating** (Owner, on the page; an empty field keeps the value):

| Secret | New one from | Meanwhile |
|---|---|---|
| Slack signing secret | *Basic Information* → *Regenerate* | Clicks refused until saved. |
| Slack bot token | Reinstall the app | Posts retried (5 attempts). |
| Teams outgoing token | Recreate the outgoing webhook | Mentions refused until saved. |
| Teams incoming URL | Recreate the workflow | Posts retried. |

After a leak, rotate first, then read the `chat_approval_*` audit rows.

## Rotate keys

Generate keys as `CONFIGURATION.md`, Required Environment Variables, says.

| Key | Effect | How |
|---|---|---|
| `JWT_SECRET_KEY` | All sessions end. API tokens keep working. | Change, restart **all** replicas at once. At least 32 bytes (`secrets.token_urlsafe(48)`). |
| `FERNET_SECRET_KEY` | Nothing re-encrypts: stored secrets become unreadable. SSO-only companies need break-glass. | Note what each company stored, change, restart, re-enter. |
| `AUDIT_SIGNING_KEY` | Older rows report `hash_mismatch`. | Only after a leak. [Archive each chain](#archiving-the-audit-chain) with the old key first. |
| `LLM_PROXY_INTERNAL_TOKEN` | Company provider connections fail while values differ. | Same value on backend and llm-proxy; restart both. |
| `DEEPSEEK_API_KEY`, `QWEN_API_KEY` | None. | Change, restart `llm-proxy`. |

End every browser session without rotating:
`UPDATE session_families SET revoked_at = now(), revoked_reason = 'operator' WHERE revoked_at IS NULL;`

## MCP servers

HTTPS MCP servers on public addresses need nothing. **stdio servers are off**
until allowed (SECURITY.md §8.7):

1. Install the server in the backend image as a fixed program in a directory
   the backend cannot write (a wrapper that `exec`s it is fine). Never allow
   `npx`, `uvx`, `node`, `python` or a shell.
2. Set `MCP_STDIO_ALLOWED_COMMANDS=/opt/mcp/bin/github-server`
   (comma-separated) and restart the backend.
3. The Manager saves the server with exactly that `command` and checks
   `GET /v1/mcp/servers/{id}/tools`.

To withdraw one, remove its path and restart. Plain http needs
`MCP_HTTP_ALLOWED_HOSTS`; private addresses are always refused.

## Read `/health` and `/metrics`

| Endpoint | Meaning |
|---|---|
| `GET /health` | Liveness. `nats.state`: `connecting` or `connected`, with `jetstream`, `authenticated`, `tls`. |
| `GET /health/deep` | `down`: Postgres or LLM proxy unreachable. `degraded`: NATS, or Neo4j while enabled. Each component has a `reason`. |
| `capabilities` in `/health/deep` | Each `on`, `off` or `degraded` with a reason. Off by configuration is not a fault. |
| `GET /metrics` | Prometheus text, per replica: scrape all and add up. |

With DeepSeek alone expect `llm.embeddings: on`, `memory: on` and
`llm.failover: on, same_provider`. If embeddings are `degraded`, memory still
works on a hash vector. Fix: `docker compose build llm-proxy`, or
`EMBEDDING_PROVIDER=local-hash` if short of memory (about 400 MB per proxy
worker).

SSO domain re-checks: alert on rising
`fabrique_sso_domain_rechecks_total{reason="unavailable"}` (DNS broken), any
`fabrique_sso_domain_recheck_cycles_total{outcome="failed"}`, and
`reason="not_found"` or `"no_match"` (a domain was unverified; details in
`sso_domain_checks`).

## Run La Fabrique on itself

`scripts/self_host/self_host.py` builds a "La Fabrique" company on a snapshot
of this repository ([`scripts/self_host/README.md`](../scripts/self_host/README.md)).

### Run a self-improvement cycle

`scripts/rsi/cycle.py` measures a pull request against `main`. It never
merges ([`scripts/rsi/README.md`](../scripts/rsi/README.md)).

1. Save `GET /v1/findings?company_id=<id>` to `findings.json`.
2. `git fetch origin pull/<n>/head`, export `GITHUB_TOKEN` and the bench
   settings, and dry-run:

   ```bash
   python3 scripts/rsi/cycle.py run --dry-run --pr <n> --repository <owner>/<repo> \
     --spec scripts/self_host/specs/<name>-specification.json --findings findings.json \
     --cycle-id cycle-<n>-<name> --work /tmp/rsi/cycle-<n> --out /tmp/rsi/cycle-<n>.json \
     --python "$(cd backend && poetry env info -p)/bin/python" \
     --grading-image fabrique-grading:backend
   ```

3. Run without `--dry-run` (hours). Exit: 0 keep, 1 revert, 2 unmeasured,
   3 refused.
4. Keep: merge pinned to `candidate_sha`, then
   `python3 scripts/rsi/cycle.py record --record /tmp/rsi/cycle-<n>.json
   --merged-sha <merge commit> --out rsi/cycles/<cycle id>.json` and land
   that file. Otherwise close the pull request.
5. `python3 scripts/rsi/cycle.py verify --record /tmp/rsi/cycle-<n>.json`.

### Grading in the sandbox

Grading runs in the daemon's container sandbox. The host needs Docker or
Podman (`docker info`; not `bwrap`), room for 2 CPUs and 2 GB per grade,
`vscode-daemon/sandbox.py` from the same checkout, and a local grading image:

```bash
docker compose build backend
printf 'FROM %s\nRUN pip install --no-cache-dir "pytest>=8.1.1,<10"\n' \
  "$(docker compose images -q backend | head -1)" | docker build -t fabrique-grading:backend -
export FABRIQUE_GRADING_IMAGE=fabrique-grading:backend   # the bench: BENCH_GRADING_IMAGE
```

Without these the result is `unmeasured`.

## Documentation export (DOCX and PDF)

PDF needs `libpango-1.0-0`, `libpangoft2-1.0-0` and `fonts-dejavu-core`
(about +100 MB in the image, estimated). Outside the image the PDF route
answers 503 until you install them and restart.

## When something is wrong

**The daemon is absent.** `GET /v1/workspace/status` says `absent` or
`unknown`. Run `./scripts/start-daemon.sh` on the projects' computer.
Deliveries wait in JetStream; nothing to replay. Other fields
([`PROTOCOL.md`](../vscode-daemon/protocol/PROTOCOL.md)):

- `liveness: offline`: no heartbeat for 90 s. Start the daemon.
- `state: incompatible`: `reason` says which side to update.

**A sprint is stuck.** In order:

1. `planned`: `POST /v1/sprints/{id}/start`. `paused`: read `pause_reason`,
   then `POST /v1/sprints/{id}/resume`.
2. `GET /v1/inbox`: tasks waiting for a person.
3. `todo` tasks never start: `POST /v1/sprints/{id}/dispatch`; check
   `/health` says `connected` and the log says
   `Task Worker consuming task.assign`.
4. `in_progress` tasks of a crashed replica return to `todo` within 30 s.

**A provider is out of credit.** The sprint pauses, naming the provider. Add
credit or change the role's model, re-check with
[Check the model keys](#check-the-model-keys), then resume.

**The nightly real-model bench spends too much (the kill switch).** Set the
repository variable `BENCH_REAL_MODELS` to anything but `true`, or disable
the Bench workflow. Each run stops at `BENCH_TOKEN_BUDGET` (300000).

**A revert pull request opened.** The nightly `watch` job found a merged
self-improvement cycle regressed. Nothing merges it automatically. Read the
numbers, then merge it, or close it and record a finding.

**The runners stop taking jobs, or their disk fills up.**

```bash
df -h /
gh run list --status in_progress     # wait until empty, or cancel
systemctl --user restart 'actions-runner-lafabrique*'
```

Never prune the named volumes `la-fabrique_*`.

### From an error id to the log line

A 500 returns an `error_id`. Search every replica:

```bash
docker compose logs backend --since 24h | grep 'error_id=<id>'
```

### CI failures on the shared runners

| Symptom | Action |
|---|---|
| Port in use in e2e | `docker ps`, remove the leftover container, rerun. |
| Image missing right after a green build | Another job removed a fixed tag; tag with the run id. |
| A timing test fails | Rerun; failing twice on a quiet machine is real. |
| `exit code 137`, `Killed` | OOM: `dmesg -T \| grep -i 'out of memory'`; see [Runner capacity](#runner-capacity). |
| Job stuck with no log lines | Cancel, then restart the runners (above). |

## Landing locally

Changes reach `main` through `scripts/land.sh`; GitHub is a mirror
([`design/LOCAL_LANDING.md`](design/LOCAL_LANDING.md)).

```bash
scripts/land.sh my-branch            # merge, check, fast-forward local main, record
scripts/land.sh my-branch --push     # the same, then mirror main and the notes
scripts/land.sh --push               # mirror main and refs/notes/verification only
scripts/land.sh --verify <sha>       # re-run the checks on <sha>, compare with its record
```

It merges `main` and the branch in `<checkout>.land-worktree` (never a
rebase), runs `scripts/check.sh full`, then writes a record under
`refs/notes/verification` and fast-forwards `main`. The record is an
unsigned claim by that machine. A change to a protected path needs
`LAND_OWNER=1` (a guard, not a security boundary).

Exit: 0 done; 1 checks failed or conflict (`main` untouched); 2 usage;
3 refused (protected path, diverged, nothing to land); 4 lock held.

When it goes wrong:

- Lock held, nothing running: check `.git/fabrique/land.lock/owner`, then
  remove the folder.
- Diverged: merge `origin/main` into local `main` by hand (never force),
  then land again.
- Push refused: fetch, `git notes --ref=verification merge`, push again.

## Protected paths

`.github/workflows/protected-paths.yml` fails a pull request that touches a
path in `backend/app/services/contracts/protected_paths.py` unless a code
owner approved the head. It gates nothing until the owner makes
`protected-paths` a required check and turns on "Require review from Code
Owners" for `main`.

## Third-party CLIs on the daemon's computer

A terminal CLI works on tasks only as a console producer a Manager chose
(SECURITY.md §7.4). `GET /v1/companies/{id}/console_producers` answers
`{"producers": {}}` when none is set. Remove the old
`CONSOLE_ASSISTANT*`, `COPILOT_CLI_*` and `CLAUDE_CLI_*` settings from
`.env`.

## Forge policy

A company's git reaches an outside forge only as its policy allows: `none`,
`fetch` or `publish` ([`contexts/release.md`](contexts/release.md)).

- Lower it: `PUT /v1/companies/{id}/forge_policy` with
  `{"forge_policy": "none"}`, or *Local only* on the Models page.
- Air-gapped: `FORGE_POLICY_MAX=none` on the backend and every daemon.
- Upgrade the daemons with the backend so both enforce it.

### Bring a local copy up to date

`POST /v1/companies/{id}/git/sync_base` only fast-forwards. If it refuses:

| Outcome | Action |
|---|---|
| `dirty` | Commit or stash in the checkout. |
| `operation_in_progress` | `git rebase --abort` or `git merge --abort`. |
| `diverged` | Decide by hand. |
| `remote_unreachable` | Run `git fetch` there to see why. |
| `forge_disabled` | The policy is `none`. |

## Run window

A company can start deferrable work only in chosen hours
([`contexts/loop.md`](contexts/loop.md)):

```http
PUT /v1/companies/{id}/run_window
{"run_window": {"timezone": "America/Montevideo", "preset": "provider_off_peak:deepseek"}}
```

`null` turns it off. To skip it, a Manager ticks *Urgent* on the board. When
a new year's Chinese holidays are published, add `<year>.json` to
`backend/app/services/platform/holiday_calendars/cn/`.

## Runner capacity

Seven self-hosted runners share one machine (about 4 cores, 16 GB).

- **Which jobs run**: `.github/workflows/changes.yml`.
- **e2e jobs at once**: label runners `e2e` **first**, then set the variable,
  or e2e jobs wait 24 h and fail.

  ```bash
  gh api repos/{owner}/{repo}/actions/runners \
    --jq '.runners[] | [.id, .name, ([.labels[].name] | join(","))] | @tsv'
  gh api -X POST repos/{owner}/{repo}/actions/runners/<id>/labels -f 'labels[]=e2e'
  gh variable set E2E_RUNS_ON --body '["self-hosted","linux","x64","lafabrique","e2e"]'
  gh variable delete E2E_RUNS_ON                        # lift the limit
  ```

- **Backend test workers**: `gh variable set BACKEND_TEST_WORKERS --body 2`
  (default 4).
- **e2e memory caps**: `E2E_POSTGRES_MEMORY`, `E2E_NATS_MEMORY`.

## Backups

```bash
scripts/backup.sh                                                     # back up
scripts/backup.sh restore backups/postgres-factory-<time>.dump --yes  # restore
```

- Writes a Postgres dump with a `.sha256` (and a Neo4j export while
  enabled) to `backups/`, keeping `BACKUP_KEEP_DAYS` (14).
- Backups hold every company's data and credentials. Keep them outside the
  checkout and copy them to another machine.
- Run it daily:
  `17 3 * * * cd /srv/la-fabrique && scripts/backup.sh >>backups/backup.log 2>&1`

**Restore.**

1. `docker compose stop backend janitor`.
2. Run the restore. It restores into a new database, then swaps it in; the
   old one is kept as `<db>_pre_restore_<time>`. A failed restore changes
   nothing.
3. Start the backend; it migrates to head.
4. Drop `<db>_pre_restore_<time>` once checked.

Always restore the whole database, never single tables.

## Retention

The janitor applies these daily. `0` keeps everything.

| Setting | Default | Deletes |
|---|---|---|
| `RETENTION_OUTBOX_DAYS` | 7 | delivered outbox events |
| `RETENTION_AGENT_RUNS_DAYS` | 0 | superseded run versions nothing points at |
| `RETENTION_KPI_TURNS_DAYS` | 0 | KPI rows outside a hash chain |
| `RETENTION_NOTIFICATIONS_DAYS` | 90 | notifications |

There is no audit or task-event retention: each row is a link of a hash
chain.

### Archiving the audit chain

Archive a company's whole chain, never part of it:

1. Back up.
2. `\copy (SELECT * FROM audit_log WHERE company_id = <id> ORDER BY chain_seq) TO 'audit-<id>.csv' CSV HEADER`
3. Keep with it the `GET /v1/audit/chain` answer, each
   `GET /v1/audit/anchors/{id}/export`, and the `AUDIT_SIGNING_KEY` used.
4. Store it read-only. The live rows stay.

### Anchoring the audit chain outside

An Owner turns on anchors at `/company/audit-anchors`
([`design/AUDIT_ANCHORS.md`](design/AUDIT_ANCHORS.md)).
`GET /v1/audit/anchors` should read `covered`. `undelivered`: fix the webhook
or authority URL, then `POST /v1/audit/anchors`. `anchor_mismatch` is an
incident (SECURITY.md §9).

## How many workers may run

Several backend replicas may share one Postgres and NATS, with the same
`.env` and `NATS_NAMESPACE`. Locks make it safe
(`services/loop/replicas.py`). The compose file's fixed container name means
`--scale backend=2` does not work: add a service or a host.

- Set Postgres `tcp_keepalives_idle` (e.g. 60) so a vanished host's tasks
  are released.
- Size `max_connections` for `replicas × (5 + 10 + 3)`.

## Neo4j

Optional and on by default. The backend does not wait for it. The compose
image is Enterprise under an evaluation licence: in production license it,
use Community, or turn it off:

```bash
NEO4J_ENABLED=false docker compose up -d --scale neo4j=0
```

Memory then runs on pgvector alone. Nothing is backfilled later.

## Securing the bus

Anything that can publish on NATS can make the daemon run commands
(SECURITY.md §8.6).

1. In `.env`, set `NATS_BACKEND_PASSWORD` and `NATS_DAEMON_PASSWORD`
   (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), then
   `docker compose up -d`. Start the daemon with
   `NATS_DAEMON_PASSWORD=... ./scripts/start-daemon.sh`.
2. Check: `curl -s localhost:8000/health` shows `"authenticated": true`.
3. After changing NATS subjects, run `python3 scripts/nats_config.py` and
   commit `nats/nats-server.conf`.
4. TLS, on the broker's machine:

   ```bash
   mkdir -p nats/certs && cd nats/certs
   openssl req -x509 -newkey rsa:4096 -sha256 -days 825 -nodes \
     -keyout ca.key -out ca.crt -subj "/CN=Fabrique NATS CA"
   openssl req -newkey rsa:4096 -nodes -keyout server.key -out server.csr -subj "/CN=nats"
   printf 'subjectAltName=DNS:nats,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > server.ext
   openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
     -days 825 -sha256 -extfile server.ext -out server.crt
   ```

   Keep `ca.key` offline. Set `NATS_TLS=true`, run
   `docker compose up -d nats`, and start the daemon with
   `NATS_TLS=true NATS_TLS_CA=/path/to/ca.crt`. `/health` shows `"tls": true`.
5. Client certificates (optional): sign with `extendedKeyUsage=clientAuth`,
   set `NATS_TLS_VERIFY=true`, and name the files with `NATS_TLS_CERT` /
   `NATS_TLS_KEY` (daemon) or `NATS_BACKEND_TLS_*` (backend).
6. Rotate a password: change it and restart broker, backend and daemon
   together.

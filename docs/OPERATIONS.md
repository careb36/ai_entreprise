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
   per company. `docs/CONFIGURATION.md` lists every variable.
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

The browser's session lives in `HttpOnly; Secure; SameSite=Strict` cookies
(issue #524): serve the frontend and the API over https (a browser accepts
`Secure` cookies from `http://localhost` too), and list the frontend's
origins in `CORS_ORIGINS` (never `*`). When the frontend and the API are on
different hosts, the page cannot read the API's CSRF cookie and asks
`GET /v1/auth/csrf` for it; keep both under one site (the same registrable
domain) or `SameSite=Strict` cookies are not sent at all.

The packaged desktop app (`npm run electron:build` in `frontend/`) needs none
of that: it serves its page and forwards `/v1/*` from its own
`http://127.0.0.1:<port>`, so set only `BACKEND_URL` on the computer that runs
it (default `http://localhost:8000`; https for a backend on another machine,
since the app refuses plain http beyond localhost; `docs/CONFIGURATION.md`,
The desktop app).
Leave `SESSION_COOKIE_SECURE` on and `CORS_ORIGINS` and `SSO_FRONTEND_URL` as
they are for the web frontend. If sign-in in the desktop app does not hold,
check that `BACKEND_URL` reaches the backend and that nothing else rewrote the
cookies (`docs/design/SESSIONS.md`, Desktop app).

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

## Releasing

A release is a `vX.Y.Z` tag on `main`, following
[Semantic Versioning](https://semver.org/) (issue #512). A tag is the only
thing that publishes: a merge to `main` publishes nothing. The process is
taken from AixleHQ/flow's `docs/operations/releasing.md` (Apache-2.0, see
`NOTICE`), on `main` instead of a `develop` branch.

1. **Changelog pull request.** On a branch cut from `main`, run
   `python3 scripts/release_notes.py release X.Y.Z`: it moves everything
   under `[Unreleased]` in `CHANGELOG.md` into a dated `## [X.Y.Z] -
   YYYY-MM-DD` section. Update the link references at the end of the file by
   hand, read the section as a user would (it is the release announcement),
   and open the pull request as `chore(release): X.Y.Z`.
2. **Merge it** once its head is green.
3. **Tag the merge commit on `main`** and push the tag, as a person:

   ```bash
   git fetch origin main
   git tag -a vX.Y.Z -m "vX.Y.Z" origin/main
   git push origin vX.Y.Z
   ```

   A tag created with a workflow's `GITHUB_TOKEN` starts no other workflow,
   so nothing would be published.
4. **Check both runs** (`Release`, `Publish Images`) and the GitHub Release.

`python3 scripts/release_notes.py notes vX.Y.Z` prints the notes exactly as
the release will show them.

**Pre-releases** (`vX.Y.Z-rc.N`) are for trying a release on a staging
deployment first. Tag them straight from `main`, without a changelog pull
request: they get no section of their own, and their GitHub Release, marked
pre-release, shows `[Unreleased]` as of the tagged commit. They never move
`latest`.

What pushing the tag runs:

- **`release.yml`** checks that the tag is `vX.Y.Z` or `vX.Y.Z-pre` and that
  its commit is on `main`, then creates the GitHub Release (`gh release
  create`): the notes are the `[X.Y.Z]` section of `CHANGELOG.md` (or
  `[Unreleased]` for a pre-release), the list of images, and the pull
  requests merged since the previous tag. A tag off `main`, a missing or
  empty section fails the run with the reason.
- **`images.yml`** makes the same checks and publishes the five images
  `docker-build.yml` builds (`backend`, `janitor`, `frontend`, `llm-proxy`,
  `vscode-daemon`) to `ghcr.io/<owner>/<repo>/<service>:X.Y.Z`, plus
  `:latest` for a stable release. Those five are built for `linux/amd64`
  only, on the self-hosted runner with plain `docker build`. A sixth image,
  `browser-sandbox` (`vscode-daemon/images/browser`, issue #529), is
  published from the same tag by a job of its own, outside that list:
  `linux/amd64` only with buildx, the same `:X.Y.Z` (plus `:latest` for a
  stable release), the same SBOM and the same keyless signature as the rest.
  Its arm64 variant builds under QEMU, but its browser aborts under emulation
  (`.github/workflows/browser-image-probe.yml`, manual `workflow_dispatch`),
  so it is not published until it has been run, with its probe, on arm64
  hardware. A new package on GHCR is
  private until its visibility is changed in the package's settings.

Each release also records what it is made of and who built it (issue #522,
part C):

- **SBOMs of the components.** `release.yml` writes a CycloneDX SBOM of each
  component's lock with [syft](https://github.com/anchore/syft) (Apache-2.0;
  the release binary is pinned and checked against its sha256 in the
  workflow): `backend/poetry.lock`, `llm-proxy/poetry.lock`,
  `vscode-daemon/requirements.txt` and `frontend/package-lock.json` (without
  its devDependencies). They are attached to the GitHub Release as
  `la-fabrique-<component>-X.Y.Z.cdx.json` with a `SHA256SUMS`.
- **SBOMs of the images.** `images.yml` scans each image it pushed (the base
  image's packages included) into `<image>-X.Y.Z.cdx.json`, kept as the run's
  artifact `sbom-<image>` for 90 days and attached to the image in the
  registry as a signed CycloneDX attestation. `browser-sandbox` is scanned by
  its digest in the registry (buildx pushes it without loading it into the
  Docker daemon).
- **Signatures.** `images.yml` signs each image by its digest with keyless
  [cosign](https://github.com/sigstore/cosign): the job's GitHub OIDC token
  (`id-token: write`, on the two publish jobs only) gets a short-lived
  certificate from Fulcio naming this workflow and the tag, and the signature
  is logged in the public Rekor transparency log. No signing key exists to
  store, rotate or leak.

Verify an image before deploying it (cosign 2.x or 3.x):

```bash
image=ghcr.io/<owner>/<repo>/backend:X.Y.Z
cosign verify "$image" \
  --certificate-identity-regexp '^https://github\.com/<owner>/<repo>/\.github/workflows/images\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation "$image" --type cyclonedx \
  --certificate-identity-regexp '^https://github\.com/<owner>/<repo>/\.github/workflows/images\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  | jq -r '.payload' | base64 -d | jq '.predicate.metadata.component'
```

Prefer the digest the run printed in its summary (`...@sha256:...`) to the
tag: a tag can be pushed again, a digest cannot. A failed verification means
the image was not built by this repository's `images.yml` from a `v*` tag;
do not deploy it. Verifying in the cluster at admission (Sigstore's
policy-controller or Kyverno) is not set up (SECURITY.md §5.3). The signing
steps run on the self-hosted runner and have not been exercised by a release
yet; the first tag after this change is their first run
(`docs/design/SELF_HOSTED_RUNNER.md` records the runner's risks).

The browser sandbox is pinned the same way: the publish run's summary prints
the line to paste into the backend's environment,
`DELIVERY_BROWSER_IMAGE=ghcr.io/<owner>/<repo>/browser-sandbox@sha256:…`
(`docs/CONFIGURATION.md`), so the application check runs its browser e2e
commands in the published multi-arch image, by digest. The verification
commands above apply to it unchanged (same workflow, same certificate
identity; its digest names the manifest list).

Check a release's component SBOMs against their checksums:

```bash
gh release download vX.Y.Z --pattern '*.cdx.json' --pattern SHA256SUMS
sha256sum -c SHA256SUMS
```

Every pull request adds its line under `[Unreleased]` in the same pull
request (`CONTRIBUTING.md`), so cutting a release is a move, not a search
through the history. Migrations do not roll back: a release that ran a
migration the previous code cannot read cannot be rolled back past without
the backup (Upgrade, above).

## Single sign-on

A company's Owner connects its identity provider on the **Single sign-on**
page (`/company/sso`); the design and threat model are in
`docs/design/SSO.md`, the settings in `docs/CONFIGURATION.md`. The operator's
part first:

1. Set `SSO_CALLBACK_URL` to the address the browser reaches the backend's
   `/v1/auth/sso/callback` at (behind a proxy, the public `https://` one) and
   `SSO_FRONTEND_URL` to the frontend's address. Set `FERNET_SECRET_KEY`, or
   client secrets are stored with a `plaintext:` prefix.
2. Put at least one Owner per company that will require SSO in
   `SSO_BREAK_GLASS_EMAILS` before anyone turns the requirement on (below).

Then, at the provider, register a web application with the redirect URI the
SSO page shows, and on the page:

- **Google** (Google Workspace): Google Cloud console, APIs & Services,
  Credentials, *OAuth client ID* of type *Web application*; add the redirect
  URI. Choose the *Google* preset, paste the client ID and secret, and list
  the Workspace domains (a `gmail.com` address is never accepted as a
  company domain).
- **Microsoft Entra ID**: Entra admin center, App registrations, *New
  registration*, single tenant, a *Web* redirect URI; create a client secret
  under *Certificates & secrets*; under *Token configuration* add the
  optional `email` claim to the ID token (Entra sends no `email` otherwise,
  and sends no `email_verified`; the tenant-pinned issuer is what vouches for
  the address). Choose the *Microsoft Entra ID* preset and enter the
  *Directory (tenant) ID*, a GUID: multi-tenant issuers (`common`,
  `organizations`) are refused.
- **Any other OpenID Connect provider** (Okta, Keycloak, Auth0, …): the
  *Generic* preset with the issuer URL (the one whose
  `/.well-known/openid-configuration` answers, https). A provider on a
  private network needs `SSO_ALLOW_PRIVATE_ISSUERS=true`. The provider must
  support the Authorization Code flow with PKCE (S256) and sign ID tokens
  with an asymmetric algorithm (RS256, PS256, ES256, …).

Press **Test connection**: it fetches discovery and the signing keys and shows
what the provider answered. Turn the connection on, then sign in once through
it yourself (log out, *Sign in with SSO*, type the company's SSO name or your
work email): only after an Owner has done that can **Require SSO** be turned
on. With auto-join off, only existing members (invited by email) get in; with
it on, anyone the provider authenticates in an allowed domain who has no
account yet joins with the default role, never Owner. An existing account is
linked only when it is a member of this company and of no other; it then
becomes SSO-only (its password is cleared) unless it is an Owner. Someone who
already has an account and is not a member is told to ask an Owner for an
invitation; someone who belongs to other companies too keeps signing in with
their password. A domain can be listed by one company only.

Every sign-in writes an `sso_sign_in` audit row with what the provider asserted
and the outcome (`created`, `linked`, `signed_in` or `refused:<reason>`), and
every change an `sso_connection_*` row. Turning **Require SSO** on (with the
connection enabled) ends the browser sessions its members started with a
password, except a break-glass Owner's; the audit row counts them
(`sessions_ended`). Removing a member (`DELETE /v1/company/{id}/members/{user_id}`)
ends all of that person's sessions. Removing someone only at the provider
ends nothing: their session lasts until it is refreshed past
`REFRESH_TOKEN_ABSOLUTE_DAYS` or they are removed here (issue #524,
`docs/design/SESSIONS.md`).

### Break-glass: the provider is down or misconfigured

While a company requires SSO, password sign-in is refused for its members.
To get in:

1. An Owner listed in `SSO_BREAK_GLASS_EMAILS` signs in with their password as
   usual; each such sign-in writes an `sso_break_glass_login` audit row. A
   listed address that is not an Owner of every SSO-requiring company it
   belongs to is still refused.
2. If no listed Owner can, the operator adds one (`SSO_BREAK_GLASS_EMAILS` in
   `.env`) and restarts the backend.
3. The Owner fixes the connection or turns **Require SSO** off (or the
   connection off: the requirement is void while it is disabled), then the
   operator removes the address from the list again.

As a last resort the operator can switch the requirement off in the
database: `UPDATE sso_connections SET require_sso = false WHERE company_id = <id>;`
(that change is not in the audit chain; record it in the incident notes).

## Approvals in Slack and Teams

A delivery held for a person's acceptance (the risk hold) can be approved or
sent back from Slack or Microsoft Teams (issue #512, phase 4). The design and
threat model are in `docs/design/CHAT_APPROVALS.md`, the settings in
`docs/CONFIGURATION.md`. The operator sets `FERNET_SECRET_KEY` (required),
`PUBLIC_API_URL` (the public `https://` address Slack and Teams reach the
backend at) and `CHAT_APP_URL` (the frontend the messages link to). The
backend must be reachable from the internet at `/v1/integrations/` only; the
rest of the API need not be.

**Slack.** One Slack app per company, created by a workspace admin at
api.slack.com/apps:

1. *OAuth & Permissions*: bot token scopes `chat:write` (post and edit the
   approval) and `commands` (the `/fabrique` command). No other scope is
   needed: La Fabrique never reads profiles or emails. Install the app to the
   workspace and copy the **Bot User OAuth Token**; invite the bot to the
   channel (`/invite @app`).
2. *Basic Information*: copy the **Signing Secret**.
3. On La Fabrique's Chat approvals page (an Owner): the workspace (team) id,
   the channel id, the bot token and the signing secret; save, then copy the
   **Request URL** the page shows.
4. *Interactivity & Shortcuts*: on, Request URL = that URL. *Slash Commands*:
   create `/fabrique` with the same Request URL.
5. *Test connection* on the page: it calls `auth.test` (the token must belong
   to the workspace id entered) and posts a line to the channel.

**Teams.** No Azure registration is needed (an outgoing webhook, not a bot):

1. In the channel, *Workflows* → "Post to a channel when a webhook request is
   received" (or a classic incoming webhook where still available); copy the
   URL. It is a bearer credential: whoever holds it can post to the channel.
2. On the Chat approvals page: the Microsoft Entra tenant id and that URL as
   the incoming webhook URL; save, and copy the **Request URL**.
3. In the team: *Manage team* → *Apps* → *Create an outgoing webhook*, name it
   (people will mention it: `@Fabrique approve K7Q2M9PX`), callback URL = the
   Request URL. Teams shows a **security token** once: paste it as the signing
   secret and save.
4. *Test connection* posts a card through the incoming webhook.

**People.** Each member links their own account on the same page (*Link my
Slack account* gives a ten-minute code to type as `/fabrique link CODE`, or
`@Fabrique link CODE` in Teams). Only a linked account with the Manager role
or above decides; a Viewer's click is refused and recorded.

**Rotating the secrets.** Each is changed on the page by an Owner; an empty
field keeps the stored value, and every change is audited by field name.

| Secret | Where to get a new one | Effect while it changes |
|---|---|---|
| Slack signing secret | Slack app → *Basic Information* → *Regenerate* | Clicks are refused (401) between regenerating it in Slack and saving it here. |
| Slack bot token | *OAuth & Permissions* → reinstall or rotate | Posts fail and are retried by the outbox (five attempts) until the new token is saved. |
| Teams outgoing webhook token | delete and recreate the outgoing webhook (Teams shows the token only at creation) | Mentions are refused until the new token is saved. |
| Teams incoming webhook URL | recreate the workflow or webhook | Posts fail and are retried until the new URL is saved. |

If a signing secret leaked, rotate it first, then read the
`chat_approval_*` audit rows since the leak: a forged request still needed a
linked Manager's chat user id and a current approval, and each decision names
both.

## Rotate keys

| Key | Where | What rotating does | How |
|-----|-------|--------------------|-----|
| `JWT_SECRET_KEY` | backend | Every session and Glassroom ticket stops working, and people log in again: access tokens no longer verify, and the refresh-token families end too, since refresh tokens are stored as HMAC under this key and match nothing after the change (issue #524, `docs/design/SESSIONS.md`). API tokens (`/v1/auth/tokens`) are stored as hashes and keep working until they expire or are revoked. To end every browser session without rotating (a suspected session theft), run `UPDATE session_families SET revoked_at = now(), revoked_reason = 'operator' WHERE revoked_at IS NULL;` and record it in the incident notes. | Change it in `.env` and restart every backend replica at once: a replica with the old key refuses the new tokens. |
| `FERNET_SECRET_KEY` | backend | Nothing re-encrypts yet. Values encrypted under the old key (project credentials, MCP server secrets, provider connection keys, run secrets, SSO client secrets, Slack and Teams credentials) can no longer be read; a company that requires SSO cannot sign in until its Owner enters the client secret again (break-glass, Single sign-on above). MCP servers and run secrets still list by name; an unreadable run secret is left out of the run and logged. | Before the change, note what each company stored (Models page, MCP servers, run secrets, Chat approvals). Change the key, restart, and enter the values again. |
| `AUDIT_SIGNING_KEY` | backend | Rows keyed with the old key no longer verify: `GET /v1/audit/chain` reports `hash_mismatch` from the first of them. The rows are intact; only the key that proves it changed. | Rotate only when the key leaked. First archive each company's chain (Archiving the audit chain, below) and keep the old key with that archive in your secret store, so the old part can still be verified offline. Then change the key, restart, and record the date. Anchors (issue #542) are not affected by the rotation: they cover the statement, not the key, and their tokens and customer signatures still verify. |
| `LLM_PROXY_INTERNAL_TOKEN` | backend and llm-proxy | While the two values differ, company provider connections (`conn:<id>:<model>`) fail and say why. The deployment's own models keep working. | Set the same new value on both and restart both. |
| Model keys (`GEMINI_API_KEY`, …) | llm-proxy | Nothing beyond the restart. | Change the key and restart `llm-proxy`. A company changes its own provider key on its Models page. |

`docs/CONFIGURATION.md` (Required Environment Variables) has the command that generates
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

## Run La Fabrique on itself

The factory can develop La Fabrique the way it develops any project (issue
#329): `scripts/self_host/self_host.py` makes a "La Fabrique" company on a
snapshot of this repository, imports La Fabrique's own specification and
architecture (`docs/architecture/`), checks they render `docs/ARCHITECTURE.md`
and `docs/adr/` unchanged, and can specify an issue, run its sprint, show the
integration a person reviews and compile the documentation pack. The steps
against a real stack, with real models, are in
[`scripts/self_host/README.md`](../scripts/self_host/README.md); the e2e run
exercises the same harness with the fake model (step 9d). The snapshot has no
remote: what is kept comes back to this repository as an ordinary pull
request.

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

**Offline is not incompatible.** `GET /v1/workspace/status` tells them apart
([`vscode-daemon/protocol/PROTOCOL.md`](../vscode-daemon/protocol/PROTOCOL.md)):
- `liveness` comes from the daemon's heartbeat (`workspace.heartbeat`, every
  20 s by default), timed on the backend's clock: `online`, `stale` after
  `DAEMON_HEARTBEAT_STALE_SECONDS` (60) without one, `offline` after
  `DAEMON_HEARTBEAT_OFFLINE_SECONDS` (90). `heartbeat` says when the last one
  was observed and how many beats were missed. `source: request` means no
  heartbeat was ever observed (a daemon from before protocol 1, or a broker
  configuration that does not allow the subject: regenerate it with
  `python3 scripts/nats_config.py`) and the state comes from asking
  `workspace.status`, as before. Offline: start the daemon, as above.
- `state: incompatible` means the daemon answers but speaks a protocol
  version outside the backend's range; `reason` says which side to update
  (`update the daemon`: restart it from the same checkout as the backend,
  `./scripts/start-daemon.sh`; `update the backend`: deploy the backend that
  matches the daemon). Meanwhile requests to it are refused with that reason
  and a task's delivery is not sent or queued: its evidence says `the
  workspace daemon is incompatible: <reason>`. Once a compatible daemon
  answers or beats, work resumes; the refusal lifts at the latest
  `DAEMON_HEARTBEAT_OFFLINE_SECONDS` after the incompatible one was last heard.
- `protocol.state: unknown` with `unknown protocol version (older daemon)` is
  a daemon that reports no version: it is served as before. Update it when
  convenient to get the heartbeat.
- Everything the daemon says about itself (version, protocol, host, id) is
  under `reported_by_daemon`: its claim, not an observation.

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

**The nightly real-model bench spends too much.** The job `bench-real` in
`.github/workflows/bench.yml` runs only while the repository variable
`BENCH_REAL_MODELS` is `true`. To stop it, set the variable to anything else
(or delete it), or disable the Bench workflow in the repository's Actions
tab; a run already going can be cancelled there. Each run also stops itself
past `BENCH_TOKEN_BUDGET` reported tokens (a repository variable; 300000 when
unset): the running sprint is stopped, the remaining specifications are
skipped and the card says `budget_exceeded`. The budget counts only the
turns whose usage the provider reported, so the provider's own spending
limit is still the last guard (`bench/README.md`, "The nightly real-model
run").

**The runners stop taking jobs, or their disk fills up.** The runner services
are user services on this machine: `systemctl --user is-active
'actions-runner-lafabrique*'` answers `active` for all seven while GitHub shows
some `offline`, or `online` and `busy=false` with the queue growing and nothing
dispatched (`gh run list --status in_progress` empty). A runner whose job died
mid-run — usually under disk pressure — keeps its session wedged: the service
stays up, the connection does not. Check `df -h /` first, then restart the
wedged services, and only once `gh run list --status in_progress` is empty, or
you cancel a running e2e job:

```bash
systemctl --user restart 'actions-runner-lafabrique*'
```

`runner-hygiene.yml` keeps the disk bounded daily (#566): this repository's
stopped e2e and smoke containers with their volumes, dangling images older than
a week, and anonymous dangling volumes older than a week are removed, and the
buildkit cache is kept under 2 GB. It never prunes by name: the named volumes
`la-fabrique_*` hold a person's development data (they show `dangling` while
that stack is down), and the named images belong to the other repositories
sharing this host. `e2e.yml` now removes its containers with `docker rm -fv`, so
a finished run leaks nothing.

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
   `head` and `anchors`), the export of each anchor
   (`GET /v1/audit/anchors/{id}/export`) and, if the chain is keyed, which
   `AUDIT_SIGNING_KEY` keyed it.
3. Keep the archive read-only. Deleting the archived rows from the live
   database is not supported yet, because the chain would report the gap.
   Until the chain can start from a checkpoint, the archive is a copy and
   the database keeps the rows.

**Anchoring the audit chain outside** (issue #542,
`docs/design/AUDIT_ANCHORS.md`). `AUDIT_SIGNING_KEY` is held by the factory,
so the chain alone does not stop someone with that key and the database from
rewriting it. An Owner turns on anchors on the Audit anchors page
(`/company/audit-anchors`): a cadence (hourly by default), posting each head
to the company's webhook, an RFC 3161 timestamping authority (its URL and its
certificate, taken from the authority itself), and the customer's Ed25519
public keys, whose private halves sign heads from the customer's own job.
`GET /v1/audit/anchors` (and `anchors` in `GET /v1/audit/chain`) must read
`covered` after a cadence; `uncovered_tail` between cadences is normal,
`no_anchors` means nothing outside covers the chain, and `anchor_mismatch` is
an incident: the chain or an anchor was rewritten (handle it as SECURITY.md
§9). An anchor whose delivery failed shows as `undelivered`: check the
webhook's last error or the authority's URL, then `POST /v1/audit/anchors`.
Rotating a customer key: add the new public key, move the signing job to it,
then retire the old one (it keeps verifying what it signed). Replacing the
authority's certificate: paste the new one; anchors keep the certificate
they were verified with.

## How many workers may run

Several backend replicas may run against one Postgres and one NATS. Each
replica serves the HTTP API and runs the workers. Give each its own host
port, or put them behind a load balancer: the Glassroom works through any
replica, since each one subscribes to every sprint's stream. All replicas
need the same `.env` and the same `NATS_NAMESPACE`. In `docker-compose.yml`
the backend has a fixed `container_name` and port, so a second replica is a
second service or a second host; `docker compose up --scale backend=2` does
not work as the file stands.

What makes several replicas safe (`backend/app/services/loop/replicas.py`,
`backend/app/services/platform/locks.py`):

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

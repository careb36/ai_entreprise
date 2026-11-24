# Owner actions from the app: a design and its threat model

> **Status: proposed, 2026-11-24, for the owner's review.** Issue #701 (the
> first action). The first slice is built: the catalogue, its endpoints, the
> inbox entry and the page, with one action, `self_host_container_run`.
> `land_branch`, `push_main` and `close_issue` are designed here and are
> **not built yet** (see "What is built"). Every rule of
> [`docs/POSITIONING.md`](../POSITIONING.md) applies unchanged. SDL practice 3;
> the boundaries are the threat-model skill's B1 to B14
> ([`reference.md`](../../.claude/skills/threat-model/reference.md)).

## Why

Some steps only the owner takes: "run the harness once in container mode and
record the run ids" (#701), land a branch, mirror `main` to GitHub, close an
issue. Today each is a line of text the platform or a coding agent hands her.
She needs a terminal, an API token (`FABRIQUE_API_TOKEN`) and copies ids by
hand, and nothing in the company's record says she did it.

An **owner action** is one of those steps, written as code, started from the
app by the company's Owner after a confirmation that shows its cost and
effect, run by the platform, audited in the company's hash chain, and
recorded as observations the platform made.

## Rules

1. **A catalogue, never a command.** Each action is a Python entry in
   `services/management/owner_actions.py`: a name, typed parameters checked
   by a schema (no free text that reaches a shell, a path or a git argument),
   what it does, what it costs and what it records. Nothing else can run.
2. **Owner only.** A signed-in person whose effective role in the company is
   Owner. A Manager gets 403. An API token gets 403 whatever its role, as for
   the Owner-only settings (`docs/contexts/platform.md`): a stolen token must
   not start one. Without `AUTH_REQUIRED` there is still no anonymous run.
3. **Confirmed.** `POST …/preview` returns the effect, the cost and a
   `confirmation` digest (sha256 over the action, its validated parameters and
   the effect text shown). `POST …/runs` must echo that digest; any other
   parameters are a 409. What the dialog showed is what runs (for
   `close_issue`, what is posted).
4. **One at a time.** At most one run of an action is `running` in the
   deployment (the daemon and the self-hosting company are shared). A second
   start is a 409 naming the running one. Runs of different actions that
   conflict refuse each other (a land while a self-hosting sandbox run is in
   flight).
5. **Cancellable.** `POST …/runs/{id}/cancel` asks the run to stop; it stops
   at its next step boundary or API call, stops the sprint it started, and
   finishes `cancelled`.
6. **Audited.** Start, cancel request and finish are each an audit row in the
   company's chain (`owner_action_started`, `owner_action_cancel_requested`,
   `owner_action_finished`), `agent_id` `human:<user id>`, with the run id,
   the action and the parameters; the finish row carries the outcome.
7. **Observations only.** A run stores what the platform created or measured:
   ids it got back from its own API (company, sprint, specification,
   architecture, agent runs), the daemon's replies (the checkout's path and
   commit, the sandbox each test run reported), the backend's clock (start,
   finish), the exit status the backend computed. No producer report is
   stored as a result. The asked sandbox and the observed one stay in
   separate fields (`sandbox.asked`, `sandbox.observed`), as the harness
   already keeps them.

## The catalogue

| Action | What it does | Cost | Records |
|---|---|---|---|
| `self_host_container_run` (#701, **built**) | The harness's steps `checkout, company, records, issue, wait, sandbox` with `--container-mode`. | Models: the PO's plan, then the sprint's Developer, QA and the others, at the company's models (`deepseek-v4-pro` today). Tokens: capped by the run's `token_budget` (default 2,000,000, at most 10,000,000). Duration: up to `timeout_seconds` for `wait` (default 3,600, at most 21,600), plus the copy (seconds to a minute). | Start and finish times, the checkout's path and commits, the company, specification, architecture and sprint ids, the agent run ids, the sandbox report (asked, observed, `container_mode`, modes, images), the tokens observed, the exit status. |
| `land_branch` (designed) | `scripts/land.sh <branch>` on the daemon's computer, through the daemon. | No model. The checks of `check.sh full` (tens of minutes, CPU on the daemon's computer). | land.sh's verification record (its note), `check.sh full`'s summary, `main` before and after. |
| `push_main` (designed) | Mirrors local `main` and land.sh's notes to `origin`, as `land.sh --push`. Fast-forward only. | No model, no tokens; a network push from the daemon's computer. | The pushed range (`origin/main` before, `main` after), the notes ref pushed. |
| `close_issue` (designed) | Posts one comment on a GitHub issue and closes it. | No model; two GitHub API calls. | The issue URL, the comment URL, the observed fix commit and whether `origin` had it. |

### `self_host_container_run` (built)

**Parameters** (all validated, nothing else accepted):

| Field | Type | Rule |
|---|---|---|
| `checkout_name` | string | One folder name, `^[a-z0-9][a-z0-9._-]{0,62}$`, not `.`/`..`. The daemon puts it under **its own** root, `FABRIQUE_SELF_HOST_ROOT` (default `~/fabrique-self-host`). It is never a path: the backend cannot name the live checkout or any folder outside that root. |
| `scope` | `S` or `M` | The PO's plan size. |
| `timeout_seconds` | integer 60 to 21,600 | `wait`'s deadline. |
| `token_budget` | integer 10,000 to 10,000,000 | The cap on the tokens the platform records for the self-hosting company during the run. |
| `urgent` | boolean, default false | Mark the sprint urgent (`PUT /v1/sprints/{id}/urgent`), so the run window does not hold it. Only when the owner ticks it. |

**How it runs without a person's token.** The backend runs the job as a
background task on its own event loop. The harness's step functions are
imported from `scripts/self_host/` (`SELF_HOST_REPOSITORY_DIR`, empty: the
backend's own checkout, else `AGENT_WORKSPACE_ROOT`, where the compose file
mounts the repository) and called with an **in-process API client**
(`services/management/in_process_api.py`): each call is an ASGI request to
the running application, never a socket. The request's ASGI scope carries an
`InProcessPrincipal` (the Owner's user id and the run id,
`services/platform/in_process_principal.py`); `auth_deps` resolves it to that
user only while the run is live. No token is minted or stored, no secret
leaves the process, and an HTTP client cannot put an object into an ASGI
scope (uvicorn builds the scope from the socket; headers are bytes). Every
route the harness calls checks the Owner's own roles as it would for her
browser, and audits as her.

**How `checkout` respects the daemon boundary.** The backend never reads or
writes the user's files. A new daemon subject, `workspace.self_host.checkout`
(request `{"name"}`), makes the snapshot on the daemon's computer: it copies
the files git tracks or would track in **the daemon's configured source**
(`FABRIQUE_SELF_HOST_SOURCE`, default the La Fabrique repository the daemon
runs from) into `<root>/<name>`, and commits them as `main` of a fresh
repository, as the harness's `checkout` step does. It refuses a name with a
separator or `..`, a target that resolves outside the root (a symlinked
root or folder), the source itself or a folder inside it, and a non-empty
folder that is not an earlier snapshot; a symlink in the source is not
followed out of it. It answers the path, both commits and the file count it
observed. Why a new subject: `workspace.generate` would make the backend read
the source to send its bytes (crossing the boundary the other way, thousands
of files), and `workspace.execute` would be a free command (`git clone …`),
which rule 1 forbids. The broker's permissions already give the backend
`workspace.>` to publish and the daemon `workspace.>` to hear, so
`scripts/nats_config.py` is unchanged (`--check` passes); the daemon announces
the subject in its abilities and the backend asks only a daemon that does.

The `records` step compared the architecture document with the snapshot's
files and asked `git status` directly. In the backend it reads the files
through `workspace.git.read_at` (the snapshot's committed bytes, hashed by
the backend) and the status through `workspace.git.diff` view `status`
(`scripts/self_host/self_host.py` takes a `snapshot` reader; the CLI keeps
its local one). The other steps were API-only already.

**Budget.** Before each API call (at most every 15 s), the job sums the
`cost_tokens` the platform recorded in the self-hosting company's audit rows
since the run started (the proxy's usage, measured by the platform, never an
agent's report). At or above `token_budget` it stops the sprint
(`POST /v1/sprints/{id}/stop`) and finishes `failed` with
`reason: budget_exceeded` and the tokens observed. The cap is checked between
calls, so it can be passed by one model turn; the dialog says so.

**Exit status** (the harness's): 0, 1 an API error, 5 not a container-mode
run (the daemon did not report an isolated container for every test run). A
run with status 5 finishes `failed`; its sandbox report is kept.

### `land_branch` (designed, not built)

- **Branch.** Picked from the local branches the daemon lists for its source
  repository (a new read-only view, `workspace.self_host.branches`), never
  typed; the backend re-checks the chosen name is in the daemon's latest
  list and matches `git check-ref-format --branch`.
- **Preview** (from the daemon): the commits and the diffstat against
  `main`; the protected paths the branch touches, computed **by the backend**
  from the daemon's changed-file list with
  `services/contracts/protected_paths.py` (the list land.sh reads); whether
  `LAND_OWNER=1` is needed.
- **Step-up.** A protected land needs a fresh re-authentication in the same
  request: the Owner's password, checked with bcrypt and the login throttle
  (`login_throttle.py`), or for an SSO account a sign-in less than five
  minutes old (`auth_time`). Only then does the backend send
  `land_owner: true`; the daemon sets `LAND_OWNER=1` for that one process and
  never reads it from anything else.
- **Runs** `["bash", "scripts/land.sh", <branch>]` in the source repository,
  as an argument list, through a dedicated subject
  (`workspace.self_host.land`), with a timeout. Not `--push`.
- **Refuses** while any sprint of the company is active, or a
  `self_host_container_run` is running, and says which.
- **Records** land.sh's verification note, `check.sh full`'s summary and the
  new `main` commit, as the daemon observed them.
- Optional "and push": queues `push_main`, which has its own confirmation.

### `push_main` (designed, not built)

- **Preview** (from the daemon, after a fetch): the commits in
  `origin/main..main` that will become public, with the sentence "This
  repository is PUBLIC."
- **Runs** on the daemon with the daemon machine's own git credentials
  (`workspace.self_host.push`: `git push origin main` and the notes ref,
  never `--force`, never `+refspec`). The daemon refuses unless
  `origin/main` is an ancestor of `main` after the fetch (not a
  fast-forward). No credential crosses the backend or the bus.
- **Records** the pushed range and the notes ref, read back from the
  remote-tracking refs after the push.

### `close_issue` (designed, not built)

- **Issue.** Picked from the open issues the backend lists with its
  configured `GITHUB_TOKEN`, through the reviewed adapter
  `services/producers/github_adapter.py` (extended with `close_issue` and
  `comment`; the `http_adapters` list does not grow).
- **Comment.** Prefilled from the action's observations: the commit that
  fixed it (from the land's record) and whether `origin` has it (from the
  daemon's remote-tracking ref). The Owner edits the text in the dialog; the
  confirmation digest covers the exact text, so what she read is what is
  posted.
- **Refuses** when the fix commit is not on `origin` yet, unless she ticks an
  explicit override (recorded as such in the audit row).
- **Records** the issue URL, the comment URL and the close.

## Endpoints

| Route | Who | What |
|---|---|---|
| `GET /v1/owner_actions?company_id=` | Owner | The catalogue (each action's parameters, cost, effect, records, whether it is available and why not) and its last runs. |
| `POST /v1/owner_actions/{name}/preview` | Owner | The confirmation: effect, cost, `confirmation` digest. Writes nothing. |
| `POST /v1/owner_actions/{name}/runs` | Owner | Starts a run (body validated, digest echoed); 409 while one runs. |
| `GET /v1/owner_actions/{name}/runs/{id}?company_id=` | Owner | A run: status, times, parameters, observations. |
| `POST /v1/owner_actions/{name}/runs/{id}/cancel` | Owner | Asks it to stop. |

`GET /v1/inbox` lists the catalogue's actions under `owner_actions` for an
Owner (not counted in `total`: a thing she can do, not a decision waiting),
and the page links each to the dialog.

## Threat model

### What is built

An Owner starts, from the browser, a background job in the backend that
drives the platform's own API in-process and asks the daemon to copy a
repository into a folder on its computer. The run creates a company and a
sprint (model spend) and writes a folder on the daemon's computer. The three
designed actions add a land (a script run on the daemon's computer that
fast-forwards `main`), a push to a public GitHub repository and a comment on
GitHub.

### Assets

The user's files and the live checkout (B3); the public repository's `main`
(B6); the audit chain and the run's observations (the product's evidence);
model budget; the daemon's machine (CPU, disk); the Owner's identity
(an action acts as her).

### Data flow and trust boundaries

```
 Browser ─B1─ Backend router (Owner, human) ─┐
                                             │ asyncio task (same process)
                      in-process ASGI ◄──────┤  InProcessPrincipal in scope
                      (routes as the Owner)  │
                                             ├─B2─ NATS ─B2─ daemon ─B3─ <root>/<name> (copy of source)
                                             │                    └─B3─ land.sh / git push (designed) ─B6─ GitHub
                                             ├─B4─ (through the sprint) LLM proxy ─ providers
                                             └─B6─ GitHub API (close_issue, designed)
 New boundary B15: the in-process principal. Code in the backend process
 can act as the Owner without a credential; it is a boundary between the
 owner-action job and every route.
```

### Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| O1 | B1 | E | A Manager, a Viewer or an API token starts an action | M | H | High | New: Owner check on every route, API tokens refused, signed-in human only, also without `AUTH_REQUIRED`; tests `test_owner_actions_router.py` (403 for Manager and for an Owner's token). Declared in `authorization.json`. |
| O2 | B1 | T | The dialog shows one thing and another runs (parameters swapped after the preview, a CSRF) | L | H | Medium | New: the start must echo the preview's digest over action, parameters and effect text; cookie requests need the CSRF header (existing, `session_cookies`). Test: changed parameters are a 409. |
| O3 | B15 | S | An external request forges the in-process principal | L | H | High | New: the principal is a Python object in the ASGI scope, which an HTTP server builds from the socket; headers cannot create it. `auth_deps` accepts only an `InProcessPrincipal` instance. Test: a request with look-alike headers is 401. |
| O4 | B15 | E | The job keeps acting as the Owner after cancel, or after she lost the Owner role | M | M | Medium | New: the principal is live only while the run is running and not cancelled; each call re-checks; every route re-checks her current role (existing). Test: a call after cancel is refused. |
| O5 | B2/B3 | T/I | The checkout name escapes the root (`..`, `/`, a symlink) or names the live checkout, so the copy overwrites or exposes the user's files | M | H | Critical | New: the backend accepts only a single folder name (422 otherwise); the daemon resolves `<root>/<name>`, refuses anything outside the root, the source or inside it, a non-empty non-snapshot folder, and does not follow symlinks out of the source. Tests on both sides. |
| O6 | B2 | S | Someone on the bus publishes `workspace.self_host.checkout` | L | M | Medium | Existing: only the backend's NATS user may publish requests; the daemon may not (`scripts/nats_config.py`, `test_nats_security_config.py`). The request names no source and no path, so even a forged one can only make a snapshot under the root. |
| O7 | B3 | I | The snapshot copies secrets | M | M | Medium | The copy takes what git tracks or would track (`--exclude-standard`), as the CLI does, so ignored `.env` files stay out; symlinks out of the source are skipped. Residual: a secret committed to the repository is copied, as by the CLI. |
| O8 | B4 | D | The run spends without bound | M | M | Medium | New: `token_budget` checked against the platform's recorded usage; the sprint is stopped and the run fails `budget_exceeded`; `timeout_seconds` caps `wait`. Test: a fake usage over the cap stops the run. Residual: one model turn can pass the cap. |
| O9 | — | D | Runs pile up and fight over the daemon | M | L | Low | New: one running run per action, 409 otherwise; conflicting actions refuse each other (designed for land). Test: a second start is a 409. |
| O10 | — | R | The Owner denies starting a run, or a run's results are edited | L | M | Medium | New: start, cancel and finish are rows in the company's hash chain, attributed to `human:<id>`; the run row keeps the observations the job wrote; the finish row carries the outcome. Test: the three rows are written and the chain verifies. |
| O11 | B5 | T | A producer's claim lands in the run's results (an agent saying its tests passed in a container) | M | H | High | Existing + new: the sandbox label is read from the daemon's replies recorded by the Verifier (`sandbox_mode.applied`), asked and observed kept apart; the job stores ids the API returned, never model text. Test: the stored report keeps `asked` and `observed` apart. |
| O12 | B1 | I | Run results leak to another company | L | M | Low | New: every run route is company-scoped and Owner-checked; a run of another company is 404. |
| L1 | B3 | E | `land_branch` lands a protected path with `LAND_OWNER=1` on a stolen session | L | H | High | Designed: step-up re-authentication (password with the login throttle, or an SSO sign-in under five minutes) before the backend sends `land_owner`; the daemon sets the variable for that process only. Test planned: a protected land without step-up is refused. |
| L2 | B3 | E | A branch name injects into land.sh | L | H | High | Designed: picked from the daemon's own list, re-checked against it and `check-ref-format`; passed as one argv element, never through a shell. |
| L3 | B3 | D | A land while a sprint writes to the repository | M | M | Medium | Designed: refused while a sprint is active or a self-hosting run is in flight. |
| P1 | B6 | T | `push_main` rewrites public history | L | H | High | Designed: never `--force` or a `+` refspec; the daemon refuses unless `origin/main` is an ancestor of `main` after a fetch. Test planned: a non-fast-forward push is refused. |
| P2 | B6 | I | Credentials cross the backend | L | H | High | Designed: the daemon's own git credentials; nothing in the request or reply carries one (`git_env.py`, no prompt). |
| P3 | B6 | I | Unreviewed commits become public by mistake | M | M | Medium | Designed: the preview lists every commit of `origin/main..main` and says the repository is public; its own confirmation, separate from the land. |
| C1 | B6 | T | The comment posted differs from what she read | L | M | Medium | Designed: the digest covers the exact text; the adapter posts the stored text. Test planned: the text sent equals the confirmed text. |
| C2 | B6 | T | An issue is closed before its fix is public | M | M | Medium | Designed: refused unless `origin` has the fix commit (observed by the daemon), or she ticks an explicit, audited override. Test planned. |

Assume breach: if the Owner check failed, the job still runs only catalogue
code with typed parameters, the daemon still confines the checkout to its
root, and every API route the job calls checks roles again. If the daemon's
root check failed, the backend still sends only a folder name. If the
budget check failed, the sprint's own per-agent budgets and the run's
timeout still apply. No step lets a model decide anything about the run.

### Tests that prove the mitigations (first slice)

`backend/tests/test_owner_actions_router.py`: Manager and Owner API token
403; escaping names 422; a changed digest 409; a second start 409; cancel;
the three audit rows on a verifying chain; the inbox lists the action.
`backend/tests/test_owner_action_runner.py`: a run against a faked API and
daemon records the sprint id, the agent run ids and the sandbox report as
observations, marks the sprint urgent only when asked, stops on cancel, and
stops the sprint and fails `budget_exceeded` over the cap; the in-process
principal is honoured only while live and never from headers.
`vscode-daemon/tests/test_self_host_checkout.py`: the copy, and refusals for
`..`, a separator, a symlinked target, the source, a non-empty folder.
`frontend/src/pages/__tests__/OwnerActionsPage.test.tsx`: the page and the
confirmation dialog.

### Residual risks

- One model turn can pass `token_budget` (checked between calls).
- A run started from the app acts with the Owner's full rights in the
  companies she owns, as her CLI token did; it can only run catalogue code.
- A secret committed to the repository is copied into the snapshot.
- The daemon's copy cannot be interrupted mid-way; cancel takes effect after.

### Open questions for the owner

1. Is `~/fabrique-self-host` the right default root, or should the root be
   unset (action unavailable) until she sets it?
2. Default `token_budget` 2,000,000 and `timeout_seconds` 3,600: right?
3. Step-up for SSO accounts: a sign-in under five minutes, or always the
   password of a break-glass account?

## What is built

- Built: the catalogue and its routes, the in-process client and principal,
  `self_host_container_run`, the daemon subject, the inbox entry, the page.
- Not built (designed above): `land_branch`, `push_main`, `close_issue`.

## Sources

The background run with a status page and a cancel button follows GitHub
Copilot coding agent's and Codex cloud's task pages (design only, no code
taken). The step-up before a dangerous action follows the GitHub "sudo mode"
re-authentication (design only).

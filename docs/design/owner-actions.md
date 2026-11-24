# Owner actions from the app: a design and its threat model

> **Status: proposed, 2026-11-24, for the owner's review.** Issue #701. Built:
> the catalogue, its endpoints, the inbox entry and the page, with four
> actions: `self_host_container_run`, then `land_branch`, `push_main` and
> `close_issue` (the second slice; where it departs from the first design,
> "Deviations in the second slice" says what and why). Every rule of
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
| `land_branch` (built) | `scripts/land.sh <branch>` on the daemon's computer, through the daemon. | No model. The checks of `check.sh full` (tens of minutes, CPU on the daemon's computer). | land.sh's verification record (its note), `check.sh full`'s summary, `main` before and after. |
| `push_main` (built) | Mirrors local `main` and land.sh's notes to `origin`, as `land.sh --push`. Fast-forward only. | No model, no tokens; a network push from the daemon's computer. | The pushed range (`origin/main` before, `main` after), the notes ref pushed. |
| `close_issue` (built) | Posts one comment on a GitHub issue and closes it. | No model; two GitHub API calls. | The issue URL, the comment URL, the observed fix commit and whether `origin` had it. |

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

### How the three repository actions observe first

`land_branch`, `push_main` and `close_issue` have **facts**: what the
platform observes before the confirmation (`owner_git_actions.py`). The
preview reads them, shows them, and the digest covers them with the
parameters and the effect. The start reads them again: a branch that moved,
a `main` or `origin/main` that moved, or a comment other than the one
previewed gives another digest, a 409. **Blockers** (not part of the digest)
refuse a start and keep the dialog's Confirm disabled: `nothing_to_land`,
`not_fast_forward`, `nothing_to_push`, `no_verification_record`,
`fix_not_on_origin`. The run keeps the facts confirmed as its `confirmed`
observation.

The daemon serves them on a read-only subject, `workspace.self_host.git`,
with a `view` (PROTOCOL.md §5.11): `branches`, `land_preview`, `origin`,
`push_preview` (after a fetch of origin's `main`) and `fix_commit` (after
that fetch). The repository is always the daemon's own
`FABRIQUE_SELF_HOST_SOURCE`, never one the request names. The broker's
`workspace.>` permissions already cover the three new subjects, so
`scripts/nats_config.py` is unchanged (`--check` passes).

`GET /v1/owner_actions/{name}/options` gives what the Owner picks from: the
daemon's branches for a land, the open issues of the repository `origin`
names for a close. Nothing is typed.

### `land_branch` (built)

- **Branch.** Picked from the daemon's `branches` view; the parameter must
  match `^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$`, the backend re-checks it is in
  the daemon's latest list, and the daemon checks again (no `..`, not
  `main`, `git check-ref-format --branch`, listed) and refuses a head other
  than the one the preview showed (409).
- **Preview.** The commits of `main..branch` (50 shown, the count kept), the
  diffstat of `main...branch`, and the protected paths: computed **by the
  backend** with `contracts/protected_paths.py` from the daemon's changed
  files and the test map the daemon read from `main:docs/architecture/contexts.json`
  (land.sh's inputs). `needs_owner` is true when a path matches, or when the
  test map could not be read (land.sh then refuses without `LAND_OWNER=1`).
- **Step-up** (`backend/app/step_up.py`). When `needs_owner`, the start
  request carries `step_up_password`: checked with bcrypt and the sign-in's
  per-account backoff (`login_throttle`: locked is 429 before the check, a
  failure counts). An SSO-only account (no password) passes when the
  request's session is an SSO sign-in under five minutes old (the session
  family's `created_at`; a refresh keeps the family). Missing or wrong: 403
  `step_up_required` / `step_up_failed` / `step_up_sso_stale`, and no run is
  recorded. The password is never stored or audited; the start's audit row
  says which proof was given (`step_up: password`). Only then does the job
  send `land_owner: true`.
- **Runs** `["bash", "scripts/land.sh", <branch>]` in the source repository
  through `workspace.self_host.land`, never through a shell, never `--push`.
  The daemon drops every `LAND_*` variable of its own environment, sets
  `LAND_OWNER=1` for that process only when `land_owner` is `true`, defaults
  `PYTHON_KEYRING_BACKEND` to the null keyring (the proxy's Poetry install in
  `check.sh full` would otherwise wait on a desktop keyring), runs without a
  git prompt (`git_env`), and kills the process group after
  `FABRIQUE_SELF_HOST_LAND_TIMEOUT` (5,400 s by default; the backend waits
  6,000 s).
- **Refuses** while any sprint in the deployment is active (409 with their
  ids), or while a `self_host_container_run` or a `push_main` runs (the
  catalogue's conflicts, both ways).
- **Records** what the daemon observed: the exit code and its meaning
  (`landed`, `checks_failed_or_conflict`, `usage_or_environment`, `refused`,
  `locked`), `main_before`, `main_after`, the output's tail; and land.sh's
  verification record of the new `main` as land.sh wrote it, under
  `verification_record` (it says of itself that it is a claim), with
  `check_summary` (status, exit code, duration, steps) taken from it. A
  land succeeds only when land.sh exited 0 and `main` moved.

### `push_main` (built)

- **Preview** (after the daemon's fetch): the commits of `origin/main..main`
  that will become public, the effect's first line "The repository is
  PUBLIC", and the dialog's alert; blocked unless a fast-forward, with
  something to push and a verification record of `main`.
- **Runs** on the daemon with the daemon machine's own git credentials
  (`workspace.self_host.push`): it fetches again, refuses (409) unless `main`
  and `origin/main` are the commits the preview showed and `origin/main` is
  an ancestor of `main`, then `git push --atomic origin
  <previewed main>:refs/heads/main refs/notes/verification:refs/notes/verification`.
  Never `--force`, never a `+` refspec (asserted in the code). No credential
  crosses the backend or the bus; nothing prompts.
- **Records** the pushed range (`origin/main` before, and after as read back
  by a fetch), the refspecs and the notes ref.

### `close_issue` (built)

- **Issue.** Picked from the open issues of the repository the daemon's
  `origin` names (`github_adapter.repository_of`; a remote that is not
  github.com makes the action refuse), listed with the backend's
  `GITHUB_TOKEN` through the reviewed adapter
  `services/producers/github_adapter.py`, extended with `list_open_issues`,
  `comment_on_issue` and `close_issue`. The `http_adapters` list does not
  grow. Without `GITHUB_TOKEN` the action is unavailable.
- **Fix.** The daemon's `fix_commit` view: the commits on `main` whose message
  names `#<n>`, each with whether `origin/main` contains it (after a fetch).
  The newest is the default; the Owner may name another of them, never any
  other commit.
- **Comment.** Prefilled from those observations ("Fixed by <sha> (<subject>).
  It is on origin/main." or "…not on origin/main yet…"). The Owner edits it
  in the dialog; an edit must be previewed again before Confirm is enabled,
  so the digest covers the exact text, and the job posts the run's stored
  text unchanged. It records the text sent and the text GitHub stored.
- **Refuses** when the fix is not on `origin` (or none was found) unless she
  ticks the override, which is a parameter, so it is in the audit row and in
  the digest.
- **Records** the issue URL, the comment URL, the texts and the close. A
  close GitHub refuses after the comment was posted finishes `failed` with
  the comment's URL kept.

### Deviations in the second slice

1. **One read-only subject with views** (`workspace.self_host.git`) instead
   of `workspace.self_host.branches` alone: the previews of all three need
   the daemon's reads (the land's diff, the push's commits, the fixing
   commit), and one read-only subject with a fixed set of views keeps the
   write subjects (`land`, `push`) apart and few.
2. **The protected list is the backend's copy**, with main's test map; land.sh
   reads both from `main`. When they differ, land.sh still decides: a path
   only land.sh protects makes it refuse (exit 3) because `LAND_OWNER` was
   not sent, which fails safe. The preview also compares with local `main`,
   where land.sh merges with `origin/main` when that is ahead.
3. **The push does not call `land.sh --push`.** The daemon pushes the
   previewed commit by its sha (`<sha>:refs/heads/main`), so what becomes
   public is exactly what the dialog listed even if `main` moves meanwhile
   (P3). The refspecs, `--atomic`, no force and the record check are
   land.sh's.
4. **The fixing commit comes from `git log --grep '#<n>'`** on `main`, not
   from a land's record: a land's record does not name issues, and a commit
   message does. The Owner sees the candidates.
5. **The SSO step-up reads the session family's `created_at`**, since the
   access token carries no `auth_time`.
6. **"And push" after a land is not offered.** The push is its own action,
   reviewed with its own preview.
7. **A land waits for every active sprint in the deployment**, not only the
   company's: the daemon's source repository is shared by every company
   that works on it.
8. **The issue must be among the newest 100 open issues** (one page of the
   list). An older one is a 422.
9. **Cancel** of a repository action is honoured before its one call; once
   the daemon runs land.sh, the run finishes with what happened (as for the
   checkout's copy).

## Endpoints

| Route | Who | What |
|---|---|---|
| `GET /v1/owner_actions?company_id=` | Owner | The catalogue (each action's parameters, cost, effect, records, whether it is available and why not) and its last runs. |
| `POST /v1/owner_actions/{name}/preview` | Owner | The confirmation: effect, cost, `confirmation` digest. Writes nothing. |
| `POST /v1/owner_actions/{name}/runs` | Owner | Starts a run (body validated, digest echoed); 409 while one runs. |
| `GET /v1/owner_actions/{name}/runs/{id}?company_id=` | Owner | A run: status, times, parameters, observations. |
| `POST /v1/owner_actions/{name}/runs/{id}/cancel` | Owner | Asks it to stop. |
| `GET /v1/owner_actions/{name}/options?company_id=` | Owner | What the Owner picks from: the daemon's branches (`land_branch`), the open issues (`close_issue`). |

`GET /v1/inbox` lists the catalogue's actions under `owner_actions` for an
Owner (not counted in `total`: a thing she can do, not a decision waiting),
and the page links each to the dialog.

## Threat model

### What is built

An Owner starts, from the browser, a background job in the backend that
drives the platform's own API in-process and asks the daemon to copy a
repository into a folder on its computer. The run creates a company and a
sprint (model spend) and writes a folder on the daemon's computer. The three
repository actions add a land (a script run on the daemon's computer that
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
                                             │                    └─B3─ land.sh / git push ─B6─ GitHub
                                             ├─B4─ (through the sprint) LLM proxy ─ providers
                                             └─B6─ GitHub API (close_issue)
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
| O9 | — | D | Runs pile up and fight over the daemon | M | L | Low | New: one running run per action, 409 otherwise; conflicting actions refuse each other (a land, a push and a self-hosting run). Test: a second start is a 409. |
| O10 | — | R | The Owner denies starting a run, or a run's results are edited | L | M | Medium | New: start, cancel and finish are rows in the company's hash chain, attributed to `human:<id>`; the run row keeps the observations the job wrote; the finish row carries the outcome. Test: the three rows are written and the chain verifies. |
| O11 | B5 | T | A producer's claim lands in the run's results (an agent saying its tests passed in a container) | M | H | High | Existing + new: the sandbox label is read from the daemon's replies recorded by the Verifier (`sandbox_mode.applied`), asked and observed kept apart; the job stores ids the API returned, never model text. Test: the stored report keeps `asked` and `observed` apart. |
| O12 | B1 | I | Run results leak to another company | L | M | Low | New: every run route is company-scoped and Owner-checked; a run of another company is 404. |
| L1 | B3 | E | `land_branch` lands a protected path with `LAND_OWNER=1` on a stolen session | L | H | High | Built: step-up in the start request (password with the login backoff, or an SSO sign-in under five minutes) before the job sends `land_owner`; the daemon drops its own `LAND_*` and sets `LAND_OWNER=1` for that process only. Tests: `test_owner_git_actions_router.py` (no password and a wrong one are 403, no run recorded; the right one starts with `land_owner`), `test_owner_git_runner.py` (sent only for a protected land with the step-up), `vscode-daemon/tests/test_self_host_git.py` (the environment). |
| L2 | B3 | E | A branch name injects into land.sh | L | H | High | Built: picked from the daemon's list, pattern-checked by the backend, re-checked against the list by both sides and by `check-ref-format`; one argv element, never a shell. Tests: a branch not listed, `../x`, `-x`, `a b` are refused on both sides. |
| L3 | B3 | D | A land while a sprint writes to the repository | M | M | Medium | Built: refused while any sprint is active, or a self-hosting run or a push runs (409). Test: `test_a_land_is_refused_while_a_sprint_is_active_or_a_self_hosting_run_runs`. |
| P1 | B6 | T | `push_main` rewrites public history | L | H | High | Built: never `--force` or a `+` refspec; the backend blocks and the daemon refuses unless `origin/main` is an ancestor of `main` after a fetch. Tests: the route's 409 (no push requested) and the daemon's refusal before `git push` against a bare repository whose `main` moved. |
| P2 | B6 | I | Credentials cross the backend | L | H | High | Built: the daemon's own git credentials; the request carries two shas, the reply the remote URL without credentials (`git_env.py`, no prompt). |
| P3 | B6 | I | Unreviewed commits become public by mistake | M | M | Medium | Built: the preview lists every commit of `origin/main..main` under a PUBLIC alert; the daemon pushes the previewed sha, so a later commit is never pushed with it. Test: the dialog shows the warning and the commits. |
| C1 | B6 | T | The comment posted differs from what she read | L | M | Medium | Built: the digest covers the exact text, an edit is previewed again, and the job posts the run's stored text. Tests: another text with the first digest is a 409; the text the fake GitHub received equals the confirmed one, whitespace included. |
| C2 | B6 | T | An issue is closed before its fix is public | M | M | Medium | Built: blocked unless `origin` has the fix commit (observed by the daemon after a fetch), or she ticks the override, a parameter in the audit row. Test: refused without, started with it. |

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
`frontend/src/pages/OwnerActions.test.tsx`: the page and the confirmation
dialog.

### Tests that prove the mitigations (second slice)

`backend/tests/test_owner_git_actions_router.py`: Manager and API token 403
on preview, start and options of each; a branch not listed 422; a protected
land's step-up (none, wrong, right; the password absent from the audit); an
unprotected land never sets `land_owner`; a land refused during an active
sprint and during a self-hosting run; a moved branch and a forged digest
409; a non-fast-forward push 409; a close refused without the override and
started with it; an edited comment needs its own digest.
`backend/tests/test_owner_git_runner.py`: `land_owner` sent only when both
needed and stepped up; a land records `main` before and after apart from
land.sh's record; the push's range; the exact text posted; a refused close
keeps the comment; cancel before the call sends nothing; the audit rows and
the chain; the adapter's issue calls (no network).
`vscode-daemon/tests/test_self_host_git.py`: branches and the land preview,
refused branch names, land.sh's argv and environment, a moved branch, a
landed branch's record, the push preview, the non-fast-forward refusal before
any push, the push's argv (recorded, not sent), the fixing commit.
`frontend/src/pages/OwnerRepoActions.test.tsx`: the protected land's
password, the push's PUBLIC warning and a blocked push, the edited comment
previewed again, the override.

### Residual risks

- One model turn can pass `token_budget` (checked between calls).
- A run started from the app acts with the Owner's full rights in the
  companies she owns, as her CLI token did; it can only run catalogue code.
- A secret committed to the repository is copied into the snapshot.
- The daemon's copy cannot be interrupted mid-way; cancel takes effect after.
  The same holds for a land once land.sh runs.
- `LAND_OWNER=1` is a guard against mistakes, not a security boundary (as
  land.sh says): whoever controls the daemon's computer can land anything.
  The step-up protects the path through the app, not the machine.
- The verification record is land.sh's claim about its own checks, stored as
  such; it is confirmed by `land.sh --verify` elsewhere or by CI after the
  push.
- An issue closed with the override can be closed before its fix is public;
  the audit row says so.

### Open questions for the owner

1. Is `~/fabrique-self-host` the right default root, or should the root be
   unset (action unavailable) until she sets it?
2. Default `token_budget` 2,000,000 and `timeout_seconds` 3,600: right?
3. Step-up for SSO accounts: a sign-in under five minutes (built), or always
   the password of a break-glass account?
4. A land waits for active sprints of every company (built): right, or only
   sprints whose project is the daemon's source?

## What is built

- First slice: the catalogue and its routes, the in-process client and
  principal, `self_host_container_run`, the daemon subject, the inbox entry,
  the page.
- Second slice: `land_branch`, `push_main`, `close_issue`, the options route,
  the step-up, the daemon subjects `workspace.self_host.git`, `.land` and
  `.push`, the GitHub adapter's issue calls, and their dialogs.

## Sources

The background run with a status page and a cancel button follows GitHub
Copilot coding agent's and Codex cloud's task pages (design only, no code
taken). The step-up before a dangerous action follows the GitHub "sudo mode"
re-authentication (design only). The previewed, fast-forward-only push of a
named commit follows Codex cloud's and Copilot coding agent's practice of
showing the exact diff before anything leaves the sandbox (design only, no
code taken).

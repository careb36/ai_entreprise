# What the backend offers that the frontend does not show

An audit of the HTTP routes the backend serves against the routes the
frontend calls, made on 2026-11-19 from `main` at `a619dd5`. It lists the
endpoints a person (a Viewer, a Manager or an Owner) should be able to use
from the web application but cannot, ordered by value to a company that runs
the factory: security, governance and audit first, then daily work, then the
rest. The backend stays the authority for every role named here; the UI only
hides what a role cannot do.

## How it was made

- **Backend.** Every `@router.<verb>` in `backend/app/routers/**` with its
  router's prefix, and every router `backend/app/main.py` mounts (every
  module of `routers/` is mounted; `sprint_planning` through `sprint`). 298
  routes.
- **Frontend.** Every path named in `frontend/src` outside the tests (the
  network itself is only in `services/api/client.ts` and `services/api/stream.ts`,
  per the layer rule; the paths are built in the `services/` modules), including
  the ones built from a variable base (`${base(companyId)}/models`).
- **Roles.** Read from each route's `require_company_role(...)` or
  `owner_person(...)` call.
- **Open work.** The open pull requests at the time (#712 and #719, the
  bench) and the work named as in progress (#703 SSO group mapping, #731 the
  changelog, #729 the local landing, merged) are left out.
- **Documents.** `CHANGELOG.md` `[Unreleased]` and `docs/contexts/*.md` were
  read for a feature that names a UI the frontend does not have.

## The gaps

Priority: **P1** security, governance and audit; **P2** daily work; **P3**
nice to have. "PR" names the pull request that closes the gap.

### P1 — security, governance and audit

| Endpoint | Role | What is missing in the UI | PR |
|---|---|---|---|
| `GET /v1/auth/tokens?company_id=` | Viewer | The company's API tokens: name, role, scopes, who made it, when, when it expires, when last used, whether revoked. Only the access review shows tokens, and only to an Owner | #733 |
| `POST /v1/auth/tokens` | Manager (a token's role never above the creator's; a token never mints one) | Creating an API token, with its role and expiry, and the token shown once | #733 |
| `POST /v1/auth/tokens/{id}/revoke?company_id=` | Manager | Revoking a token at once, without completing an access review | #733 |
| `GET /v1/run-secrets?task_id=` | Viewer | A task's run secrets by name (the value is never returned) | #733 |
| `POST /v1/run-secrets` | Manager | Adding a secret a task's run may read (name and value; the value is write-only) | #733 |
| `DELETE /v1/run-secrets/{id}?company_id=` | Manager | Deleting a run secret | #733 |
| `GET /v1/audit/chain?company_id=` | Viewer | The company's audit chain walked end to end: whether it verifies, where it breaks, its head (sequence and hash) to record elsewhere. The Evidence page only checks one interaction (`/v1/audit/verify`) | #735 |
| `GET /v1/tasks/{id}/events` | Viewer | A task's event log in order, with its own hash chain's state and which record the board reads (`events` or `comments`) | #735 |
| `GET /v1/audit/anchors/statement?company_id=` | Viewer | The statement of the chain's head to sign with the company's own key (customer-key anchor, `docs/design/AUDIT_ANCHORS.md`) | #735 |
| `POST /v1/audit/anchors/signature?company_id=` | Viewer (verified against the keys an Owner registered) | Submitting the signature made outside the factory | #735 |
| `GET /v1/audit/anchors/{id}/export?company_id=` | Viewer | Downloading an anchor's receipt to verify it outside the factory | #735 |
| `GET/POST /v1/companies/{id}/security/wstg[/reviews]` | Viewer reads, Manager records | The project's OWASP WSTG plan, each test's status and a person's review of one test. The documentation pack shows the same rows read-only | #741 |
| `GET/PUT /v1/companies/{id}/sandbox`, `POST …/sandbox/cache/reset` | Viewer reads, Manager writes | Where agent-written code runs and which environment variables (by name) a sandboxed run sees; resetting the dependency cache | #741 |
| `GET/PUT /v1/companies/{id}/research` | Viewer reads, Manager writes | The hosts the research tools may read beyond the deployment's defaults (an egress allowlist) | #741 |
| `GET/POST/PATCH/DELETE /v1/mcp/servers`, `GET …/{id}/tools` | Viewer reads, Manager writes | The MCP servers agents may call, the roles each serves, enabling and removing one, its tools | #741 |
| `DELETE /v1/company/{id}/members/{user_id}` | Owner (the last Owner stays) | Removing a member at once (it ends their sessions). Today only completing an access review removes one; there is no member list outside it | #741 |
| `GET/DELETE /v1/companies/{id}/sso/identities[/{id}]` | Owner | The members' linked SSO identities, and unlinking one (it ends their SSO-started sessions). Built once #703 (SSO group mapping, same page; #738) landed | #744 |
| `GET /v1/companies/{id}/rsi/cycles[/{id}]`, `POST …/rsi/resume`, `…/cycles/import`, `…/cycles/{id}/merge` | Viewer reads; a signed-in Owner acts | The self-improvement loop's state and cycles; resuming after its stop; recording the person's merge; importing a runner's record. The Findings page shows cycles per finding, not the loop | #741 |

### P2 — daily work

| Endpoint | Role | What is missing in the UI | PR |
|---|---|---|---|
| `POST /v1/sprints/{id}/integrate` | Manager | Integrating a sprint now (the task worker does it when a sprint closes); the Overview shows the plan only | #737 |
| `POST /v1/sprints/{id}/integration/publish` | Manager (the company's forge policy must be `publish`) | Pushing the integration branch and opening the sprint's pull request | #737 |
| `POST /v1/sprints/{id}/integration/merge` | Manager (segregation of duties: not the person who started the sprint; the review gate when required) | Merging the integration locally | #737 |
| `GET/PUT /v1/companies/{id}/webhook`, `DELETE`, `POST …/ping` | Viewer reads, Manager writes | The notification webhook (URL, events) and a test ping | #742 |
| `GET/PUT /v1/companies/{id}/stack_profile` | Viewer reads, Manager writes | The project's stack profile the agents work to | #742 |
| `GET/PUT /v1/companies/{id}/team_selection` | Viewer reads, Manager writes | Whether a planned sprint's team is suggested or chosen automatically; the planner shows the outcome, not the setting | #742 |
| `GET /v1/companies/{id}/architecture/versions[/{version}]`, `POST …/architecture/revisions` | Viewer reads, Manager drafts | Opening the next architecture revision for an approved specification; the history panel lists versions from `GET …/architecture` | #742 |
| `GET /v1/architectures/{id}/adrs` | Viewer | The decisions as ADR files to download | #742 |
| `GET /v1/documentation/{pack_id}/manifest` | Viewer | Downloading a pack's signed manifest | #742 |
| `GET /v1/sprints/{id}/relations` | Viewer | The sprint's task relations as one view; a task's are shown on its page | #742 |

### P3 — nice to have

| Endpoint | Role | What is missing in the UI | PR |
|---|---|---|---|
| `GET /v1/sprints/{id}/events` | Viewer | The sprint's raw event list; the task event log above covers the need per task | #743 |
| `GET /v1/models/catalog/{provider}` | public | One provider's models and capabilities; the Models page reads the whole catalog | #743 |

## Left out on purpose

- **Machines, not people.** `POST /v1/agent_runs` (a producer registers its
  run), `POST /v1/audit` (an agent's interaction), `POST /v1/integrations/github/webhook`,
  `POST /v1/integrations/slack/{id}/interactions`, `POST /v1/integrations/teams/{id}/messages`,
  `GET /v1/auth/sso/{key}/start` and `GET /v1/auth/sso/callback` (the browser
  is sent there; the UI links to them), `/health`, `/health/deep`.
- **Removed.** `POST /v1/approve_task` answers 410; the approval is
  `POST /v1/agent_runs/{id}/approve`, which the UI uses.
- **Shown another way.** `GET /v1/manager_decisions` (the inbox carries the
  agent's decisions); `GET /v1/companies/{id}/sso/domains` (the domains come
  with `GET /v1/companies/{id}/sso`); `GET /v1/companies/{id}/architecture/versions[/{version}]`
  (the history panel lists every version from `GET /v1/companies/{id}/architecture`).
- **Owned by open work.** The bench's routes and views (#712, #719), the SSO
  group mapping (#703), the changelog (#731).

## What the documents promise

`CHANGELOG.md` `[Unreleased]` and `docs/contexts/*.md` describe no view that
the frontend lacks beyond the rows above: the features they name with a page
(the wiki, the findings, documentation, duties, the integration review,
audit anchors' coverage and settings) have one. The changelog's #524 entry
names member removal as new and Owner-only; it has no direct control yet (P1
above).

## Rules every pull request follows

The frontend layer rule (`scripts/check_frontend_architecture.py`), the
600-line ratchet (`scripts/check_file_sizes.py`), the design system's
primitives (`components/ui`), both locales, role gating as the backend
enforces it, a vitest test per new view, and these standards where a doubt
arises:

- **WCAG 2.2 AA**: every control labelled, reachable and operable by
  keyboard, status as icon and word, focus visible, a confirmation before a
  destructive action (3.3.4 Error Prevention), status messages announced (4.1.3).
- **OWASP ASVS 4.0.3**: a new secret is shown once and never stored by the
  browser (V2.10, V8.2.1: no `localStorage`, `autocomplete="off"` on the
  secret's field), a secret's value never read back (V6.4), a destructive or
  privileged action confirmed and only offered to the role the backend allows
  (V4.1.1, V4.1.3), and the audit records read from the server, never computed
  in the browser (V7.1).

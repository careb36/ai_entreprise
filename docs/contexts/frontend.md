# The frontend

The React 18 application under `frontend/` (Vite, TypeScript, Tailwind). Not a
backend context: it has its own layers and its own dependency rule. A page or
panel is described with the backend service it shows; this document is the map.

## The i18n rule

Every string a person reads or hears is in both `frontend/src/i18n/en.json` and
`frontend/src/i18n/es.json`, read with the `useTranslation` hook from i18next.
Never hardcode English in JSX ([`CLAUDE.md`](../../CLAUDE.md), Frontend i18n).
Three checks hold it:

- `npm run lint`: `eslint-plugin-i18next` fails on literal text in JSX and in
  attributes read aloud (`title`, `alt`, `placeholder`, `aria-label`…).
- `src/uiRules.test.ts` (rules in `src/test/uiRules.ts`): what the lint cannot
  see (words in template literals, a disabled i18next rule) and the
  accessibility basics: `alt` on images, a name on icon-only controls, a label
  on every input, a key handler on a clickable element. Its allowlist,
  `src/test/uiRulesBaseline.json`, only shrinks.
- `src/i18n/locales.test.ts` and `src/i18n/coverage.test.ts`: both files have
  the same keys, and every `t('…')` key exists in both.

The worker's live-view messages carry a `message_key` the Glassroom translates
(`lib/agentEvents.ts`); stored records keep the English text.

## The dependency rule between layers

`frontend/src` is divided into layers, listed from the bottom up in
`docs/architecture/frontend.json` and checked by
`scripts/check_frontend_architecture.py` (a step of `frontend-build.yml`; tests
in `backend/tests/test_check_frontend_architecture.py`).

| Layer | Paths | What it holds |
|---|---|---|
| foundation | `i18n/`, `styles/`, `electron.d.ts`, `vite-env.d.ts` | Translations, styles, ambient types; imports nothing of the app |
| services | `services/` | The backend's client (`services/api.ts` over `services/api/`, one module per domain), the query keys, the service hooks |
| lib | `lib/` | Pure helpers and constants |
| state | `store/`, `hooks/` | The zustand stores and the React Query hooks |
| components | `components/` | Reusable views and the parts of larger ones |
| pages | `pages/` | One view per route, and its parts |
| app | `App.tsx`, `main.tsx`, `app/` | The router, the entry point, the routes and the layout |

- **A module imports only from its own layer or below.** A part two pages share
  is a component; a type services and a view share lives in services.
- **The network belongs to services.** Only `network.modules`
  (`services/api/client.ts`, `services/api/stream.ts`) may use `fetch`,
  `XMLHttpRequest`, `EventSource`, `WebSocket` or `axios`.
- **Tests are exempt** from both, but no shipped module may import a test or the
  test support.
- **Every source file is in exactly one layer**: a new folder under `src` is
  added to `frontend.json` in the same change.
- The allowlist is empty and only shrinks (`--update` drops stale entries,
  never adds one).

## Rules

- **The session holds no credential in JavaScript.** The tokens are HttpOnly
  cookies; `useAuthStore` keeps only `user`, `memberships` and `expiresAt`.
  `services/api/client.ts` sends `X-Fabrique-CSRF` on unsafe methods and
  refreshes once per tab on a 401 ([`SESSIONS.md`](../archive/design/SESSIONS.md)).
- **Observed and claimed never mix.** What the platform observed and what an
  agent or producer reported are separate groups with their own heading:
  observed solid under a shield, claimed dashed under a speech mark. A claim is
  never styled as an observation, and a suggestion is never filled into a form.
- **Every verdict is the server's.** The UI shows the backend's decisions and
  refusals; it does not compute them.
- **Agent output is untrusted.** Markdown from agents (`WikiMarkdown`,
  `PackMarkdown`) renders without raw HTML or images and keeps only safe links;
  Mermaid source is shown as text, never run.
- **Status is an icon and a word, never colour alone.** Focus stays visible,
  the keyboard reaches everything the mouse does, and every animation stops
  under reduced motion.
- A secret or token is shown once, write-only, and never stored.
- Every source file stays at or under 600 lines (`scripts/check_file_sizes.py`).
  A view too large for one file keeps its entry file and puts its parts in a
  lower-camel-case folder beside it (`components/glassroom/`,
  `pages/companyNew/`, `pages/agentSettings/`); `hooks/useQueries.ts` and
  `services/api.ts` are barrels over one module per domain.

## Where views live

- **Shell:** `App.tsx` (the router's provider), `app/AppLayout.tsx` (the page
  around every route), `app/AppRoutes.tsx` (routes and legacy redirects),
  `components/shell/` (navigation bar, health pill, language switcher).
- **Design system:** `components/ui/` (`PageHeader`, `Toolbar`,
  `SegmentedFilter`, `Panel`, `CountPill`, `StatusBadge`, `EmptyState`,
  `AccessNotice`, loading and error states, form foot, table classes) and its
  classes in `styles/index.css`; tokens in `tailwind.config.js`. Restyle a page
  with these; add a primitive only when a second page needs it.
- **The sprint board:** `components/KanbanBoard.tsx` and `components/board/`;
  saved views and filters in `SprintTaskViews`. A card's signals keep
  **Observed** and **Reported** apart.
- **The sprint planner:** `components/QuickLaunch.tsx` (the plan preview),
  with `PlannerFallback` (a plan made without the model) and `AlreadyDoneNote`
  (what the PO says is already done, labelled as its claim, beside the files
  the platform saw it read, a claimed path it did not read flagged "not read").
- **The live view:** `components/GlassRoom.tsx` and `components/glassroom/`,
  over SSE (`services/api/stream.ts`).
- **The desktop app:** `frontend/electron/`, outside the layers. The packaged
  app serves `dist/` on `127.0.0.1` and forwards `/v1/*` to the backend, so the
  page and the API share an origin; the main window stays on that origin and
  other links open in the system browser.

## Statuses on the Live page and the board

Every status comes from a backend record; the event stream only animates it
(issue #766). `GET /v1/sprints/{id}/live_state` ([loop](loop.md#what-the-live-page-reads))
and the board's `observed.hold` ([tasks](tasks.md#what-holds-a-task-back)) are
the sources; the logic is in `components/glassroom/liveStatus.ts` and
`components/board/HoldBadge.tsx`.

**Live page** (`components/GlassRoom.tsx`, `components/glassroom/`):

| Status | Source | Definition |
|---|---|---|
| working (sidebar spinner, typing row, roster, "N working") | `live_state.working` | A run in flight for that agent now: an assignee's run of a claimed task or QA's review. Empty unless the sprint is `active` or `needs_split`. |
| a message group's badge | the group's messages | Its latest message that says a state: approved, rework requested, blocked, delivered, error, update; a start reads "working" only while the agent is in `working`, else "started". |
| Agents | the feed's authors | Each company role once (aliases folded: `Dev` is Developer, `Security` is SecOps); the system, the Verifier, the quality gate and a person are not agents. An unknown name shows as itself. |
| feed order | each event's `timestamp` | Events are placed by their own time; after `closed_at` a separator says "After the sprint closed". |
| sprint status | board `status`, `integration_state`, `pause_kind` | `SprintStatusBadge` (a completed sprint still integrating shows integrating); a paused one says why. |
| Agents with a run in flight | `tiles.active_agents` | Distinct roles in `working`. |
| Failed agent calls | `tiles.agent_failures` | The sprint's `review_failed` and `retried` task events. |
| Blocked tasks | `tiles.blocked_tasks` | Tasks whose status is `blocked`. |
| Tasks with an unanswered rework request | `tiles.open_reworks` | Tasks not done or discarded with a rework request no delivery answered. |
| Last event an agent recorded | `tiles.last_agent_event_at` | The newest task event whose actor is a role (not the system, a gate or a person). It is not a heartbeat. |
| Workspace daemon heartbeat | `tiles.daemon` | The daemon's liveness as the backend observed its heartbeats: online, late, offline or none seen, and how long ago. |
| Memory retrievals seen on this stream | the stream | `*.memory_retrieved` events received since the page opened, each once; no record keeps them. |
| Previous sprint (name) | `tiles.previous_sprint` | The company's latest completed sprint before this one: done of total. |

**Board** (`components/KanbanBoard.tsx`, `components/board/`, `SprintControls`):

| Status | Source | Definition |
|---|---|---|
| column, status select | `status` | The task's status. |
| hold chip | `observed.hold.kind` | `person` waits for a person; `dependency` waits for the tasks listed (`#30`); `retry_pending` and `review_pending` the worker runs on its own; `frozen`; `risk_hold` waits for a person to accept the risk; `run_window` ready, held until the window opens. |
| Needs a person | `observed.waits_for_person` | Only a person releases the hold (person, risk hold, frozen); a dependency wait is not one. |
| left edge | `status` and the hold | Red for blocked, amber for blocked by a dependency, sky for a pending retry or review. |
| paused banner | `pause_kind`, `pause_reason` | The kind's title and what to do, then the reason as recorded. |
| `x / y done`, blocked, rework requests (Sprints list) | `GET /v1/sprints` | Done and total tasks, tasks blocked now, and every rework request made over the sprint. |

`reassigned_from` is not a record field: the planner writes the reassignment
into the task's description, which the task page shows.

## Routes and the page that serves each

The routes are in `app/AppRoutes.tsx`; every page is in `pages/` unless a
component is named.

| Route | Page |
|---|---|
| `/login`, `/register`, `/login/sso` | `Login`, `Register`, `SsoComplete` (frame: `pages/auth/AuthCard`) |
| `/` | redirects to `/evidence` |
| `/evidence[/:runId]` | `Evidence`, `EvidenceDetail` (a run, its verdict and checks) |
| `/sprints`, `/sprints/:id/:tab?` | `Sprints`, `SprintWorkspace` (its tabs; the Overview through `Dashboard`) |
| `/sprints/:sprintId/tasks/:taskId`, `/task/:taskId` | `TaskDetails` (run secrets, event log, edit task) |
| `/dashboard`, `/analytics`, `/workspace/…`, `/sprint-ops/:id` | redirect to the active sprint's tab |
| `/companies/new`, `/companies/select` | `CompanyNew` (the wizard, or start from an exported file), `CompanySelect` |
| `/invitations`, `/account`, `/notifications` | `Invitations`, `Account`, `MyNotifications` |
| `/company/members`, `/company/teams`, `/company/duties` | `CompanyMembers`, `CompanyTeams`, `CompanyDuties` |
| `/company/sso`, `/company/chat`, `/company/github` | `CompanySso`, `ChatApprovals`, `GitHubIntegration` |
| `/company/usage`, `/company/quotas`, `/company/models`, `/company/model-catalog` | `Usage`, `components/QuotaPanel`, `Models`, `ModelCatalog` |
| `/company/project`, `/company/execution`, `/company/mcp` | `ProjectSettings` (with the Owner's company export), `ExecutionSettings`, `McpServers` |
| `/company/routines` | `Routines` (with `pages/routines/`): the company's scheduled tasks, run now with values, revisions and restore |
| `/company/owner-actions` | `OwnerActions` (with `pages/ownerActions/`): the Owner's catalogue of actions, the confirmation dialog with cost and effect (`ConfirmDialog`, which sends back the preview's digest), a run's live status, cancel, and the ids it recorded with a copy button (`RunPanel`). `land_branch`, `push_main` and `close_issue` use `RepoActionCard` (a branch or an issue picked from the backend's options, never typed) and `RepoConfirmDialog` (the commits, diffstat and protected paths, the password of a protected land, the PUBLIC warning of a push, the editable comment that is previewed again before Confirm, and the blockers that keep Confirm disabled); [design](../design/owner-actions.md) |
| `/company/notifications`, `/company/api-tokens` | `Notifications` (the webhook), `ApiTokens` |
| `/company/audit-trail`, `/company/audit-anchors` | `AuditTrail`, `AuditAnchors` |
| `/company/procedures`, `/company/wiki`, `/company/documentation` | `Procedures`, `Wiki`, `Documentation` |
| `/company/specifications[/:id]` | `Specifications`, `SpecificationDetail` |
| `/company/specifications/:specId/architecture[s/:id]` | `ArchitectureDetail` |
| `/company/findings[/:id]`, `/company/self-improvement` | `CompanyFindings`, `CompanyFindingDetail`, `SelfImprovement` |
| `/company/security-tests` | `SecurityTests` |
| `/bench` | `Bench` ([models](models.md#the-benchs-scorecards-the-bench-page)) |
| `/agents`, `/governance` | `AgentSettings` (an open card shows the agent's change history, `agentSettings/ConfigHistory`, with rollback for a Manager), `components/GovernancePanel` (the Review page: inbox, intake queue, client requests, Manager policy) |
| `/support` | `HelpDesk` |
| `/lineage`, `/memory`, `/generate` | `Lineage`, `MemorySearch`, `Generate` |
| anything else | `NotFound` |

## See also

- The frontend's checks and the test commands: [testing](../TESTING.md).
- Views described with their service: the Models page in [models](models.md),
  the Review page and the Manager policy in [management](management.md), the
  sprint controls and reviews in [loop](loop.md), the integration in
  [release](release.md#a-persons-review-of-the-integration), Chat approvals in
  [integrations](integrations.md#approvals-in-slack-and-teams), teams, duties
  and roles in [platform](platform.md#auth-and-multi-tenancy), the Usage page in
  [models](models.md#who-used-what-the-usage-report).
- Running the dev server and the build: [`CLAUDE.md`](../../CLAUDE.md), Running the System.

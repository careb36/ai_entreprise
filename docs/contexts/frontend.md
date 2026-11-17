# The frontend

Not a backend context: the React application under `frontend/`. The index is [`CLAUDE.md`](../../CLAUDE.md), which holds the i18n rule every frontend change follows.

## Components

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — the sprint board: five status columns of task cards, moved by drag and drop (`@dnd-kit`, pointer and keyboard sensors), by Alt+←/→ on a focused card, or with the card's status select; a Manager's "Move to sprint…" on a blocked or To do card (`MoveToSprint`). Its parts are in `components/board/`: `BoardToolbar` (the search, `/` to focus it, the role filter, the presets All, Needs a person, High risk and Waiting on tasks, compact cards, collapse all, the activity panel, refresh), `BoardColumn` (a sticky header with the status's icon and name, the count and how many tasks need a person; a fold into a narrow strip of `CollapsedCard` chips that stay links and stay draggable; the empty state, with and without filters), `TaskCard` (priority, title, number, the assignee's `RoleAvatar` and the time of the last change), `CardSignals` (two groups that never mix: **Observed**, from the board's `observed` facts and the latest task-worker run, solid chips under a shield; **Reported**, read from the task's messages, dashed chips) and `ActivityPanel` (the latest task messages, each marked as a claim). The filters run on the loaded board (`lib/kanban.ts`); the folded columns and the density are this browser's preference (`hooks/useBoardPreferences.ts`, localStorage, optional). The design follows AixleHQ/flow's board (no code copied)
- `SprintTaskViews` — the sprint's Board tab (issue #535, [design](../design/SAVED_VIEWS.md)): the Board and List layouts, the filter builder (`TaskFilterBuilder`, one labelled row per condition, all or any) and the saved views; with no filter on the Board layout it is the `KanbanBoard`, otherwise the server evaluates the filter, limited to the sprint, and `TaskListView` shows the tasks read-only, as a table (grouped when the view says so) or as columns by status (`hooks/useTaskViews.ts`, `lib/taskFilters.ts`, `services/api/taskViews.ts`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat
- `Wiki` — the company's project wiki (`/company/wiki`, issue #445): its pages by kind with their status, a page's statements each with the sources its citations resolved to beside it, the lint's reasons, the proposal waiting, the history; a Manager edits, creates, accepts or rejects proposals, runs the lint and turns the write to `docs/wiki/` on or off (`pages/Wiki.tsx`, `components/WikiPageView.tsx`, `hooks/useWiki.ts`, `services/api/wiki.ts`). Page text may be agent output: `components/WikiMarkdown.tsx` renders it without raw HTML or images and keeps only http(s), mailto and anchor links
- `IntakeQueue` — the Review page's incoming requests (issue #532; `components/IntakeQueue.tsx`, `services/intake.ts`): the requests by status with their source and sender, the Support agent's suggestion beside each as a claim (never filled into the form), a person's decision, the task or specification it became and the request or task it duplicates; for a Manager the decision form (accept as a task in an open sprint or into a specification revision, decline with a reason, mark a duplicate as `intake:ID` or `task:ID`, snooze until a date, back to pending); for everyone a form to send a request; and the queue's numbers (`GET /v1/companies/{id}/intake-record`)
- `ArchitectureDetail` — one version of the company's architecture (`pages/ArchitectureDetail.tsx`, `/company/specifications/:specId/architectures/:id`; `services/architectures.ts`): its check, questions, modules (with what each covers in the version's own specification and, under "Covers in", in the earlier specifications it keeps), interfaces, decisions and conventions; `components/ArchitectureHistory.tsx` (issue #329, F3) names the company's version number, whether it is the current one and the earlier specifications it also covers, and lists every version of the company's architecture from `GET /v1/companies/{id}/architecture` (status, the specification it was drafted for, how many it covers, its approval date), each linked; its loading and error lines are plain text, not a status or an alert, since the page's own actions own those roles
- `SprintIntegration` — the sprint's integration on the Overview tab: the plan, the latest integration and a person's review of it (`components/IntegrationReview.tsx`, `IntegrationDiff.tsx`, `services/integrationReviews.ts`): the merge gate's state and, for an Owner, its setting; the reviews and the comments; the diff the daemon reads (on demand) with a thread under each line commented and, for a Manager, a comment form on any line it shows; and the review form (comment, approve, request changes). See [release](release.md#a-persons-review-of-the-integration)
- `BoardNotes` — the sprint page's Board notes tab (`/sprints/:id/notes`, issue #439): the sprint board's notes as claims with what was observed beside each file, the board's numbers, and for a Manager a form to add a note and a way to mark one wrong (`hooks/useBoardNotes.ts`, `services/api/boardNotes.ts`)

- **Findings** (`pages/CompanyFindings.tsx`, `/company/findings`, and `pages/CompanyFindingDetail.tsx`, `/company/findings/:id`; `components/FindingOutcomes.tsx`, `hooks/useFindings.ts`, `services/api/findings.ts`) — The self-improvement loop's findings ([design](../design/rsi-loop.md), §1 and §7), read-only for every member of the company, a Viewer included, from `GET /v1/findings?company_id=` and `GET /v1/findings/{id}` ([learning](learning.md)): each finding's source and reference, title, status (as text beside an icon, never colour alone), target measure, and a table of its cycles, each with the fitness decision, its reasons (guard, outcome, detail), the baseline's (B) and the candidate's (C) sha (the first 12 characters, the whole sha as the title) and the date; a finding's page adds the specification's path, its sha256 and its text, when it was recorded and changed, and who closed it. Loading, error (with a retry), empty and not-found states. The page has no button: the platform writes the outcomes, and a person's retry or close is not offered here.
- **Documentation** (`pages/Documentation.tsx`, `/company/documentation`; `services/documentation.ts`, `components/PackMarkdown.tsx`, `components/C4DiagramView.tsx`, `lib/c4Layout.ts`; issue #530) — The project's documentation packs: the versions, the drift lint, each document as Markdown (`react-markdown` with `remark-gfm` for tables, raw HTML skipped, no images, only `#` and http(s) links kept) with its PMBOK 8th edition domain and focus area, sources, claims and person sections, and the C4 diagrams drawn as SVG from their data (the Mermaid source shown as text, never run). A Manager compiles and writes an approved pack; an Owner approves while the lint finds no drift.

## Routes and the page that serves each

For support: from the address a person reports to the file to read. The routes are in `App.tsx`; every page is in `pages/` unless a component is named.

| Route | Page |
|---|---|
| `/login`, `/register`, `/login/sso` | `Login`, `Register`, `SsoComplete` |
| `/` | redirects to `/evidence` |
| `/evidence`, `/evidence/:runId` | `Evidence`, `EvidenceDetail` (a run, its verdict and checks) |
| `/sprints`, `/sprints/:id/:tab?` | `Sprints` (with `components/CostSummary`, `components/PromptWeight`), `SprintWorkspace` (its tabs, the Overview through `Dashboard`) |
| `/sprints/:sprintId/tasks/:taskId`, `/task/:taskId` | `TaskDetails` |
| `/dashboard`, `/analytics`, `/workspace/…`, `/sprint-ops/:id` | redirects to the active sprint's tab |
| `/companies/new`, `/companies/select` | `CompanyNew`, `CompanySelect` |
| `/company/members`, `/company/teams`, `/company/duties` | `CompanyMembers`, `CompanyTeams`, `CompanyDuties` (segregation of duties, protected paths, access review, service accounts: `components/SodPolicyPanel`, `ProtectedPathsPanel`, `SodReportPanel`, `AccessReviewPanel`, `ServiceAccountsPanel`) |
| `/company/sso`, `/company/chat`, `/company/github` | `CompanySso`, `ChatApprovals`, `GitHubIntegration` |
| `/company/usage`, `/company/quotas`, `/company/models` | `Usage`, `components/QuotaPanel`, `Models` |
| `/company/audit-anchors`, `/company/procedures`, `/company/wiki` | `AuditAnchors`, `Procedures`, `Wiki` |
| `/company/findings[/:id]` | `CompanyFindings`, `CompanyFindingDetail` |
| `/company/specifications[/:id]` | `Specifications`, `SpecificationDetail` |
| `/company/specifications/:specId/architecture[s/:id]` | `ArchitectureDetail` |
| `/company/documentation` | `Documentation` |
| `/agents`, `/governance` | `AgentSettings`, `components/GovernancePanel` |
| `/support` | `HelpDesk` (bug reports and the Support chat) |
| `/lineage`, `/memory`, `/generate` | `Lineage`, `MemorySearch`, `Generate` |
| anything else | `NotFound` |

## The desktop app

**`frontend/electron/`** — the Electron main process and preload, compiled by
`npm run electron:compile` into `dist-electron-main/` and outside the layered
`frontend/src` tree. `npm run electron:dev` loads the Vite dev server;
`npm run electron:build` builds the renderer with `--mode electron` and
packages it. The packaged app does not load `file://`: `local-server.ts`
serves `dist/` on `127.0.0.1` at a port the system picks and forwards `/v1/*`
to `BACKEND_URL` (default `http://localhost:8000`), so the page and the API
share an origin and the session cookies of issue #524 are first-party; the
desktop build calls the API on its own origin (`VITE_API_SAME_ORIGIN`, so
`API_BASE` in `services/api/client.ts` is empty). `navigation.ts` keeps the
main window on that origin, opens other links in the system browser and runs
single sign-on in a window without the preload; `ipc-handlers.ts` answers only
the local page. Design and threat model: `docs/design/SESSIONS.md`, Desktop
app.

## Layout of the larger views

A view too large for one file keeps its entry file, with the same exports, and puts its parts in a folder beside it, named after it in lower camel case: subcomponents with one job each, and `useXxx` hooks for the state that separates cleanly. Every source file stays at or under 600 lines (`scripts/check_file_sizes.py`).

- `components/GlassRoom.tsx` → `components/glassroom/`: the top bar, the three columns (`LeftSidebar`, `MessageFeed`, `RightPanel`), the right panel's panels, the message atoms and groups, the agent identities and formatters, and the hooks that derive the feed (`useFeed.ts`: scroll, clock, filters, presence) and the sprint's state (`useSprintInsights.ts`: assistant summary, tasks, quota, health, autonomy tree) from the stream.
- `pages/CompanyNew.tsx` → `pages/companyNew/`: one component per wizard step (`ProjectStep` … `ReviewStep`), their cards, the constants and types, and the hooks for the project folder (`useProjectFolder`) and the team being drafted (`useTeamDrafts`).
- `pages/AgentSettings.tsx` → `pages/agentSettings/`: the toolbar, the creator, the role groups and agent cards, the draft fields and model selector, the model recommendation, and the hooks for the list (`useAgentList`), the creator (`useAgentCreator`) and the two-step delete (`useConfirmDelete`).
- `App.tsx` → `app/` and `components/shell/`: `App.tsx` keeps the router's provider (`BrowserRouter`; the query client's is in `main.tsx`) and re-exports `LanguageSwitcher` and `sectionForPath`; `app/AppLayout.tsx` is the page around every route (the session-expired redirect to login, the desktop app's native menu, the navigation bar, the error boundary, the support bot and the toasts) and `app/AppRoutes.tsx` the routes and the legacy redirects. The navigation bar is in `components/shell/`: `NavBar.tsx` (links, the Company menu, the mobile menu), `navLinks.ts` (`sectionForPath`, `useNavLinks`), `StatusPill.tsx` (the services' health) and `LanguageSwitcher.tsx`.
- `hooks/useQueries.ts` is a barrel over `hooks/queries/`, one module per domain: `system` (capabilities, the daemon's status, models, the current role), `evidence` (runs, verifications, approvals, the audit chain, standards, the Reviewer's record), `company` (inbox, procedures, messages, setup, members, GitHub, lineage), `sprints`, `quotas`, `agents` (agents, generation, memory) and `helpDesk`. Callers, and the tests that mock it with `vi.mock('../hooks/useQueries')`, import from `hooks/useQueries` as before; a hook goes in the module of its domain, never in the barrel.
- `services/api.ts` is a barrel over `services/api/`: `client.ts` holds the fetch wrapper (base URL, timeout, retries, `ApiError`, the session: every request sends the cookies with `credentials: 'include'` and, on an unsafe method, `X-Fabrique-CSRF` from the `fabrique_csrf` cookie or the value login returned, kept in memory; a 401 tries one `POST /v1/auth/refresh` per tab at a time, a 409 from it means another tab refreshed and the request is resent, a refused refresh signs out; a 403 with `X-Fabrique-CSRF: required` fetches `GET /v1/auth/csrf` once and resends), the generic verbs and the file and text downloads (`fetchBlob`, `fetchText`). The client imports no store: `useAuthStore` registers `setSessionHooks` (which session a request belongs to, and ending it) and the app `setOnUnauthorized` (the redirect to login). `useAuthStore` keeps no credential (issue #524, `docs/design/SESSIONS.md`): the access and refresh tokens are HttpOnly cookies, and it persists only `user`, `memberships` and `expiresAt` (when the session ends at the latest); `sessionEpoch` changes on every sign-in and sign-out so a late answer from an older session is dropped; `logout` calls `POST /v1/auth/logout`; `stream.ts` opens the Glassroom's event stream (`openGlassroomStream`); each domain module (`agents`, `sprints`, `auth`, `system`, `companies`, `workspace`, `messages`, `boardNotes`, `progress`, `evidence`, `analytics`, `memory`, `support`, `github`, `findings`) holds its types and its endpoints. The barrel assembles the one `api` object and re-exports every name, so callers import from `services/api` as before. The endpoints call the verbs through that object, so a test that spies on `api.get` still sees every endpoint built on it.

## The dependency rule between layers

The frontend counterpart of the backend's rule between contexts (issue #519; the backend's is in [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)). `frontend/src` is divided into layers, listed from the bottom up in `docs/architecture/frontend.json`, each owning folders or files by path:

| Layer | Paths | What it holds |
|---|---|---|
| foundation | `i18n/`, `styles/`, `electron.d.ts`, `vite-env.d.ts` | Translations, styles and ambient types; imports nothing of the application |
| services | `services/` (the barrel `services/api.ts` and `services/api/`) | The backend's client, one module per domain, the React Query keys (`services/queryKeys.ts`) and the service hooks that call the client |
| lib | `lib/` | Pure helpers and constants; some read the services' types or classify their errors, so they sit above services |
| state | `store/`, `hooks/` | The zustand stores and the React Query hooks |
| components | `components/` | Reusable views and the parts of the larger ones |
| pages | `pages/` | One view per route, and the parts of the larger ones |
| app | `App.tsx`, `main.tsx`, `app/` | The router, the entry point, the routes and the layout around them |

- **A module imports only from its own layer or one below.** `components` never imports `pages` (a part two pages share is a component: `components/SpecificationBadges.tsx`), and `services` never imports `components` or `hooks` (a type they share lives in services: `Source` in `services/specifications.ts`).
- **The network belongs to services.** Only the modules in `network.modules` — `services/api/client.ts` (the fetch wrapper, `fetchBlob`, `fetchText`) and `services/api/stream.ts` (the Glassroom's `EventSource`) — may name `fetch`, `XMLHttpRequest`, `EventSource`, `WebSocket` or `axios`; every other module calls a function of `services` that returns data. This mirrors the backend's "HTTP only in adapters" rule (#511), itself after AixleHQ/flow's design rules (design only).
- **Tests** (`*.test.ts(x)`, a `__tests__` folder and the support in `src/test/`) are exempt from both: a page's test renders the page and stubs `fetch`. They are not exempt from everything: no module that ships may import a test or the test support, and their relative imports must resolve like every other module's.
- **Every source file is in exactly one layer**: a new folder under `src` is added to `frontend.json` in the same pull request.

`scripts/check_frontend_architecture.py` (standard-library Python, like `scripts/check_architecture.py`) reads the imports from the text with comments and strings blanked — static `import … from`, `import '…'`, `export … from` and dynamic `import()`, `import type` included — resolves relative paths and the aliases in `frontend.json` (`aliases`, empty today: the project has no `@/` path alias), and maps each file to its layer by path. The allowlist holds what broke the rule when it was adopted, one entry per edge (`"services/api/client.ts -> store/useAuthStore.ts"` was the only one, the client reading the token, gone with #524) or per use of the network (`"file: fetch"`); it may only shrink: an entry that no longer occurs is reported and `--update` drops it, and never adds one. Today it is empty.

```bash
python3 scripts/check_frontend_architecture.py            # from the repository root
python3 scripts/check_frontend_architecture.py --update   # drop stale allowlist entries
```

It is a step of `.github/workflows/frontend-build.yml`, which runs on a change to `frontend/`, the script or `frontend.json`; its tests are `backend/tests/test_check_frontend_architecture.py` ([testing](../TESTING.md)).

## Text and accessibility in the views

Every string a person reads or hears is in `en.json` and `es.json` ([`CLAUDE.md`](../../CLAUDE.md), Frontend i18n). Three checks hold it, and the accessibility basics, in place:

- `npm run lint`: `eslint-plugin-i18next` fails on literal text in JSX and in a `title`, `alt`, `placeholder`, `aria-label`, `aria-description` or `label` attribute (`eslint.config.mjs`).
- `src/uiRules.test.ts`, over every view in `pages/` and `components/` (rules in `src/test/uiRules.ts`, read from the TypeScript syntax tree with the `typescript` package already installed): what the lint rule cannot see — a template literal with words shown as text or given to an attribute read aloud (`` `${n} tokens` ``), prose given to any other attribute (a component's own `heading`, `aria-valuetext`), an `eslint-disable` of the i18next rule — and the accessibility basics: an `<img>` without `alt`; a `<button>`, `<a>` or `<Link>` whose only content is a `lucide-react` icon (in every branch of a conditional) and that has no `aria-label`, `aria-labelledby` or `title`; an `<input>`, `<select>` or `<textarea>` with no label wrapping it, no `htmlFor` naming its `id`, and no `aria-label`, `aria-labelledby` or `title`; and a non-interactive element with `onClick` that has no key handler, or neither a `role` nor a `tabIndex` (`role="presentation"` and `aria-hidden` excepted, for a backdrop). The allowlist, `src/test/uiRulesBaseline.json`, counts by `file: rule` and only shrinks, like `scripts/file_size_baseline.json`: a count above what is found fails until it is lowered (`UPDATE_UI_RULES_BASELINE=1`). It holds one entry: `NavBar`'s mobile menu, which closes on a click bubbling up from its links (a keyboard activates those too).
- `src/i18n/locales.test.ts`: `en.json` and `es.json` have the same keys; `src/i18n/coverage.test.ts`: every key a `t('…')` names exists in both.

A label is tied to its control with `htmlFor` and an `id` from `useId()` when the view can be shown twice (`pages/companyNew/AgentCard.tsx`, `pages/agentSettings/DraftFields.tsx`); an icon-only control has an `aria-label` and its icon `aria-hidden`; a spinner that replaces a button's text keeps the text for screen readers (`sr-only`); a status shown by a coloured dot alone carries `role="img"` and a name (`components/glassroom/TopBar.tsx`).

## Described elsewhere

- The i18n rule (every string in `en.json` and `es.json`): [`CLAUDE.md`](../../CLAUDE.md), Frontend i18n.
- The frontend's checks (`npm run lint`, Vitest) and the table of its test files: [testing](../TESTING.md).
- A page or component is described with the backend service it shows: the Models page in [models](models.md), the Review page and the Manager policy in [management](management.md), Files changed, reviews and the sprint's controls in [evidence](evidence.md), [loop](loop.md), [release](release.md) and [workspace](workspace.md).
- Running the dev server and the production build: [`CLAUDE.md`](../../CLAUDE.md), Running the System.
- The Chat approvals page (`pages/ChatApprovals.tsx`, `components/ChatConnectionForm.tsx`, `hooks/useChat.ts`, `services/api/chat.ts`): [integrations](integrations.md#approvals-in-slack-and-teams).
- The Duties and access page (`pages/CompanyDuties.tsx`, Owner; `components/SodPolicyPanel.tsx`, `ProtectedPathsPanel.tsx` (whether the company hosts the factory, the list read-only), `AccessReviewPanel.tsx`, `SodReportPanel.tsx`, `ServiceAccountsPanel.tsx`, `hooks/useDuties.ts`, `services/api/duties.ts`; issue #543): [platform](platform.md#auth-and-multi-tenancy) and [management](management.md).
- The Teams page (`pages/CompanyTeams.tsx`, Owner; `hooks/useTeams.ts`, `services/api/teams.ts`) and the effective role `useCurrentRole` returns (`effective_role` from `/v1/auth/me`, else `human_role`): [platform](platform.md#auth-and-multi-tenancy). The Usage page (`pages/Usage.tsx`, Manager; `services/api/usage.ts`, the CSV through `fetchText`): [models](models.md#who-used-what-the-usage-report).

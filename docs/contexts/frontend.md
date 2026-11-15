# The frontend

Not a backend context: the React application under `frontend/`. The index is [`CLAUDE.md`](../../CLAUDE.md), which holds the i18n rule every frontend change follows.

## Components

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — sprint task tracking with drag-and-drop (`@dnd-kit`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat
- `Wiki` — the company's project wiki (`/company/wiki`, issue #445): its pages by kind with their status, a page's statements each with the sources its citations resolved to beside it, the lint's reasons, the proposal waiting, the history; a Manager edits, creates, accepts or rejects proposals, runs the lint and turns the write to `docs/wiki/` on or off (`pages/Wiki.tsx`, `components/WikiPageView.tsx`, `hooks/useWiki.ts`, `services/api/wiki.ts`). Page text may be agent output: `components/WikiMarkdown.tsx` renders it without raw HTML or images and keeps only http(s), mailto and anchor links
- `IntakeQueue` — the Review page's incoming requests (issue #532; `components/IntakeQueue.tsx`, `services/intake.ts`): the requests by status with their source and sender, the Support agent's suggestion beside each as a claim (never filled into the form), a person's decision, the task or specification it became and the request or task it duplicates; for a Manager the decision form (accept as a task in an open sprint or into a specification revision, decline with a reason, mark a duplicate as `intake:ID` or `task:ID`, snooze until a date, back to pending); for everyone a form to send a request; and the queue's numbers (`GET /v1/companies/{id}/intake-record`)
- `BoardNotes` — the sprint page's Board notes tab (`/sprints/:id/notes`, issue #439): the sprint board's notes as claims with what was observed beside each file, the board's numbers, and for a Manager a form to add a note and a way to mark one wrong (`hooks/useBoardNotes.ts`, `services/api/boardNotes.ts`)

- **Documentation** (`pages/Documentation.tsx`, `/company/documentation`; `services/documentation.ts`, `components/PackMarkdown.tsx`, `components/C4DiagramView.tsx`, `lib/c4Layout.ts`; issue #530) — The project's documentation packs: the versions, the drift lint, each document as Markdown (`react-markdown` with `remark-gfm` for tables, raw HTML skipped, no images, only `#` and http(s) links kept) with its PMBOK 8th edition domain and focus area, sources, claims and person sections, and the C4 diagrams drawn as SVG from their data (the Mermaid source shown as text, never run). A Manager compiles and writes an approved pack; an Owner approves while the lint finds no drift.

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
- `services/api.ts` is a barrel over `services/api/`: `client.ts` holds the fetch wrapper (base URL, timeout, retries, `ApiError`, the session: every request sends the cookies with `credentials: 'include'` and, on an unsafe method, `X-Fabrique-CSRF` from the `fabrique_csrf` cookie or the value login returned, kept in memory; a 401 tries one `POST /v1/auth/refresh` per tab at a time, a 409 from it means another tab refreshed and the request is resent, a refused refresh signs out; a 403 with `X-Fabrique-CSRF: required` fetches `GET /v1/auth/csrf` once and resends), the generic verbs and the file and text downloads (`fetchBlob`, `fetchText`). The client imports no store: `useAuthStore` registers `setSessionHooks` (which session a request belongs to, and ending it) and the app `setOnUnauthorized` (the redirect to login). `useAuthStore` keeps no credential (issue #524, `docs/design/SESSIONS.md`): the access and refresh tokens are HttpOnly cookies, and it persists only `user`, `memberships` and `expiresAt` (when the session ends at the latest); `sessionEpoch` changes on every sign-in and sign-out so a late answer from an older session is dropped; `logout` calls `POST /v1/auth/logout`; `stream.ts` opens the Glassroom's event stream (`openGlassroomStream`); each domain module (`agents`, `sprints`, `auth`, `system`, `companies`, `workspace`, `messages`, `boardNotes`, `evidence`, `analytics`, `memory`, `support`, `github`) holds its types and its endpoints. The barrel assembles the one `api` object and re-exports every name, so callers import from `services/api` as before. The endpoints call the verbs through that object, so a test that spies on `api.get` still sees every endpoint built on it.

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
| app | `App.tsx`, `main.tsx` | The router and the entry point |

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

## Described elsewhere

- The i18n rule (every string in `en.json` and `es.json`): [`CLAUDE.md`](../../CLAUDE.md), Frontend i18n.
- The frontend's checks (`npm run lint`, Vitest) and the table of its test files: [testing](../TESTING.md).
- A page or component is described with the backend service it shows: the Models page in [models](models.md), the Review page and the Manager policy in [management](management.md), Files changed, reviews and the sprint's controls in [evidence](evidence.md), [loop](loop.md), [release](release.md) and [workspace](workspace.md).
- Running the dev server and the production build: [`CLAUDE.md`](../../CLAUDE.md), Running the System.
- The Chat approvals page (`pages/ChatApprovals.tsx`, `components/ChatConnectionForm.tsx`, `hooks/useChat.ts`, `services/api/chat.ts`): [integrations](integrations.md#approvals-in-slack-and-teams).

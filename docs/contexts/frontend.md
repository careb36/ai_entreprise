# The frontend

Not a backend context: the React application under `frontend/`. The index is [`CLAUDE.md`](../../CLAUDE.md), which holds the i18n rule every frontend change follows.

## Components

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — sprint task tracking with drag-and-drop (`@dnd-kit`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat
- `BoardNotes` — the sprint page's Board notes tab (`/sprints/:id/notes`, issue #439): the sprint board's notes as claims with what was observed beside each file, the board's numbers, and for a Manager a form to add a note and a way to mark one wrong (`hooks/useBoardNotes.ts`, `services/api/boardNotes.ts`)

## Layout of the larger views

A view too large for one file keeps its entry file, with the same exports, and puts its parts in a folder beside it, named after it in lower camel case: subcomponents with one job each, and `useXxx` hooks for the state that separates cleanly. Every source file stays at or under 600 lines (`scripts/check_file_sizes.py`).

- `components/GlassRoom.tsx` → `components/glassroom/`: the top bar, the three columns (`LeftSidebar`, `MessageFeed`, `RightPanel`), the right panel's panels, the message atoms and groups, the agent identities and formatters, and the hooks that derive the feed (`useFeed.ts`: scroll, clock, filters, presence) and the sprint's state (`useSprintInsights.ts`: assistant summary, tasks, quota, health, autonomy tree) from the stream.
- `pages/CompanyNew.tsx` → `pages/companyNew/`: one component per wizard step (`ProjectStep` … `ReviewStep`), their cards, the constants and types, and the hooks for the project folder (`useProjectFolder`) and the team being drafted (`useTeamDrafts`).
- `pages/AgentSettings.tsx` → `pages/agentSettings/`: the toolbar, the creator, the role groups and agent cards, the draft fields and model selector, the model recommendation, and the hooks for the list (`useAgentList`), the creator (`useAgentCreator`) and the two-step delete (`useConfirmDelete`).
- `services/api.ts` is a barrel over `services/api/`: `client.ts` holds the fetch wrapper (base URL, timeout, retries, `ApiError`, the 401 handler), the generic verbs and the file and text downloads (`fetchBlob`, `fetchText`); `stream.ts` opens the Glassroom's event stream (`openGlassroomStream`); each domain module (`agents`, `sprints`, `auth`, `system`, `companies`, `workspace`, `messages`, `boardNotes`, `evidence`, `analytics`, `memory`, `support`, `github`) holds its types and its endpoints. The barrel assembles the one `api` object and re-exports every name, so callers import from `services/api` as before. The endpoints call the verbs through that object, so a test that spies on `api.get` still sees every endpoint built on it.

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

`scripts/check_frontend_architecture.py` (standard-library Python, like `scripts/check_architecture.py`) reads the imports from the text with comments and strings blanked — static `import … from`, `import '…'`, `export … from` and dynamic `import()`, `import type` included — resolves relative paths and the aliases in `frontend.json` (`aliases`, empty today: the project has no `@/` path alias), and maps each file to its layer by path. The allowlist holds what broke the rule when it was adopted, one entry per edge (`"services/api/client.ts -> store/useAuthStore.ts"`, the client reading the token) or per use of the network (`"file: fetch"`); it may only shrink: an entry that no longer occurs is reported and `--update` drops it, and never adds one. Today it holds one entry.

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
- The Teams page (`pages/CompanyTeams.tsx`, Owner; `hooks/useTeams.ts`, `services/api/teams.ts`) and the effective role `useCurrentRole` returns (`effective_role` from `/v1/auth/me`, else `human_role`): [platform](platform.md#auth-and-multi-tenancy). The Usage page (`pages/Usage.tsx`, Manager; `services/api/usage.ts`, the CSV through `fetchText`): [models](models.md#who-used-what-the-usage-report).

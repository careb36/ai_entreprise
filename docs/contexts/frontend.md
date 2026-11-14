# The frontend

Not a backend context: the React application under `frontend/`. The index is [`CLAUDE.md`](../../CLAUDE.md), which holds the i18n rule every frontend change follows.

## Components

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — sprint task tracking with drag-and-drop (`@dnd-kit`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat

## Layout of the larger views

A view too large for one file keeps its entry file, with the same exports, and puts its parts in a folder beside it, named after it in lower camel case: subcomponents with one job each, and `useXxx` hooks for the state that separates cleanly. Every source file stays at or under 600 lines (`scripts/check_file_sizes.py`).

- `components/GlassRoom.tsx` → `components/glassroom/`: the top bar, the three columns (`LeftSidebar`, `MessageFeed`, `RightPanel`), the right panel's panels, the message atoms and groups, the agent identities and formatters, and the hooks that derive the feed (`useFeed.ts`: scroll, clock, filters, presence) and the sprint's state (`useSprintInsights.ts`: assistant summary, tasks, quota, health, autonomy tree) from the stream.
- `pages/CompanyNew.tsx` → `pages/companyNew/`: one component per wizard step (`ProjectStep` … `ReviewStep`), their cards, the constants and types, and the hooks for the project folder (`useProjectFolder`) and the team being drafted (`useTeamDrafts`).
- `pages/AgentSettings.tsx` → `pages/agentSettings/`: the toolbar, the creator, the role groups and agent cards, the draft fields and model selector, the model recommendation, and the hooks for the list (`useAgentList`), the creator (`useAgentCreator`) and the two-step delete (`useConfirmDelete`).
- `services/api.ts` is a barrel over `services/api/`: `client.ts` holds the fetch wrapper (base URL, timeout, retries, `ApiError`, the 401 handler) and the generic verbs; each domain module (`agents`, `sprints`, `auth`, `system`, `companies`, `workspace`, `messages`, `evidence`, `analytics`, `memory`, `support`, `github`) holds its types and its endpoints. The barrel assembles the one `api` object and re-exports every name, so callers import from `services/api` as before. The endpoints call the verbs through that object, so a test that spies on `api.get` still sees every endpoint built on it.

## Described elsewhere

- The i18n rule (every string in `en.json` and `es.json`): [`CLAUDE.md`](../../CLAUDE.md), Frontend i18n.
- The frontend's checks (`npm run lint`, Vitest) and the table of its test files: [testing](../TESTING.md).
- A page or component is described with the backend service it shows: the Models page in [models](models.md), the Review page and the Manager policy in [management](management.md), Files changed, reviews and the sprint's controls in [evidence](evidence.md), [loop](loop.md), [release](release.md) and [workspace](workspace.md).
- Running the dev server and the production build: [`CLAUDE.md`](../../CLAUDE.md), Running the System.

# The frontend

Not a backend context: the React application under `frontend/`. The index is [`CLAUDE.md`](../../CLAUDE.md), which holds the i18n rule every frontend change follows.

## Components

**`frontend/src/`** — React 18 app. Key views:
- `KanbanBoard` — sprint task tracking with drag-and-drop (`@dnd-kit`)
- `GlassRoom` — real-time agent activity monitor via SSE
- `SprintWorkspace` — sprint creation and management
- `SupportBot` — bilingual (EN/ES) built-in help desk chat

## Described elsewhere

- The i18n rule (every string in `en.json` and `es.json`): [`CLAUDE.md`](../../CLAUDE.md), Frontend i18n.
- The frontend's checks (`npm run lint`, Vitest) and the table of its test files: [testing](../TESTING.md).
- A page or component is described with the backend service it shows: the Models page in [models](models.md), the Review page and the Manager policy in [management](management.md), Files changed, reviews and the sprint's controls in [evidence](evidence.md), [loop](loop.md), [release](release.md) and [workspace](workspace.md).
- Running the dev server and the production build: [`CLAUDE.md`](../../CLAUDE.md), Running the System.

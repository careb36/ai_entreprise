# The contracts context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/contracts/`.

- **Folder:** `backend/app/services/contracts/`
- **Responsibility:** The shapes several contexts exchange, as plain data and pure functions: the verdict names, the file manifest of a deliverable (parse_file_manifest, the allowed artifact extensions), SEARCH/REPLACE edit blocks, the daemon's delivery reply as the evidence reads it, the task record's fields, the role catalogue and the standards catalogue. It performs no I/O and imports no other context; it never decides anything.
- **May import:** no other context (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Modules:** `board_notes`, `edit_blocks`, `intake_rules`, `manifest`, `roles`, `sandbox_report`, `sandbox_schemas`, `standards_catalogue`, `task_dependencies`, `task_filters`, `tool_calls`, `verdicts`, `wiki`, `wiki_lint`, `wiki_templates`.

## Components

### The role catalogue

**`backend/app/services/contracts/roles.py`** (`services/contracts/roles.py` is its shim) — The role catalogue: `ROLES` is the `AgentRole` values of `services/models/model_factory.py` (CamelCase like `ScrumMaster`: PO, Architect, ScrumMaster, Developer, QA, SecOps, TechWriter, and the new BackendDeveloper, FrontendDeveloper, DevOps (DevOps/SRE), Designer (UX/UI) and DataEngineer (Data/DBA)); `canonical` reads the older stored spellings as aliases (`Dev`, `BackendDev`, `FrontendDev`, `Data`, `SM`, `Security`…), nothing stored is rewritten. `DEVELOPER_ROLES` (Developer and the four coding roles) take the Developer's path: the delivery is role-agnostic, and `orchestration_metrics.canonical_kpi_role` maps them to `Developer`, so the Architect's consultation, the edit plan and the KPIs follow; `FILE_ROLES` add QA and the Designer (it writes UI specifications and markup, reading the existing components first) for the workspace tools; `RESEARCH_ROLES` is the research default. The coding roles' default model mirrors the Developer's (and `AGENT_MODEL_DEVELOPER` when they have no `AGENT_MODEL_<ROLE>`), `company_models` falls back to the company's Developer choice for them, and `model_suggestions` suggests them as code roles (the Designer as a deciding role). Agent roles stay free text (no validation on `POST /v1/agents`); the Agents page, the New company wizard and the Models page list the new roles, labelled from `teamRoles.*`.

### Other modules

- `sandbox_schemas`: the pydantic shapes of a sandbox run (`SandboxExecutionRequest`, `SandboxExecutionResponse`, `SandboxHealthResult`) that `workspace/sandbox_manager.py` exchanges with the sandbox.

## Described elsewhere

- `manifest` (`parse_file_manifest`) and `edit_blocks` (SEARCH/REPLACE edits): step 3 of [the sprint loop](../SPRINT_LOOP.md).
- `standards_catalogue`: the quality standards paragraph in [evidence](evidence.md).
- `wiki`, `wiki_lint`, `wiki_templates` (the project wiki's citation, statement and facts syntax and the links derived from a body; the three deterministic lint rules; the en/es scaffolding and lint reasons, `language_of` reading the company's `doc_language` defensively): [the project wiki](../design/PROJECT_WIKI.md).
- `board_notes` (the sprint board's kinds, bounds, the `NOTE … END` format and its parser, the relevance of a note to a task): [the sprint board](../design/SPRINT_BOARD.md).
- `intake_rules` (the intake queue's sources, statuses, transitions and categories, the typed links, and `measures`, the deterministic comparison of the Support agent's suggestion with a person's decision): [the intake queue](planning.md#the-intake-queue).
- `task_filters` (the filter over a company's tasks: the allowlist of fields with their operators and values, the bounds of the AND/OR tree, `parse_filter`, `matches`, and a saved view's settings): [saved views](../design/SAVED_VIEWS.md).
- `tool_calls`, `sandbox_report`, `task_dependencies`, `verdicts`: where each came from is in [the migration record](../architecture/MIGRATION.md) (steps 1, 5 and 6).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-1).

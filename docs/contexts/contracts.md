# The contracts context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/contracts/`.

- **Folder:** `backend/app/services/contracts/`
- **Responsibility:** The shapes several contexts exchange, as plain data and pure functions: the verdict names, the file manifest of a deliverable (parse_file_manifest, the allowed artifact extensions), SEARCH/REPLACE edit blocks, the daemon's delivery reply as the evidence reads it, the task record's fields, the role catalogue, the standards catalogue and the protected paths. It performs no I/O and imports no other context; it never decides anything.
- **May import:** no other context (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Interface** (`public` in `docs/architecture/contexts.json`, ADR 0018, checked by `scripts/check_architecture.py`): every module (`"*"`): the context exists to be shared.
- **Modules:** `board_notes`, `edit_blocks`, `intake_rules`, `manifest`, `protected_paths`, `roles`, `sandbox_report`, `sandbox_schemas`, `standards_catalogue`, `task_dependencies`, `task_filters`, `tool_calls`, `verdicts`, `wiki`, `wiki_lint`, `wiki_templates`.

## Components

### The role catalogue

**`backend/app/services/contracts/roles.py`** (`services/contracts/roles.py` is its shim) — The role catalogue: `ROLES` is the `AgentRole` values of `services/models/model_factory.py` (CamelCase like `ScrumMaster`: PO, Architect, ScrumMaster, Developer, QA, SecOps, TechWriter, and the new BackendDeveloper, FrontendDeveloper, DevOps (DevOps/SRE), Designer (UX/UI) and DataEngineer (Data/DBA)); `canonical` reads the older stored spellings as aliases (`Dev`, `BackendDev`, `FrontendDev`, `Data`, `SM`, `Security`…), nothing stored is rewritten. `DEVELOPER_ROLES` (Developer and the four coding roles) take the Developer's path: the delivery is role-agnostic, and `orchestration_metrics.canonical_kpi_role` maps them to `Developer`, so the Architect's consultation, the edit plan and the KPIs follow; `FILE_ROLES` add QA and the Designer (it writes UI specifications and markup, reading the existing components first) for the workspace tools; `RESEARCH_ROLES` is the research default. The coding roles' default model mirrors the Developer's (and `AGENT_MODEL_DEVELOPER` when they have no `AGENT_MODEL_<ROLE>`), `company_models` falls back to the company's Developer choice for them, and `model_suggestions` suggests them as code roles (the Designer as a deciding role). Agent roles stay free text (no validation on `POST /v1/agents`); the Agents page, the New company wizard and the Models page list the new roles, labelled from `teamRoles.*`.

### The protected paths

**`backend/app/services/contracts/protected_paths.py`** ([`docs/design/rsi-loop.md`](../design/rsi-loop.md), section 3; REQ-9) — The paths the factory's own sprints may not change without a person, as data: `PROTECTED`, each glob pattern with why it is protected, and `PATTERNS`. The list is the owner's wide one: `bench/**`, `scripts/bench/**`, `scripts/self_host/**`, `scripts/e2e/**`, `backend/app/services/evidence/**`, the bench's tests (`test_verificador_benchmark.py`, `test_bench_*.py`), `.github/workflows/**`, `.github/CODEOWNERS`, `docs/POSITIONING.md`, the list and its test, the ratchets and their data (`check_architecture.py`, `check_frontend_architecture.py`, `check_file_sizes.py`, `file_size_baseline.json`, `docs/architecture/contexts.json`, `frontend.json`), `history_check.py`, `tasks/board.py`, `learning/procedures.py`, `management/manager_policy.py` and `platform/sod.py`. The tests whose contexts include evidence are derived from the test map, not listed by hand: `evidence_test_patterns` takes the `tests.files` of `docs/architecture/contexts.json`, which the caller reads, and `protected_patterns(test_files)` is the list with them. `matches(paths, patterns)` returns each path that matches, normalised (POSIX separators, `.` and `..` resolved, a path outside the repository dropped), once, with the first pattern that matched; `**` crosses folders, `*` and `?` stay within one, and the match ignores case, since a match only adds a person's review. It decides nothing: the Verifier's `protected_paths` check and the `grader` area of the risk tier read it for a company that hosts the factory ([evidence](evidence.md)), and the Manager agent's `accept_high_risk` refuses a match ([management](management.md)); the integration merge, CI and the fitness decision are later steps of the design. The list in force is the running backend's copy, never a company project's. `.github/CODEOWNERS` gives every pattern an owner, and `tests/test_protected_paths.py` fails when a pattern has no owned line covering it (the catch-all `*` does not count, the last matching line wins).

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

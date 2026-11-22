# The contracts context

The shapes several contexts exchange, as plain data and pure functions. Read
this before changing `backend/app/services/contracts/`; each module's
docstring has the detail.

- **Folder:** `backend/app/services/contracts/`
- **May import:** no other context.
- **Interface:** every module (`"*"`): the context exists to be shared.
- **Modules:** `board_notes`, `company_limits`, `company_regex`, `company_standards`, `edit_blocks`, `emails`, `forge_policy`, `intake_rules`, `language`, `manifest`, `protected_paths`, `roles`, `rsi_cycle_record`, `run_window`, `sandbox_report`, `sandbox_schemas`, `specification_text`, `standards_catalogue`, `task_dependencies`, `task_filters`, `tool_calls`, `verdicts`, `wiki`, `wiki_lint`, `wiki_templates`.

## Rules

- No I/O, no imports from other contexts, no decisions. A caller reads the
  data (a file, the clock, the test map) and passes it in.
- A stored setting that no longer validates is never applied: it reads as
  the default (`company_limits`, `run_window`, `forge_policy`).
- A company's regular expression is compiled and run only by
  `company_regex.py`, on RE2 with bounded memory, never on Python's `re`.
  Without the binding the pattern is refused; there is no fallback.
- A company's standards file only adds: it never names a catalogue id
  (`STD-…`) and never adds a kind.

## Components

### The role catalogue

`roles.py`: `ROLES` (CamelCase, such as `ScrumMaster`), `canonical` for older
stored spellings (nothing stored is rewritten), `DEVELOPER_ROLES` (the
Developer and the four coding roles, which take the Developer's path),
`FILE_ROLES` (adds QA and the Designer) and `RESEARCH_ROLES`. Agent roles
stay free text on `POST /v1/agents`.

### The protected paths

`protected_paths.py`: the paths the factory's own sprints may not change
without a person, each pattern with why. `matches` normalises paths, drops
any outside the repository and ignores case, since a match only adds a
review. It decides nothing: the Verifier and the risk tier read it for a
company that hosts the factory ([evidence](evidence.md)), and the Manager
agent refuses a match ([management](management.md)). The list in force is
the running backend's copy, never a company project's. Every pattern needs
an owner in `.github/CODEOWNERS` (`tests/test_protected_paths.py`). Design:
[`rsi-loop.md`](../design/rsi-loop.md), section 3.

### A company's standards file

`company_standards.py`, `company_regex.py`: parse and validate
`.fabrique/standards.toml` (TOML only). File refusals reject the whole file;
a standard's refusals keep the others. The five kinds are closed.
`command_refusal` refuses at import what a standard may never run; the
daemon's command policy and sandbox still apply at run time. Design:
[`COMPANY_STANDARDS.md`](../archive/design/COMPANY_STANDARDS.md).

### Company settings as data

- `forge_policy.py`: `none`, `fetch`, `publish`; default `none`. The daemon
  keeps the same values. The setting is [platform](platform.md)'s.
- `company_limits.py`: the Developer's limits within fixed floors and
  ceilings.
- `run_window.py`: a run window over IANA zones, never fixed offsets, with
  presets and holidays passed in. The gate is [loop](loop.md)'s.
- `language.py`: the line asking agents to write a person's prose in the
  company's language while code and parsed markers stay in English.
- `emails.py`: the one rule for comparing addresses (strip, lower-case).

### The self-improvement records

- `specification_text.py`: a specification's canonical text and sha256, the
  key a finding is stored under.
- `rsi_cycle_record.py`: the cycle record's shape, checked against
  `scripts/rsi/cycle_record.py` by a parity test. Shapes only; the backend
  recomputes every kept file's hash.

### Delivery and run shapes

- `manifest.py` (`parse_file_manifest`), `edit_blocks.py` (SEARCH/REPLACE):
  step 3 of [the sprint loop](../SPRINT_LOOP.md).
- `verdicts.py`, `tool_calls.py`, `sandbox_report.py`,
  `task_dependencies.py`: shared shapes of a run and a task.
- `sandbox_schemas.py`: the pydantic shapes `workspace/sandbox_manager.py`
  exchanges with the sandbox.
- `standards_catalogue.py`: the quality standards ([evidence](evidence.md)).

### Feature syntaxes

- `wiki.py`, `wiki_lint.py`, `wiki_templates.py`: the project wiki's
  syntax, lint rules and templates ([`PROJECT_WIKI.md`](../archive/design/PROJECT_WIKI.md)).
- `board_notes.py`: the sprint board's note format ([`SPRINT_BOARD.md`](../archive/design/SPRINT_BOARD.md)).
- `intake_rules.py`: the intake queue's statuses and transitions
  ([planning](planning.md#the-intake-queue)).
- `task_filters.py`: the task filter's allowlist and bounds
  ([`SAVED_VIEWS.md`](../archive/design/SAVED_VIEWS.md)).

## See also

- How the context was built: [the migration record](../architecture/MIGRATION.md#step-1-contracts).

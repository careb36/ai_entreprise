# The planning context

What is built and in which tasks: the specification, the architecture, their
approval, the PO's sprint plan, the sprint's team, the intake queue, the stack
discovery and the project's documentation pack. It writes plans and documents;
it never runs a task. Read this before changing
`backend/app/services/planning/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/planning/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md), [evidence](evidence.md), [workspace](workspace.md), [tools](tools.md).
- **Interface:** `arch_author`, `arch_check`, `changelog_order`, `document_approval`, `spec_check`, `spec_planning`, `sprint_planner`, `sprint_team`, `stack_detection`.
- **Modules:** `adr_docs`, `already_done`, `arch_author`, `arch_check`, `arch_versions`, `changelog_order`, `company_standards_import`, `discovery_service`, `discovery_team`, `document_approval`, `intake_queue`, `intake_records`, `objective_text`, `planner_fallback`, `planning_reads`, `project_docs/`, `project_docs/c4`, `project_docs/c4_code`, `project_docs/checks`, `project_docs/closure`, `project_docs/collect`, `project_docs/collect_checks`, `project_docs/collect_code`, `project_docs/collect_plans`, `project_docs/collect_release`, `project_docs/collect_stakeholders`, `project_docs/collect_status`, `project_docs/collect_transition`, `project_docs/comms_docs`, `project_docs/control_docs`, `project_docs/domain_checks`, `project_docs/export`, `project_docs/export_docx`, `project_docs/export_markdown`, `project_docs/export_pdf`, `project_docs/final_docs`, `project_docs/iterations`, `project_docs/layout`, `project_docs/log_docs`, `project_docs/pack`, `project_docs/pack_manifest`, `project_docs/performance`, `project_docs/plan_checks`, `project_docs/quality_docs`, `project_docs/refs`, `project_docs/release_docs`, `project_docs/sanitize`, `project_docs/scope_docs`, `project_docs/security_docs`, `project_docs/specs`, `project_docs/stakeholder_docs`, `project_docs/statements`, `project_docs/status_docs`, `project_docs/texts`, `project_docs/texts_checks_en`, `project_docs/texts_checks_es`, `project_docs/texts_en`, `project_docs/texts_es`, `project_docs/texts_final_en`, `project_docs/texts_final_es`, `project_docs/texts_plans_en`, `project_docs/texts_plans_es`, `project_docs/texts_release_en`, `project_docs/texts_release_es`, `project_docs/texts_stakeholders_en`, `project_docs/texts_stakeholders_es`, `project_docs/texts_status_en`, `project_docs/texts_status_es`, `project_docs/texts_transition_en`, `project_docs/texts_transition_es`, `project_docs/texts_wstg_en`, `project_docs/texts_wstg_es`, `project_docs/transition`, `project_docs/transition_docs`, `project_docs/wstg_status`, `spec_author`, `spec_check`, `spec_diff`, `spec_impact`, `spec_planning`, `sprint_context`, `sprint_fallbacks`, `sprint_planner`, `sprint_prompts`, `sprint_team`, `stack_detection`, `wstg_plan`

## How it works

A person's brief becomes a specification the PO drafts and a Manager
approves. The Architect drafts the company's architecture from it, also
approved by a Manager. The PO then splits the approved specification into
tasks that name the criteria they cover, with a suggested team. Each task's
prompt and QA's review carry only the requirements and architecture modules
the task covers. The documentation pack is compiled from the records, not
written by a model.

## Rules

- Ids (`REQ-1`, `REQ-1.2`, `MOD-1`, `ADR-1`) are assigned by the code in order,
  never by a model or a request.
- Only a Manager approves a specification or an architecture (409 while its
  deterministic check blocks); only an Owner approves a documentation pack
  (409 while its lint blocks). An approved version is never edited; a revision
  is a new version.
- A plan made without the planner's model says so (`planner_fallback`,
  recorded in the audit log by the backend). The objective a person wrote is
  kept whole, apart from the model's `goal`.
- The worker never runs a task outside its sprint's team. The team decides who
  gets tasks, not who judges them: QA and the Verifier still see every
  delivery.
- Intake: the `source` is what the backend observed, never a field the sender
  fills. Only a Manager decides. The Support agent's suggestion (a claim) and
  the person's decision are separate fields.
- Hashes of files written through the daemon are the daemon's read-back,
  kept as observations; the pack's digests and manifest are computed by the
  backend.
- The pack has no personal data (roles and counts, never names or
  addresses). Record text never becomes Markdown or Mermaid structure. A
  person's statement and the TechWriter's prose are quoted as claims. An
  effort, carry or intake event is read only when the platform wrote it
  (actor `system`); any other is a claim and is not read.
- Sections only a person can decide (business case, budget, risk appetite…)
  are templates no agent fills.

## Components

### The specification

`spec_check.py` normalises ids and is the deterministic gate (a requirement
without a criterion, a quality target without a number, an open question).
`spec_author.py` asks the PO for a draft in a fixed JSON shape.
While it writes, the PO may read the project (see "The authors read the
project" below).
`spec_planning.py` has the PO split an approved specification into tasks
(`uncovered` lists what no task covers) and reads coverage from the boards.
`spec_diff.py` diffs two versions by id; `spec_impact.py` lists the tasks and
modules a revision affects and changes nothing.

### The architecture

`arch_check.py` normalises and checks the architecture (missing references, a
dependency cycle, an open question). `arch_author.py` drafts it with the
Architect; a date or a supersession the Architect writes is dropped, since
those are a person's record. A company has one architecture, versioned:
`arch_versions.py` holds the current version, revisions for a new
specification and coverage across the specifications it serves. `adr_docs.py`
renders one ADR file per decision, keeping numbers already in the repository.
`document_approval.py` approves specifications, architectures and packs, and
is shared with the Manager agent ([management](management.md)).

### A sprint's team

`sprint_team.py`: the PO suggests a team within the hired roles; the
company's `team_selection` is `suggested` (a Manager confirms or adjusts) or
`automatic`. `team_source` records who chose. A person never picks a team
from scratch. The planning prompt also says what each role can do
(`capabilities_section`, issue #766): the roles that read the code and run
the tests when they execute a task (`roles.delivers_files`, from
`contracts/tool_steps`: since 2026-11-24 every role, unless its profile
denies reading or running); the others write whole files in their answer but
cannot read the project or run anything, so a task that runs scripts or a
validation goes to the first kind.
`fit_tasks`, which every plan passes through (from a prompt, self-improvement,
from a specification), enforces it without a model: a task whose title runs,
checks or changes the code, given to a role without the tools, moves to the
closest file role on the team (QA for a validation that changes nothing,
DevOps for deployment or CI, else a developer role), with `reassigned_from`
and a note in its description. Only the title is read, since a description
may quote the objective whole. A TechWriter's task moves only when the
title's first word runs or checks something (its documents are whole files in
its answer); an acceptance-test task, or a team without a file role, is left
alone. With every role's execution turn holding the tools, this move is the
fallback for a role whose profile denies them, not the rule.

### The PO reads before it plans

`planning_reads.py`: the planning step's ceiling is reading
(`tool_steps.PLAN`). Where the daemon answers and the company's project path
is known, `sprint_planner._plan_with_po` asks through the tool loop with the
read-only workspace tools (`review_tools.for_step`: list, read, search,
find_file) for at most `PLANNING_TOOL_ROUNDS` rounds; the daemon's listing
stays in the prompt. Each reading round is a `plan_read` audit turn and the
final one carries the calls' observations (`tool_calls`); the plan carries
them as `planning_reads`. The answer keeps today's form and parser. No
daemon, no root, 0 rounds, a call made on an event loop (the planning
endpoints run the planner in a thread for that reason), or an empty answer
after the reads: today's single call. The reads also offer read-only Git
(`blame` included) and a document's text when the daemons announce them
(`tools/workspace_reads`), and the record tools beside them (`board__task`,
`board__evidence`, `board`): scoped to the company's board (any task of the
company, never another's), read by `tasks/task_records`, off with
`BOARD_TOOLS_ENABLED`, and never without the reads.

### The PO checks what is already done

`already_done.py` (owner, 2026-11-24: sprint 8 planned four tasks for an
issue whose option, tests and docs were already on main). Beside the reads,
the PO is asked to check first whether each part of the objective already
exists (search the named flags, functions and files, read them and their
tests), to plan tasks only for what is missing, and to name what is done in
an optional `already_done` key (`part`, `evidence`, `paths`). The plan keeps
two fields, never one: `already_done_claim` (the PO's words, paths it named
or path-like words of its evidence) and `already_done_observed` (from the
reads' observations: `files_read` with the sha256 the daemon reported, or the
backend hashed for `git show_base`; `searches`; and `claimed_paths`, per
claimed path whether the PO read it in this planning turn, `read: false`
being "not read"). When the PO claims parts done and plans no task, the plan
is one QA task, "Verify that <objective> is already met: run the tests that
cover it and check each criterion", depending on nothing; with a claim, one
task left is a plan (not `too_few_tasks`). Without reads (no daemon, no root,
0 rounds) the PO is not asked and a claim in its answer is ignored. The
plan endpoints record both under the plan's ref (`plan:<company>:<plan_id>`,
action `already_done_claim`: the claim in `result`, the observations in
`details`), sharing the `plan_id` of a fallback record; `start_sprint` with
that `plan_id` copies the row to `sprint:<id>:planning`, which records no
work. The `Sprint` row has no column for plan metadata. The plan from an
approved specification (`spec_planning.plan_from`) goes through the same
reads and check (`planning_reads.spec_answer`, with the PO's system prompt
and billing unit; `plan_read` audit turns; the plan's `planning_reads`):
its claim and observed fields are the same, and a plan whose every part is
claimed done is one QA task, "Verify that the specification "<title>" is
already met", covering every criterion, without the test-writing tasks.
Without reads it is today's single call (`spec_author.ask`) and a claim is
ignored.

### The authors read the project

The PO writing a specification (`spec_author.draft_from`) and the Architect
writing or revising an architecture (`arch_author.draft_from`) have families R
and S (`tool_steps.SPEC_AUTHOR`, `ARCH_AUTHOR`). `planning_reads.author_turn`
gives them the read-only workspace tools on the company's project
(`review_tools.for_step`: list, read, search, find_file, read-only Git and a
document's text when announced, navigation when announced; never an edit, a
move, a delete, a lint or a test run) beside their research tools, as one
`CombinedToolbox` whose round limit is the larger of the reads'
(`PLANNING_TOOL_ROUNDS` for the PO, `ARCHITECT_TOOL_ROUNDS` for the
Architect) and the research tools' own. Each round is an audit turn
(`spec_author_read`, `arch_author_read`) and the final one carries every
call's observation (`tool_calls`); the draft's `sources` stay the research
calls only. No daemon, no root, 0 rounds or a call on an event loop: the
research tools alone, as before.

### The intake queue

`intake_queue.py`, `intake_records.py`: requests that reach a company (from a
ticket, a brief, an API token or chat, see
[integrations](integrations.md#approvals-in-slack-and-teams)) until a person
decides. The moves are `contracts/intake_rules.TRANSITIONS`. Accepting creates
a task in an open sprint or a specification revision with the request as an
open question. Every move is an audit row; `history` reads it back from the
chain. The intake janitor wakes snoozed requests; it runs in the backend
because only the backend seals audit rows.

### The sprint planner

`sprint_planner.py` makes the foundation, execution and self-improvement
plans and re-exports the names of `sprint_prompts.py` (templates),
`sprint_context.py` (roster, stack summary, the project's listing) and
`sprint_fallbacks.py` (deterministic plans). The listing the PO sees comes from
the workspace daemon (`project_listing`: `workspace.list` to depth 2, at most
120 entries), read by the caller (`POST /v1/plan_sprint`, the self-improvement
plan, the follow-up sprint at a close): the backend container does not mount
the user's project. Without a daemon, the backend's own listing
(`_list_root_files`) is said. The execution and self-improvement plans ask the PO
for each task's `depends_on` (the titles of the tasks done first) and read it
as `spec_planning.py` does, through `sprint_planner.with_dependencies`: by
title or position, unknown ones and self dropped, a cycle broken; `start_sprint`
then holds a task until those are done. `changelog_order.last` then makes a
changelog task (its title or description names the changelog) depend on
every other task, QA's test tasks included, and drops any edge to it: the
fragment describes the sprint's diff, merged into its worktree. `objective_text.py` reads an objective's explicit items
without a model; `planner_fallback.py` records why a plan fell back
(`no_model`, `model_error`, `unparseable`…).

### Stack discovery

`stack_detection.py` reads the stack from the files alone (no model, no
network). `discovery_team.py` holds the rule-based team and sets each
suggested agent's model to its role's default; `discovery_service.py` asks the
model for a team.

### Importing a company's standards

`company_standards_import.py`: the daemon reads `.fabrique/standards.toml` at
the head of the default branch (never a task branch); the backend hashes what
it received, validates it, hashes each guard's files and dry-runs the
standards before storing a draft. Design:
[`COMPANY_STANDARDS.md`](../archive/design/COMPANY_STANDARDS.md).

### The security test plan (OWASP WSTG)

`wstg_plan.py` selects tests by a deterministic rule table from signals in
the specification, architecture and stack; a model may only propose additions,
shown as claims. `project_docs/wstg_status.py` sets each row from an
observation (a Verifier check) or a person's recorded review. Design:
[`PROJECT_DOCUMENTATION.md`](../archive/design/PROJECT_DOCUMENTATION.md#security-test-plan-and-checklist-owasp-wstg-issue-539).

### The documentation pack

`project_docs/` compiles a project's management documentation, placed after
the PMBOK Guide (8th edition). Design and threat model:
[`PROJECT_DOCUMENTATION.md`](../archive/design/PROJECT_DOCUMENTATION.md).

- **Snapshot.** `collect.py` reads the records into plain data; the
  `collect_*` modules add keys: `collect_status`, `collect_release`,
  `collect_plans`, `collect_stakeholders`, `collect_checks`,
  `collect_transition` and `collect_code` (the stored code graph, through the
  tools interface; the pack never asks the daemon).
- **Pack.** `pack.py` builds the documents, keeps each one's input digest and
  lints drift (`pack.lint`); its one model call is the charter's background
  (`write_prose`). `layout.py` places documents by domain, `refs.py` cites
  records with digests, `sanitize.py` escapes record text, `specs.py` groups by
  specification, `statements.py` holds the person sections (append-only,
  claims). A document may carry `data`: the same records its body renders,
  in no language, for a reader that grades the pack (the traceability rows,
  `data.trace`; the checks' results, `data.checks`; issue #698,
  `scripts/bench/docs_pack_grade.py`).
- **Builders** (pure, from the snapshot): `scope_docs`, `control_docs`,
  `log_docs`, `c4` and `c4_code` (claim beside observed files),
  `status_docs` with `performance`, `release_docs` with `iterations`,
  `final_docs` with `closure`, `quality_docs`, `comms_docs`,
  `stakeholder_docs`, `transition_docs` with `transition`, and
  `security_docs`.
- **Checks.** `checks.py` holds `CHECKS` per domain; `plan_checks` and
  `domain_checks` add more. A check is not computable, with its reason, when
  the records cannot decide.
- **Manifest and export.** `pack_manifest.py` lists every file with its
  sha256, signs it with the audit key and enters its digest in the audit
  chain. `export.py` renders DOCX (`export_docx`) or PDF/A (`export_pdf`) from
  our own parser (`export_markdown`), reproducibly, with no raw HTML, link or
  image.
- **Language.** Fixed strings come from `texts.py` in English and Spanish:
  `texts_en`, `texts_es`, `texts_status_en`, `texts_status_es`,
  `texts_release_en`, `texts_release_es`, `texts_final_en`, `texts_final_es`,
  `texts_plans_en`, `texts_plans_es`, `texts_stakeholders_en`,
  `texts_stakeholders_es`, `texts_checks_en`, `texts_checks_es`,
  `texts_transition_en`, `texts_transition_es`, `texts_wstg_en`,
  `texts_wstg_es`.

## See also

- How a plan's dependencies are kept and followed: [tasks](tasks.md).
- The test-writing tasks and acceptance tests: step 4 of [the sprint loop](../SPRINT_LOOP.md).
- `POST /v1/discover_team` and the project's inspection: [workspace](workspace.md).

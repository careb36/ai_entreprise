# The loop context

Driving a sprint's tasks: the task worker, board moves, carry-over, sprint
control, a person's review of a run and the company bootstrap. It decides what
happens next from the evidence; it never computes a verdict itself. Read this
before changing `backend/app/services/loop/`; each module's docstring has the
detail.

- **Folder:** `backend/app/services/loop/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md), [evidence](evidence.md), [workspace](workspace.md), [tools](tools.md), [learning](learning.md), [planning](planning.md), [producers](producers.md). Imported by [release](release.md) and [management](management.md).
- **Interface:** `carry_over`, `delivery_checks`, `run_review`, `sod_requesters`, `sprint_control`.
- **Modules:** `agent_config_history`, `answers`, `board_moves`, `carry_over`, `company_service`, `console_host`, `delivery_checks`, `delivery_suites`, `edit_plan`, `folder_baseline`, `foundation_setup`, `preconditions`, `refuted_undo`, `replicas`, `review_digest`, `run_review`, `run_window_gate`, `sod_requesters`, `sprint_archive`, `sprint_budget`, `sprint_close`, `sprint_control`, `task_edit`, `task_routing`, `task_worker`, `wiki_close`, `worker/board_writes`, `worker/close`, `worker/deliver`, `worker/execute`, `worker/intake`, `worker/messages`, `worker/procedures_round`, `worker/prompt`, `worker/prompt_budget`, `worker/qa_review`, `worker/quality_gate`, `worker/record`, `worker/related`, `worker/review`, `worker/review_evidence`, `worker/review_prompt`, `worker/sprint_notes`, `worker/task_run`, `worker/wiki_section`.

## How it works

`task_worker.py` is the `task.assign` consumer the lifespan starts; it
re-exports the stages in `worker/`. `worker/intake` claims a task under its
lock; `worker/task_run` gathers context, `worker/execute` calls the model,
`worker/deliver` sends the files to the daemon and `worker/record` ingests the
run and has the Verifier judge it. `worker/review` runs the review cycle (one
replica at a time) that sends delivered tasks to QA, and `worker/close`
finalises a sprint once its tasks settle. The step-by-step flow is
[the sprint loop](../SPRINT_LOOP.md).

## Rules

- The verdict is the Verifier's. A review, an agent message or a sprint note is
  a claim, never evidence; a note's observations go in a separate `observed`
  field.
- A cut model answer is sent back, never recorded.
- A halted sprint (`planned`, `paused`, `cancelled`) dispatches, reviews and
  retries nothing.
- Work starts only when its preconditions hold: with delivery tests off or the
  host sandbox, an Owner must have recorded an acknowledgement. Every endpoint
  that starts work answers 409 `missing_acknowledgements` before changing
  anything; the Manager agent never stands in for the Owner; the worker checks
  again before it claims a task.
- Segregation of duties: a person who started a task's sprint cannot accept its
  held delivery, from the web, a chat button or the board alike (403, audited
  `sod_refused`).
- A company that hosts the factory (`protected_paths_enabled`) never gets a
  model-planned follow-up sprint, whatever the setting.
- A sprint with evidence is archived, never deleted; the audit chain is never
  touched.
- What a sprint does once (follow-up, retrospective, procedures, engram)
  happens only at its first close.

## Components

### Reviews of a run

`run_review.py`: reviews as on a pull request, line comments and a review
(`comment`, `approve`, `request_changes`). A Manager writes them, a Viewer
reads them; audited `run_review_<event>` with `via` (`web`, `slack`, `teams`).
On a task-worker run a review acts only where the loop waits for a person
(`request_changes` sends the task back with a `NEEDS WORK:` comment, `approve`
accepts a risk hold); anything else is a 409. On another producer's run it
records an approval. `sod_requesters.py` reads who asked for the work from the
records, for the segregation check ([design](../archive/design/SEGREGATION_OF_DUTIES.md)).
The integration's review is in [release](release.md#a-persons-review-of-the-integration).

### The company bootstrap and its foundation sprint

`company_service.py`, `foundation_setup.py`: `POST /v1/init_company` creates the
company, its agents and credentials, and plans "Sprint 0 — Foundations" unless
it is declined, or the daemon observed the folder to be a repository with
source code. The decision rests on the daemon's observation, never on a claim
in the request; an unobserved folder gets the sprint.

### An agent's configuration and its revisions

`agent_config_history.py`: every change to an agent's name, role, persona,
seniority, specialty, model, system prompt or budget (`PATCH /v1/agents/{id}`,
Manager) is a revision: an `agent_config_changed` audit row, in the company's
hash chain, with the fields changed, their values before and after, who
changed them, the reason when given, and its source (`edit` or `rollback`).
`GET /v1/agents/{id}/config-revisions` (Viewer) lists them, newest first.
`POST /v1/agents/{id}/config-revisions/{revision_id}/rollback` (Manager) puts
back what a revision replaced, as a revision of its own with
`rolled_back_from`, and says which of those fields changed again since. A
rollback whose values the agent already has is refused (409). From
Paperclip's agent configuration revisions (MIT).

### A sprint's budget in money

`sprint_budget.py`: an Owner may set `sprint_budget_cents` (and
`budget_warn_percent`, default 80) in the company's limits. Before a task's
model turn the worker reads the sprint's cost as the platform priced it from
the proxy's usage (`services/models/run_cost`), never a figure an agent
reported. At or past the budget the sprint pauses with the reason, the task
waits as `todo`, and a `sprint_budget_reached` audit row records the
incident; a cost that cannot be priced pauses it too. Past the warning share,
once per sprint, a `sprint_budget_warning` row and a Glassroom event say so.
From Paperclip's budget policies (MIT).

### Sprint control and autonomy

`sprint_control.py`: start, pause, resume, stop and dispatch (all Manager).
A provider out of credit pauses the sprint with a `pause_reason` and puts the
task back to `todo`; a transient model failure re-queues it at most
`MAX_TRANSIENT_RETRIES` (3) times, then blocks it. Autonomy (Manager to change):
at 1 every QA-approved task waits for a person, at 2 only high-risk ones, at 3
follow-ups and procedures also run on their own.

### The run window

- `preconditions.py`: the Owner's acknowledgements a sprint needs before it runs
  (see Rules); a task queued by another path pauses its sprint.
- `run_window_gate.py`: outside the company's run window, and unless the sprint
  is `urgent`, no ready task is queued and the worker holds a task before its
  next model turn; a running model call is never cut. Each hold and release is
  a task event by the System with the instant the backend computed; neither
  changes the task's status. A lifecycle worker releases due sprints, so a
  restart releases from the database alone. The setting is in
  [platform](platform.md), the window in [contracts](contracts.md).

### Moves on the board

- `board_moves.py`: a Manager moving a card to To do starts it, recorded as a
  send-back so the rework budget restarts; a `completed` or `cancelled` sprint
  is reopened, an archived one is a 409. A move to Discarded needs the
  person's reason as the comment (422) and a task in To do or Blocked (409);
  the task is settled, never dispatched or carried over, and a task waiting
  on it is held for a person. Moving it to To do reopens it.
- `sprint_close.py`: a second close after a reopening answers
  `reclosed_after_reopen` and skips what happens once.
- `carry_over.py`: moves a task blocked for a person into another open sprint
  of the company, once per task, within `SCOPE_HARD_MAX`.
- `task_edit.py`: a Manager edits a `todo` or `blocked` task's title or
  description with a reason; the change is a chained `edited` event with both
  sides.

### Editing and removing a sprint

`sprint_archive.py`: `PATCH /v1/sprints/{id}` (Manager) edits name, goal and
duration. Delete is Owner only (never a token or the Manager agent:
`delete_sprint` is in `ALWAYS_ESCALATE`) and only for a halted sprint. A sprint
with any evidence (runs, events, messages, audit rows about it…) is archived
instead, stays readable by id, and an Owner may unarchive it.

### The Developer's prompt

- `worker/prompt`: the read-only context (memory, lessons, done tasks,
  standards, procedures); a failing reader leaves its section out.
- `worker/prompt_budget`: the prompt as named sections in cache order. Over
  `PROMPT_BUDGET` whole sections go, least important first, named under
  `## Left out of this prompt`; priority 0 (format, task, brief, review
  feedback) is never cut. The measure goes on the run's evidence as an
  observation of the backend's own.
- `worker/related`: the seeds of the `## Related code` section (the approved
  architecture's modules for the task's requirements, files earlier runs
  wrote).
- `worker/wiki_section`: the company's wiki pages in force for the task.
- `worker/sprint_notes`: writes a delivery's `NOTE … END` blocks to the sprint
  board and puts the relevant notes in the prompt.
- `task_routing.py`: estimates the task's difficulty and routes its model; the
  estimate is an `estimated` event and goes on the run's evidence.
- `edit_plan.py`: the Architect's edit plan in architect/editor mode (off by
  default; [models](models.md)).

### Delivery and QA

- `worker/deliver`: the files to the daemon (`workspace.generate`, or queued on
  `workspace.deliver`).
- `refuted_undo.py`: without Git, a delivery the Verifier refutes is undone at
  once from the daemon's snapshot (`workspace.undo`, audited as
  `run_delivery_undone`), so the shared folder holds only verified work; a
  file another task changed since is a conflict that touches nothing. The
  next attempt is told it starts from the files as they were.
- `folder_baseline.py`: without Git, before a delivery's tests run, records
  which tests failed in the folder's latest run for another task whose
  delivery is still there (`test_baseline`), so the Verifier does not blame
  this delivery for them.
- `delivery_checks.py`: guards the acceptance tests on a task branch; a test
  written on a sibling branch is not run against this task.
- `delivery_suites.py`: runs the suites of the folders the delivery wrote in and
  the company's every-delivery commands in the sandbox, for the Verifier's
  `required_commands` check.
- `worker/board_writes`: every board transition as a task event; a `verified`
  event names the run's latest verification.
- `worker/quality_gate`: the form check before QA.
- `answers.py`: reads a model's answer before anything is recorded.
- `review_digest.py`, `worker/review_prompt`, `worker/review_evidence`: what QA
  sees: every file within a fixed size, the agent's message labelled as a
  claim, the platform's observations marked as ones a review does not settle.
- `worker/qa_review`: the verdict (only `APPROVED:` or `NEEDS WORK:`), the hold
  for a person, and the one rework budget per task (`MAX_REWORKS`, or the
  company's `max_reworks`).
- `worker/messages`: hand-over messages (claims) and the ScrumMaster's triage of
  an escalation.

### Closing a sprint

`worker/close`: the retrospective, one close across replicas, and at a first
close the procedures round (`worker/procedures_round`: keep or revert
procedures by the board's numbers) and the wiki compile (`wiki_close.py`, which
never raises).

### Elsewhere

`replicas.py` (claims, recovery, one close): [platform](platform.md).
`console_host.py` (the loop's side of a console producer):
[producers](producers.md).

## See also

- The task worker's steps: [the sprint loop](../SPRINT_LOOP.md).
- Settings (`PROMPT_BUDGET`, `MAX_REWORKS`, `DELIVERY_TESTS_ENABLED`, `RUN_WINDOW_RECHECK_SECONDS`): [configuration](../CONFIGURATION.md).
- Design: [segregation of duties](../archive/design/SEGREGATION_OF_DUTIES.md), [project wiki](../archive/design/PROJECT_WIKI.md), [sprint board](../archive/design/SPRINT_BOARD.md).

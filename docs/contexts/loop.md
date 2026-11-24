# The loop context

Driving a sprint's tasks: the task worker, board moves, carry-over, sprint
control, a person's review of a run and the company bootstrap. It decides what
happens next from the evidence; it never computes a verdict itself. Read this
before changing `backend/app/services/loop/`; each module's docstring has the
detail.

- **Folder:** `backend/app/services/loop/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md), [evidence](evidence.md), [workspace](workspace.md), [tools](tools.md), [learning](learning.md), [planning](planning.md), [producers](producers.md). Imported by [release](release.md) and [management](management.md).
- **Interface:** `carry_over`, `delivery_checks`, `run_review`, `sod_requesters`, `sprint_control`.
- **Modules:** `agent_config_history`, `answers`, `board_moves`, `carry_over`, `company_service`, `console_host`, `daemon_gate`, `delivery_checks`, `delivery_suites`, `edit_plan`, `folder_baseline`, `foundation_setup`, `preconditions`, `refuted_undo`, `repeat_delivery`, `replicas`, `review_digest`, `routine_revisions`, `routines`, `run_review`, `run_window_gate`, `sod_requesters`, `sprint_archive`, `sprint_budget`, `sprint_close`, `sprint_control`, `task_edit`, `task_routing`, `task_worker`, `wiki_close`, `worker/architect_brief`, `worker/board_record`, `worker/board_writes`, `worker/close`, `worker/close_listing`, `worker/deliver`, `worker/execute`, `worker/execute_tools`, `worker/intake`, `worker/messages`, `worker/procedures_round`, `worker/prompt`, `worker/prompt_budget`, `worker/qa_review`, `worker/qa_review_reads`, `worker/quality_gate`, `worker/record`, `worker/related`, `worker/review`, `worker/review_evidence`, `worker/review_prompt`, `worker/sprint_notes`, `worker/task_run`, `worker/triage_reads`, `worker/wiki_section`.

## How it works

`task_worker.py` is the `task.assign` consumer the lifespan starts; it
re-exports the stages in `worker/`. `worker/intake` claims a task under its
lock; `worker/task_run` gathers context, `worker/execute` prepares the task's
worktree under the Git workflow (`developer_tools.enter_task_worktree`, so a
first attempt's tools run there) and calls the model,
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

### No agent turn without a workspace daemon

`daemon_gate.py`: before the worker claims a task, when no daemon heartbeat
was heard within `DAEMON_HEARTBEAT_OFFLINE_SECONDS`, a `workspace.status`
request asks; only NATS's "no responders" counts as absent (a timeout is
unknown and the task runs). After a run, one whose workspace tool calls were
all answered "no responders" (`daemon_absent` on each observation) is not
handed to review. Either way the sprint pauses with the reason (run
`./scripts/start-daemon.sh`, then resume), the task goes back to `todo` with a
`paused` event (no rework counted), a `sprint_daemon_absent` audit row records
the incident once per pause, and the Glassroom shows
`sprint.paused_daemon_absent`. Mirrors `sprint_budget.py`.

### Routines

`routines.py`: a Manager writes a routine (a title, a description, the hired
role that does it and a `routine_schedule`) with
`/v1/companies/{id}/routines` and `/v1/routines/{id}`. The `routine_scheduler`
worker checks every minute; a due routine opens a sprint `Routine: <title> —
<date>` with its one task, queued like any other, so the run window, the
sprint budget, the Verifier and QA apply. While the sprint it opened last is
still open, the occurrence is skipped. Replicas claim an occurrence with a
conditional update, so it runs once. Every change, run and skip is an audit
row (`routine_*`). From Paperclip's routines (MIT).

`routine_revisions.py`: each change that alters a routine writes its next
numbered revision, a whole snapshot (`GET /v1/routines/{id}/revisions`). A
run records the revision and the variable values it used in its
`routine_ran` row; "run now" may give values, a scheduled run takes the
defaults and is skipped when a required one is missing. `POST
/v1/routines/{id}/revisions/{number}/restore` (Manager) puts an earlier one
back as a new revision with `restored_from`. From Paperclip's routine
revisions and variables (MIT).

### Sprint control and autonomy

`sprint_control.py`: start, pause, resume, stop and dispatch (all Manager).
A provider out of credit pauses the sprint with a `pause_reason` and puts the
task back to `todo`; a transient model failure re-queues it at most
`MAX_TRANSIENT_RETRIES` (3) times, then blocks it, and a final answer that was
only a tool call written as text re-queues it once (`MAX_TOOL_CALL_AS_TEXT_RETRIES`,
a new attempt with a fresh tool budget), then blocks it. Autonomy (Manager to change):
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
  `person_fields` records who made each change from the signed-in session
  (`sod.actor_of`): an API token is named as itself with its creator's
  account; no signed-in person is `identified: false` (issue #767).
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
- `worker/architect_brief`: the Architect's brief (`worker/execute`,
  `_consult_architect`) runs through the tool loop with the workspace's read
  tools (`review_tools.for_step` for the `architect_brief` step: list,
  read, search, find_file, read-only Git and a document's text when
  announced, on the task's root; no edit, move, delete, lint or test run)
  and, since the step allows research (R and S), the research tools beside
  them (`research__package`, `research__advisories`, a fetch of the allowed
  hosts: `research_tools.company_toolbox`, attached with
  `board_tools.attach` within the same rounds; never without the reads),
  for at most
  `ARCHITECT_TOOL_ROUNDS` rounds. Each call is an observation on its turns in
  `audit_log` (`details.tool_calls`); the brief is a claim, and a tool call
  typed as text is no brief. Without the daemon it is one plain call. It also
  builds the Architect's agent and system prompt from its row
  (`architect_agent`, `system_prompt`).
- `worker/execute_tools`: the execution turn's toolbox, moved out of
  `worker/execute`: the task's step (`tool_steps.execution_step`: a
  documentation task is the `documentation` step), `toolbox_for` for that
  step and the role within the model's limits, the record tools on a rework,
  and `tools_offered` with the step, its families and its write scope.
- `worker/board_record`: the record tools' scopes and offer
  (`tools/board_tools`: `board__task`, `board__evidence`; the readers moved
  to `tasks/task_records`, so planning reads them too). The scope is the
  turn's company and its task plus the tasks it depends on (`task_scope`), or
  for the ScrumMaster's triage the task's sprint (`sprint_scope`), read from
  the database; the readers split each record into observations (events' kinds,
  actors, statuses and times, the system's and gates' own text, the
  Verifier's checks, the files' sha256, `tools_offered`, the round count) and
  claims (comments, verdicts, rework requests, the plan's words, the
  producer's model, claims and deliverable). `offer` puts them beside the QA
  review's reads and the Architect's brief tools, and `on_rework` beside an
  execution turn's tools on a rework, within the turn's rounds; a turn
  offered no tools gets none.

### Delivery and QA

- `worker/deliver`: the files to the daemon (`workspace.generate`, or queued on
  `workspace.deliver`). A documentation turn's answer delivers documentation
  only (`docs_scope`, `contracts/tool_steps.writable`: a TechWriter, or a task
  whose kind is documentation): a block naming another path is left out, the
  review payload says so, and the run's evidence records `docs_write`
  (`refused`, `reason`), the backend's decision.
- `refuted_undo.py`: without Git, a delivery the Verifier refutes is undone at
  once from the daemon's snapshot (`workspace.undo`, audited as
  `run_delivery_undone`), so the shared folder holds only verified work; a
  file another task changed since is a conflict that touches nothing. The
  next attempt is told it starts from the files as they were.
- `folder_baseline.py`: without Git, before a delivery's tests run, records
  which tests failed in the folder's latest run for another task whose
  delivery is still there (`test_baseline`), so the Verifier does not blame
  this delivery for them.
- `repeat_delivery.py`: a delivery refuted for its code that sends the same
  files, byte for byte, as the refuted attempt before it (the backend's own
  sha256 of what it sent, `evidence.expected`) is blocked for a person at once
  instead of going back again. An unconfirmed delivery may always be sent
  again. From Paperclip's rewake throttle (MIT).
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
- `worker/qa_review_reads`: before the verdict, QA may read the delivery with
  the read-only workspace tools (`tools/review_tools`) through the Developer's
  tool loop, for `QA_REVIEW_TOOL_ROUNDS` rounds. The verdict keeps the same
  messages, client and parser; an answer without a verdict falls back to the
  single tool-less call unchanged, and so does a review with no recorded
  delivery or no daemon. Each reading round is a `qa_review_read` audit turn,
  priced but outside the QA rate; the calls' observations go on the verdict's
  turn (`tool_calls`). The record tools (`worker/board_record`) go beside the
  reads.
- `worker/messages`: hand-over messages (claims) and the ScrumMaster's triage of
  an escalation.
- `worker/triage_reads`: before its decision on a blocked task, the
  ScrumMaster may read the task's root with the read-only workspace tools
  (`review_tools.for_step`, step `scrum_decision`: read only) for
  `SCRUM_TRIAGE_TOOL_ROUNDS` rounds. Each reading round is a
  `scrum_triage_read` audit turn and the final one carries the calls'
  observations (`tool_calls`); the decision stays a claim, parsed as before.
  No daemon or root, 0 rounds, or no decision after the reads: today's single
  call (`scrum_master.ask_triage`). The record tools go beside the reads
  (`board_record.offer(..., sprint=True)`), scoped to the blocked task's
  sprint (`sprint_scope`); never without the reads.

### Closing a sprint

`worker/close`: the retrospective, one close across replicas, and at a first
close the procedures round (`worker/procedures_round`: keep or revert
procedures by the board's numbers) and the wiki compile (`wiki_close.py`, which
never raises).
`worker/close_listing` lets the closing thread read the project's listing
through the daemon for the follow-up plan (`bind` before the close, `current`
in `_plan_follow_up`; at most `LISTING_WAIT_SECONDS`, else the backend's own
listing).

Who closes: a worker approval closes its sprint on the spot, and the review
cycle (`worker/review._closing_candidates`) looks at every `active` sprint and
closes each one that has settled (`dependencies.sprint_settled`). That covers
a sprint whose last task a person moved to Done or Discarded on the board
(`PATCH /v1/backlog/{id}`), which does not close the sprint itself; it closes
within one cycle (issue #766). A paused, planned or cancelled sprint is not
`active` and is never closed this way.

### Elsewhere

`replicas.py` (claims, recovery, one close): [platform](platform.md).
`console_host.py` (the loop's side of a console producer):
[producers](producers.md).

## See also

- The task worker's steps: [the sprint loop](../SPRINT_LOOP.md).
- Settings (`PROMPT_BUDGET`, `MAX_REWORKS`, `DELIVERY_TESTS_ENABLED`, `RUN_WINDOW_RECHECK_SECONDS`): [configuration](../CONFIGURATION.md).
- Design: [segregation of duties](../archive/design/SEGREGATION_OF_DUTIES.md), [project wiki](../archive/design/PROJECT_WIKI.md), [sprint board](../archive/design/SPRINT_BOARD.md).

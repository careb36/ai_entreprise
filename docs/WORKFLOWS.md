# Workflows per board column: a design

> **Status: proposal, 2026-11-09. Not approved.** No product code changes
> until the owner approves this document. It answers the owner's request to
> adopt Aixle Flow's "workflow per board column" idea, and says how to do it
> without weakening the rules in [`POSITIONING.md`](POSITIONING.md): claims
> are never evidence, the Verifier and the numbers decide, and a person
> approves where the policy says so.

Flow (Aixle Flow) is Apache 2.0. It was read as data only: none of its code
was run. Paths below marked `flow:` are relative to the root of the Flow
repository; the others are relative to this repository.

## Summary

- Today the loop is one fixed sequence written in code: produce, deliver,
  Verifier, delivery gate, QA, holds.
- This document proposes to write that sequence down as data: a small DAG
  of typed steps. A company can bind such a DAG to a task kind or to a board
  column.
- The step types are a closed list in code (`produce`, `run_tests`,
  `verify`, `review`, `approve`, `integrate`, `publish`, `notify`). Each type
  declares, in code, which claims and which observations it consumes and
  produces. A workflow only places and configures steps. It cannot create a
  new step type.
- The rules do not live in the graph. They live in a validator that refuses
  a workflow which could break them, and in a terminal guard on the `done`
  transition that no workflow can remove. Autonomy, risk and the Manager
  policy are read there, the same way they are read today.
- Task events stay the only writer of a task's state. Steps emit events.
- Runs resume from Postgres, as the loop does today, without Temporal.
- Slice 1 adds the built-in default workflow, which equals today's loop, and
  changes no behaviour.

---

## 1. What Flow does

### 1.1 Workflow model: steps, edges, DAG, parallel steps, retries

- **A workflow** is a record scoped to a project, or a `System` record
  (`flow:app/models/workflow.rb`, table `workflows` in
  `flow:db/schema.rb`). It has many `steps` and many `runs`. It is versioned
  (`include Versioned`), and each `StepRun` records the version it ran
  (`workflow_version_id`, `flow:app/models/step_run.rb` and table
  `step_runs`). A workflow cannot be deleted while a run is live or a column
  is still bound to it (`soft_delete!` and `check_column_bindings`,
  `flow:app/models/workflow.rb:108` and `:271`).
- **A step** (`flow:app/models/step.rb`, table `steps`) has free-text
  `instructions`, an optional `agent`, a `preferred_model`, a
  `required_agent_runtime`, a `reasoning_effort`, tools, skills, MCP servers,
  and input and output asset specs (`input_asset_specs`,
  `output_asset_specs`).
- **Edges** are `depends_on_step_ids`, a JSON array on the dependent step.
  There is no separate edge table. When a step is saved,
  `depends_on_step_ids_valid` (`flow:app/models/step.rb:174`) refuses three
  things: a self-reference, an id that is not a sibling step, and a cycle,
  which it names along its path. A step that another step depends on cannot
  be deleted (`Step#destroy`).
- **Execution** is a Temporal workflow,
  `flow:app/temporal/workflows/workflow_execution_workflow.rb`.
  - `process_steps` (line 101) loops over `ready_steps` (line 131): the steps
    whose dependencies have all completed.
  - It runs all ready steps together with `execute_steps_parallel`
    (line 180). Parallelism is therefore implicit: two steps with no edge
    between them run at the same time. The join waits for every pending step
    (`wait_for_all_parallel`), and one failure fails the run.
  - When steps are left but none is ready, `unsatisfiable_dependencies!`
    (line 122) fails the run with that reason.
- **Retries and failure policy** are per step (`flow:app/models/step.rb:21-22`):
  - `on_failure` is one of `retry`, `skip` or `fail`, with `max_retries`.
  - `skip_policy` is one of `never`, `if_outputs_exist` or `manual`.
  - `recover_from_step_completion_failure` (`workflow_execution_workflow.rb:298`)
    counts failures per step and retries up to `max_retries`.
  - A retry is a new `StepRun` that carries `retry_count` and the earlier
    errors in `error_history` (`StepRun.next_attempt!`,
    `flow:app/models/step_run.rb`).
  - A step that failed on a provider quota is never retried
    (`@quota_error_step_run_ids`).
- **Sub-steps** (`flow:app/models/sub_step.rb`, `sub_step_runs`) are
  checklist items inside a step. The agent marks them itself
  (`flow:app/services/internal_tools/mark_sub_step.rb`).
- **How a step completes**
  (`flow:app/temporal/activities/workflow/complete_step_activity.rb`):
  - A step fails if its session ended `failed` or `cancelled`, or if a quota
    or authentication error was detected.
  - Otherwise the step's declared outputs are validated (`validate_outputs`),
    and if they pass the step is marked completed.
  - A non-interactive session ends when the agent calls `finish_session`
    (`flow:app/services/internal_tools/finish_session.rb`). The agent's
    report that it has finished is the completion signal. Output validation
    then checks only that the declared assets exist.

### 1.2 Column-to-workflow binding

- Every project has one board (`boards`, unique `project_id`). A board has
  ordered columns that people define (`board_columns`: `name`, `position`,
  `purpose`).
- `ColumnWorkflowBinding` (`flow:app/models/column_workflow_binding.rb`,
  table `column_workflow_bindings`) binds one workflow to one column. The
  column is unique (line 15). The binding also has a `trigger_mode` of `auto`
  or `manual` (default `manual`, line 13), a `cooldown_seconds` (default 5),
  and `created_by`. The workflow must be visible from the column's project.
- Moving a card calls `TaskService.check_auto_trigger`
  (`flow:app/services/task_service.rb:342`).
  `record_pending_auto_trigger` (line 379) applies three guards: auto mode,
  no pending gate on the task, and the cooldown. It then records a
  `board.column.auto_triggered` event inside the move's transaction.
- `TriggerEngine` (`flow:app/services/trigger_engine.rb`; see the header
  comment, `record_column_trigger` at line 173 and `fire_workflow` at
  line 212) dispatches the event through a transactional outbox. A
  `TriggerDispatch` ledger, unique on a dedup key, makes sure each event
  starts at most one run.
- Each move is kept as a `ColumnTransition`
  (`flow:app/models/column_transition.rb`). It records the from and to
  columns, the actor type (`human`, `agent` or `auto_trigger`) and the run
  the move started.

### 1.3 Persistence and resumption (Temporal)

- `flow:app/services/temporal_service.rb` connects the Rails app to a
  Temporal cluster and registers the workflows and activities under
  `flow:app/temporal/`.
- A `WorkflowRun` row (`workflow_runs`: `state`, `mode`, `relay_state`,
  `relay_attempts`, `stop_requested_at`) is created first. The Temporal
  execution starts only after that row commits. `WorkflowRunRelay`
  (`flow:app/services/workflow_run_relay.rb`) is an outbox relay: it claims
  stuck runs with `FOR UPDATE SKIP LOCKED` and starts them.
- Temporal's event history makes the run durable. Steps report back through
  signals (`step_completed`, `step_skipped`, `step_retried`,
  `container_finished`, `workflow_cancelled`). A run waits up to 23 hours
  for a decision (`INTERACTIVE_TIMEOUT`).
- Changing the workflow code needs replay-safe patches
  (`Temporalio::Workflow.patched(...)` at lines 73, 396 and 416, and
  `flow:docs/architecture/temporal-versioning.md`).
- `WorkflowExecutionWorkflowV2`
  (`flow:app/temporal/workflows/workflow_execution_workflow_v2.rb`) makes the
  **database the authority**. Signals only wake the run up. The run reads
  step and session state from Postgres with a backoff from 30 to 900 seconds
  (`await_session_change`), "because history is finite". A
  `StaleRunCleanupWorkflow` sweeps runs that stay `running` or `paused`.

### 1.4 How approvals are recorded

- A run's `mode` is `interactive`, `non_interactive` or `mixed`
  (`flow:app/models/workflow_run.rb:54`). A step may run without a person
  (`allow_non_interactive`, turned into `auto_run` in
  `flow:app/temporal/activities/workflow/prepare_step_list_activity.rb:17`).
- Otherwise the workflow waits in `wait_for_interactive_decision`
  (`workflow_execution_workflow.rb:255`), with the step run in
  `waiting_input`.
- "Approve" is `WorkflowService.approve_step`
  (`flow:app/services/workflow_service.rb:211`). It calls
  `step_run.mark_completed!` and sends the `step_completed` signal. It is
  reached from `approve_step` in
  `flow:app/controllers/web/company/projects/workflow_runs_controller.rb:61`
  and from `flow:app/services/personal_tools/approve_step_run.rb`.
- Authorization is `controllable?`
  (`flow:app/policies/web/company/projects/workflow_runs_policy.rb:11-26`):
  the run's owner, or someone who can control it.
- **No approver is stored on the step.** `step_runs` has no approver column
  (`flow:db/schema.rb`, table `step_runs`). The approval is a state change
  plus a signal. The activity feed records `workflow_started`, `_completed`,
  `_failed` and `_cancelled` (`WorkflowService.record_activity`), but not who
  approved a step.
- `Gate` (`flow:app/models/gate.rb`) is something else: a CI precondition on
  a task (GitHub checks, GitLab pipeline, Azure, Bitbucket), with
  `pending`, `resolved` or `stale` and a TTL. While a gate is pending, the
  column's auto-trigger does not fire. A `stale` gate is explicitly not a
  pass.

### 1.5 How a step's container is chosen

- `SessionService.build_for_workflow_step`
  (`flow:app/services/session_service.rb:179-219`) picks the agent runtime in
  this order: `step.required_agent_runtime`, then `workflow_run.agent_runtime`,
  then the member's default runtime, then the newest credential's type, then
  `claude_code`. The model is `step.preferred_model`, else the run's
  requested model.
- The image is derived from the runtime:
  `"#{agents.image_prefix}#{AgentRuntime.fetch(agent_type).image}:#{tag}"`,
  with an override per runtime (`resolve_image` in
  `flow:app/services/container_strategies/agent_base_strategy.rb:95-103`,
  and `agents:` in `flow:config/settings.yml:527-545`).
- Where the container runs is the run's `runner_id`. Null means hosted.
  Otherwise it is an external runner, checked by `Runners::Resolver.resolve!`
  (`flow:app/models/concerns/runner_reference.rb`,
  `flow:app/services/runners/resolver.rb:38`).
- `WorkflowStepStrategy`
  (`flow:app/services/container_strategies/workflow_step_strategy.rb`) puts
  the earlier steps' outputs into the container and collects the step's own
  outputs.

---

## 2. What La Fabrique has today

### 2.1 The loop as a fixed workflow

The task worker (`backend/app/services/task_worker.py`) runs this sequence
for every task. Steps 1–8 of "Data Flow for a Sprint" in `CLAUDE.md` describe
it in full.

| Phase | Where | Evidence kind |
|---|---|---|
| Claim | `replicas.claim_todo`, a conditional update from `todo`; `claimed` event | bookkeeping |
| Advice (optional) | console assistants (`collect_advice`), Architect brief (`_consult_architect`); `advised` | claims, prompt context only |
| Produce | `_execute_task`: model call with MCP, workspace and research tools | the answer is a claim; tool calls are observations |
| Text gate | `_validate_minimum_deliverable`, then `_send_back_from_quality_gate` | deterministic check on the claim's form |
| Deliver | `_deliver_artifacts`: `workspace.generate` on the daemon (or a queued job) | observation: sha256 read back, diff, snapshot or commit |
| Install, tests, application | daemon `workspace.execute` and `workspace.app.check` | observations: exit status, per-test lines, sandbox |
| Verify | `_record_task_run`, which writes an `AgentRun` and its `Verification` (`services/verification.py`) | verdict and checks, including `risk_tier` |
| Delivery gate | `delivery_gate.gate`: refuted, unconfirmed, failed tests, a failed or edited criterion, standards, secops | decides send-back or block, without a model |
| Review | QA in `_review_cycle_body`; only `APPROVED:` passes | claim; acts only after the gate |
| Holds | high-risk tier (`risk.is_high`), autonomy level 1 (`sprint_control.HUMAN_LED_REASON`), a browser check that was not observed (`person_acceptance_reason`) | decides `risk_hold`, which waits for a person |
| Close | `done`, or `blocked` with escalation to the ScrumMaster triage | events `reviewed`, `blocked`, `escalated`, `decided` |
| After done | sprint integration (`integration.schedule_after_close`), local merge, publish | observations: `SprintIntegration`, `RunPublication` |

The retry limits are constants:

- `AUTOPILOT_MAX_REWORK_CYCLES` for rework, from QA or from a gate
  (default `MAX_QA_REWORK_CYCLES = 2`).
- `MAX_QA_REVIEW_FAILURES = 3` for QA answers that cannot be used.
- After a limit, the task is blocked and escalated. It is never skipped.

### 2.2 How much of Flow's model already exists

| Flow concept | La Fabrique today | What is missing |
|---|---|---|
| A step DAG inside a run | The phases above, in code order | The order cannot be configured |
| `depends_on_step_ids` with a cycle check | **Between tasks**: `BacklogItem.depends_on`, `dependencies.find_cycle` / `check_positions`, `is_ready` | Nothing between steps |
| Parallel ready steps | Tasks without dependencies run in parallel; inside a task, nothing runs in parallel | Parallel steps inside a task |
| Retries with `max_retries` | Rework budgets per author (`record.rework_requests_by`), the QA failure cap, `retried` / `recovered` events | Limits per step, set by a company |
| Interactive approval step | Holds: `risk_hold`, the autonomy-1 hold, the inbox, `run_review` approve and request changes, and the Manager agent under `ManagerPolicy` | Only one place in the sequence can hold a task |
| `Gate` (a CI precondition) | Dependency holds (`Blocked: waits for task …`), `DELIVERY_QUEUED` (a daemon offline), the delivery gate | — |
| Run persisted and resumed | Postgres plus the JetStream `task.assign` durable consumer, the outbox, `replicas.task_lock` (an advisory lock as a lease), `recover_stale` each review cycle, the daemon's delivery journal | — |
| Approval recorded | A `TaskEvent` (`approved_by_person`, `decided`, a chained event with its actor), `RunReview`, `AgentRunApproval`, `ManagerDecision`, audit rows | — (stronger than Flow's) |
| Column binding | Fixed columns = `BacklogItem.status` (`todo`, `in_progress`, `in_review`, `done`, `blocked`). Moving a card to To do starts the task (`services/board_moves.py`) | A workflow chosen per column or per kind |
| Container per step | One execution environment per company: the daemon and `Company.delivery_sandbox` (host, container or bwrap), the stack profile, the command policy | — (deliberately not per step; see §8) |
| Definition versioning, pinned on the run | Procedures and specifications are versioned; the loop is not | A workflow version pinned on each task's run |

So La Fabrique already has most of what makes Flow work. It has edges
(between tasks), approval gates (the holds), retries (the rework budgets),
durable resumption and an approval record. What it lacks is the ability to
**configure the order and the set of steps inside a task**.

---

## 3. The proposed model

### 3.1 A workflow is a small DAG of typed steps

A workflow definition is a JSON document with a name, a version and a list
of steps. Each step has:

- a `key`, unique within the definition;
- a `type`, from the closed list in §3.2;
- `after`: the keys it depends on;
- `config`: only the fields its type allows.

```json
{
  "name": "fabrique.default",
  "version": 1,
  "applies_to": {"kind": null},
  "steps": [
    {"key": "produce",  "type": "produce",   "after": [],           "config": {"role": "Developer", "producer": "llm"}},
    {"key": "tests",    "type": "run_tests", "after": ["produce"],  "config": {"install": "profile", "app_check": "deployment"}},
    {"key": "verify",   "type": "verify",    "after": ["tests"]},
    {"key": "qa",       "type": "review",    "after": ["verify"],   "config": {"role": "QA"}},
    {"key": "accept",   "type": "approve",   "after": ["qa"],       "config": {"when": "guard"}}
  ],
  "limits": {"rework": 2, "review_failures": 3}
}
```

The graph is acyclic. **Rework is not an edge.** A step that sends a task
back (`verify` through its gate, or `review`) ends the current **attempt**.
The run then starts a new attempt at the nearest upstream `produce`, and that
attempt counts against the produce step's rework budget. This works like
Flow's retry, which creates a new `StepRun`. The definition stays a DAG, and
the loop stays bounded.

Every workflow ends in an implicit **terminal guard**, `close`. A company
cannot place it, configure it or remove it (§4).

### 3.2 Step types and their evidence

The types are a closed list in code (a registry, `services/workflow_steps.py`
in slice 1). Each type declares, in code, what it consumes and what it
produces, and whether each item is a **claim** or an **observation**. A
workflow cannot change these declarations.

| Type | Actor | Consumes | Produces | May decide |
|---|---|---|---|---|
| `produce` | a role (Developer, QA, TechWriter); `producer: llm` or `console` (a terminal assistant on the daemon, slice 7) | task, covered requirements, architecture, `NEEDS WORK:` feedback, procedures, lessons (all prompt context) | **claims**: answer text, declared files, summary. **Observations**, recorded by the backend: tool calls, workspace reads and edits, the daemon's write reply (sha256 read back, diff, snapshot or commit) | nothing; the text gate may send the answer back on its form, as today |
| `run_tests` | the daemon | the worktree as written (observation) | **observations**: install exit status, test exit status and per-test lines, criterion results, application start and e2e, the sandbox report | nothing by itself; `verify` reads it |
| `verify` | the Verifier (deterministic) | every observation of the attempt; the claims, labelled as claims | **observation**: the `Verification` (verdict `verified` / `refuted` / `unverifiable`, checks, `risk_tier`) | the delivery gate: send back, or block after the limit |
| `review` | a role (QA by default; SecOps or a second reviewer allowed) | the digest of the delivery and the Verification | **claim**: `APPROVED:` or `NEEDS WORK:` with a reason | send back; it never closes a task alone |
| `approve` | `person`, or `manager_agent` under the company's `ManagerPolicy` | the facts `manager_inbox.run_facts` reads | a person's decision event, or a `ManagerDecision` | accept or send back |
| `integrate` | the daemon (sprint scope; see the open questions) | the done tasks' branches | **observation**: `SprintIntegration` | nothing about a task |
| `publish` | the daemon, with a person's or the Manager agent's `publish` rule | a branch, its evidence | **observation**: `RunPublication` | nothing about a task |
| `notify` | the backend's webhook outbox | an event | a delivery record (`last_status`), which is not evidence | nothing; a failure never blocks |

Rules that follow from the registry:

- No type writes a `Verification` except `verify`. No type writes an
  observation field from a claim.
- There is no "script" or "custom" type. A step that ran arbitrary code
  could write anything into the evidence.
- `review` and `approve` outcomes are claims or decisions, kept apart from
  the evidence, as they are today (`RunReview`, `ManagerDecision`, events).

### 3.3 Parallel steps

Two steps with no path between them may run in parallel, as in Flow. But a
step type may run in parallel only when its declared effect allows it:

- **Read-only steps may run in parallel:** `review` (two reviewers, for
  example QA and SecOps) and `notify`.
- **Steps that write to the worktree or run code in it never run in
  parallel inside one task:** `produce` and `run_tests`. The daemon's
  project lock (`vscode-daemon/project_lock.py`) would serialize them anyway,
  and a test run must see one fixed set of bytes.
- **Join:** a join waits for all its branches. Any `NEEDS WORK:` or any
  refutation from any branch sends the task back. One reviewer's approval
  never outweighs another's request for work.

### 3.4 Retries with limits

- Each `produce` step has a **rework budget** (`limits.rework`, today 2). A
  company may lower it, or raise it up to a ceiling fixed in code (proposed:
  5).
- Each `review` step has a budget for **unusable answers** (today 3), with
  the same kind of ceiling.
- Infrastructure retries (a daemon that is not there, a lost lease) are not
  counted against these budgets. They stay `DELIVERY_QUEUED` and `recovered`,
  as today.
- When a budget is spent, the outcome is always **block and escalate** to
  the ScrumMaster triage, as today. No step type offers `skip` on failure.
  The single exception is `notify`: its failure is recorded and never affects
  the task.

### 3.5 Binding: column and kind

La Fabrique's columns are task statuses, not boards that people draw. The
settlement rules, the inbox and the history checker read those statuses. So
a binding names a **lifecycle point** and, optionally, a **task kind**:

| Binding point | When it runs | Default |
|---|---|---|
| `todo` (the work column) | when a task is dispatched: the start, the dispatch, or a person moving the card to To do (`board_moves.start_moved_task`) | today's loop |
| `done` (after done) | once a task closes `done` | nothing (today, merge and publish are a Manager's or a policy's action) |
| `kind` | `BacklogItem.kind`, for example `acceptance_tests` | QA's test-writing variant of today's loop |

A workflow is resolved, most specific first, from:

1. the company's binding for `(column, kind)`;
2. the company's binding for `(column, any kind)`;
3. the built-in workflow for the kind;
4. the built-in default.

A company with no binding runs the built-in default, which equals today's
loop step for step. **Nothing changes unless a company configures
something.**

A run pins the definition's id, version and server-computed sha256 when it
starts, as Flow does with `step_runs.workflow_version_id`. Editing a
workflow never changes a task that is already running.

---

## 4. Invariants a workflow can never break

These are enforced in two places: the **validator**, when a definition is
saved or activated, and the **engine and terminal guard**, at run time. The
run-time check makes sure that a validator bug, or a definition written
behind the API, still cannot break a rule.

| # | Invariant | Validator | Run time |
|---|---|---|---|
| I1 | **`verify` follows every `produce`.** | On every path from a `produce` to anything that is not `run_tests`, there is a `verify`. Only `run_tests` may sit between them. `verify` has no config that turns it off, no skip and no failure policy. | The engine refuses to start a step that consumes the attempt's deliverable, other than `run_tests`, unless a `Verification` exists for the attempt's `AgentRun`. `close` refuses `done` without one. |
| I2 | **High risk always gets a person.** A delivery whose `risk_tier` is high, or whose browser check was not observed, closes only by a person's decision, or by the strict `accept_high_risk` rule that a person enabled, exactly as today. | Not a graph property, so it cannot be configured away. The validator only refuses an `approve` step whose actor setting would claim to bypass it. | `close` reads `risk.is_high` and `sprint_control.person_acceptance_reason` from the latest Verification, whatever steps ran before. A workflow may add `approve` steps. It never removes this hold. |
| I3 | **The autonomy level still applies.** Level 1 holds every approved task; level 3 runs follow-ups and procedures on the company's own. | No workflow field reads or sets autonomy. | `close` calls `sprint_control.needs_person_acceptance`, as `_review_cycle_body` does today. |
| I4 | **The confirmation set stays.** | `approve` with `manager_agent` names no rule. It means "the company's `ManagerPolicy`", and the validator refuses any rule list, any `ALWAYS_ESCALATE` category (`cancel_sprint`, `delete_sprint`, `change_autonomy`, `spend_above_budget`) and any type that would act on one. **Proposed:** add `change_workflow` to `ALWAYS_ESCALATE`, so the Manager agent can never edit or activate a workflow. | The Manager agent decides through `manager_policy.evaluate` and `manager_actions.act`, which are unchanged. A workflow grants no rule. |
| I5 | **A step cannot mark a claim as evidence.** | The registry fixes each type's consumed and produced kinds, and `config` is checked against a schema per type with no free-form mapping. A step whose input is declared as an observation can only follow a type that produces one. | Only `verification.py` writes a `Verification`. Claims and observations keep their separate fields on `AgentRun`. Tests assert that no step handler writes evidence from a claim. |
| I6 | **The history checker still holds.** | A property test generates valid workflows and random step outcomes, runs them through the engine against SQLite, and runs `app/history_check.py` on every history produced. | Every state change goes through `task_events.record`, with the existing kinds and actors. `history_check.py` keeps importing no production reader. It gains its own independent rules for the new payload fields (step key, attempt, run id). The e2e keeps running it. |
| I7 | **A task always settles.** | The graph is acyclic, has at least one `produce` and one `verify`, every step is reachable, and every budget is finite and within its ceiling. | An attempt that cannot proceed (unsatisfiable, as in Flow's `unsatisfiable_dependencies!`) blocks the task for a person. The sprint can still close. |
| I8 | **Dependencies between tasks are unchanged.** | Workflows have no field that touches `depends_on`. | `dependencies.is_ready` and `settle_active` gate dispatch before any workflow starts. |

Each invariant gets a named refusal reason (the same style as `spec_check`
and `arch_check`). `POST /v1/workflows/{id}/validate` returns them, and
activation is a 409 while any of them blocks.

---

## 5. Storage and API

### 5.1 Tables (one migration per slice, written as `CONTRIBUTING.md` §5 says)

Every table carries a required `company_id`, as `test_tenant_scoping.py`
requires.

- **`workflow_definitions`**: `id`, `company_id`, `name`, `version`,
  `status` (`draft` / `active` / `retired`), `graph` (JSON), `graph_sha256`
  (computed by the server from the canonical JSON, never sent by the client),
  `created_by`, `activated_by`, `activated_at`, `supersedes_id`. The pair
  `(company_id, name, version)` is unique. An active version is never edited.
  `/revise` opens the next version, as specifications do.
- **`workflow_bindings`**: `id`, `company_id`, `column` (`todo` / `done`),
  `kind` (nullable), `definition_id`, `created_by`, `created_at`. The triple
  `(company_id, column, kind)` is unique. A definition cannot be retired
  while a binding names it, as in Flow's `check_column_bindings`.
- **`task_workflow_runs`**: `id`, `company_id`, `backlog_item_id`,
  `definition_id`, `version`, `graph_sha256` (pinned at start), `state`
  (`running` / `waiting_person` / `settled`), `attempt`, `started_at`,
  `settled_at`.
- **`task_step_runs`**: `id`, `company_id`, `run_id`, `step_key`, `attempt`,
  `state` (`pending` / `running` / `waiting_person` / `passed` /
  `sent_back` / `failed` / `blocked`), `started_at`, `finished_at`,
  `agent_run_id`, `verification_id`, `first_event_id`, `last_event_id`,
  `error`. The pair `(run_id, step_key, attempt)` is unique, so a step
  attempt is inserted once, however many replicas try.

### 5.2 Relation to `task_events`: the events stay the single writer

- The **task's state** (status, thread, verdicts, holds) is written only by
  `task_events.record`, as today. The step rows **never** set a task's
  status.
- A step that ends calls one function, `workflow_engine.finish_step`. In the
  same transaction it:
  - records the task event the outcome calls for (`delivered`, `verified`,
    `reviewed`, `risk_hold`, `blocked` …), with `payload.step = {run_id,
    key, attempt}`;
  - updates the step row and stores the event's id on it.
- The step rows are the run's **scheduling state**: what is ready, which
  attempt this is, who holds the lease. Everything they say about outcomes
  can be checked against the events. The history checker can therefore
  refuse a step row whose outcome has no matching event, or an event that
  names a step the run does not have.
- **New event kinds are avoided.** Steps reuse today's kinds, and starting a
  step is not an event. This keeps `history_check.py`, the projection
  (`task_projection`) and the migration `0014` backfill valid without
  changes. If a later step type needs a kind of its own, that kind goes into
  `task_events.KINDS` and the checker in the same pull request.

### 5.3 Resumption after a restart, without Temporal

What we would use is what the loop already uses:

- **Postgres is the authority.** A step attempt is a row. It moves forward
  through conditional updates (`UPDATE … WHERE state = 'pending'`), which is
  the `replicas.claim_todo` pattern.
- **Leases are advisory locks.** A replica holds
  `locks.held(engine, locks.WORKFLOW_STEP, step_run_id)` while it works on
  a step, as `replicas.task_lock` does for a task today. When a replica dies,
  its lock is released with its connection.
- **Recovery** extends `replicas.recover_stale`, which runs at the start of
  every review cycle. A `running` step attempt that nobody holds goes back to
  `pending` if its type is idempotent: `produce` (a new attempt, the daemon's
  journal replays a write that already happened), `run_tests`, `verify` and
  `review`. If its type has side effects (`publish`, `integrate`), it is
  first **read back**: the `RunPublication`, the `SprintIntegration` row and
  the daemon's answer are checked, never assumed. This is what
  `integration_jobs` already does for `running` rows.
- **Wake-ups** are the JetStream `task.assign` durable consumer (for
  `produce`), the outbox (for side effects) and the review cycle (for
  everything else). A step waiting for a person is a row in
  `waiting_person`, shown in the inbox. It is not a timer.

**Why not Temporal:**

- It is a new cluster to run, upgrade and back up, in a product that
  companies host themselves.
- Workflow code must replay deterministically. Flow's history shows the cost:
  a `patched(...)` branch for every behaviour change, and a document on how
  to retire them (`flow:docs/architecture/temporal-versioning.md`).
- It gives two sources of truth. Flow's own V2 workflow moved the authority
  back to the database and demoted signals to wake-ups
  (`workflow_execution_workflow_v2.rb`, header comment). We would start where
  they ended.
- Our evidence has to sit in one place: the audit hash chain and the
  per-task event chain. A Temporal history would be a second record of what
  happened, outside both chains.
- Our steps are short, or they wait for a person, which is a row. Neither
  needs a durable timer service.

### 5.4 API and permissions

All routes are company-scoped. A **Viewer** reads, a **Manager** writes, an
API token acts with its role, and the Manager agent cannot write (I4).

| Route | Who | What |
|---|---|---|
| `GET /v1/companies/{id}/workflows` | Viewer | definitions with status and version, and the built-in ones (read-only) |
| `POST /v1/companies/{id}/workflows` | Manager | create a draft |
| `GET /v1/workflows/{id}` | Viewer | the definition and the validator's findings |
| `PATCH /v1/workflows/{id}` | Manager | edit a draft (an active version is a 409) |
| `POST /v1/workflows/{id}/validate` | Viewer | the findings, without saving |
| `POST /v1/workflows/{id}/activate` | Manager | 409 while a finding blocks; audited as `workflow_activated` with the sha256 |
| `POST /v1/workflows/{id}/revise` / `retire` | Manager | the next draft / retire (a 409 while bound) |
| `GET` / `PUT /v1/companies/{id}/workflow_bindings` | Viewer / Manager | the bindings, audited as `workflow_binding_changed` with before and after |
| `GET /v1/tasks/{id}/workflow` | Viewer | the task's run: steps, attempts, states, the events and the runs and verifications behind each step |
| `POST /v1/tasks/{id}/workflow/steps/{key}/retry` | Manager | retry a `failed` infrastructure step (not a spent budget) |

There is **no new approval endpoint.** Approvals keep going through the
inbox's accept, `run_review` and the Manager agent's actions, so they are
recorded exactly as today, with the actor in a chained event.

---

## 6. UI sketch

**Company → Workflows** (`/company/workflows`; a new page, every string in
`en.json` and `es.json`):

```
Workflows                                              [New workflow]
┌───────────────────────────────────────────────────────────────────┐
│ fabrique.default  v1  built-in        bound: To do (any kind)     │
│ two-reviewers     v3  active          bound: To do (kind: none)   │
│ two-reviewers     v4  draft  ⚠ 1 finding                          │
└───────────────────────────────────────────────────────────────────┘

two-reviewers v4 (draft)
  1  produce      Developer · LLM                 rework ≤ 2
  2  run_tests    install: profile · app: off
  3  verify       Verifier                        🔒 always present
  4  review       QA            ┐ in parallel
  5  review       SecOps        ┘ any NEEDS WORK sends back
  6  approve      person
  ·  close        guard: risk tier, autonomy      🔒 not editable
Findings:
  ✖ I1  step "lint" (run_tests) after review: tests must come before verify
[Validate] [Activate]   (Viewer: read-only, no buttons)
```

The editor is an ordered list with `after` pickers, not a free canvas. The
graphs are small, and a list shows the invariants better. The locked rows
cannot be dragged or deleted.

**Board:** each column header shows its binding as a chip
(`To do · two-reviewers v3`). Each card shows a strip of step dots for the
current attempt (`● ● ● ◐ ○`), plus the attempt number when it is above 1.

**Task page → Workflow tab:** each step with its state, attempt, start and
end. Each step links to its `AgentRun`, its `Verification` and its events.
An approval shows who decided (person, or Manager agent with the rule). A
step waiting for a person links to the inbox item.

---

## 7. Migration path in slices

Each slice can ship on its own, with its own tests, and leaves the product
working.

1. **The default workflow as data, with no behaviour change.**
   - The step registry (types, evidence declarations, config schemas) and
     the validator, in code.
   - `fabrique.default.v1` and the `acceptance_tests` variant, as built-in
     definitions in code.
   - A read-only projection that maps a task's existing events to the
     default workflow's steps.
   - `GET /v1/tasks/{id}/workflow` and the task page's Workflow tab.
   - No table, and the worker is untouched.
   - Tests: the validator passes the built-ins and refuses one example of
     each invariant; the projection matches the events of every outcome in
     `test_review_gate.py` and `test_task_events.py`.
2. **Definitions and validation.**
   - The `workflow_definitions` table, the CRUD and validate routes, and the
     Workflows page.
   - Drafts can be created and activated, but **bindings are not read** by
     the worker yet. Activation is audited.
   - `change_workflow` is added to `ALWAYS_ESCALATE`.
3. **The engine runs the default workflow.**
   - The `task_workflow_runs` and `task_step_runs` tables, with leases and
     recovery.
   - The worker's phases become step handlers, called by the engine with
     the built-in default.
   - Behaviour must be identical. The e2e's event sequences are compared
     before and after (a golden file per task), `history_check.py` runs
     unchanged, and the replica tests cover step leases.
4. **Bindings with knobs inside today's shape.**
   - `workflow_bindings`, resolved by column and kind.
   - Configurable budgets within the ceilings.
   - Add-only steps: a second `review` (in sequence), a person's `approve`,
     and `notify`.
   - The property test of I6.
5. **Parallel steps.** Parallel `review` branches and the join rule (§3.3).
6. **After-done workflows** bound to `done`: `publish`, local merge and
   `notify` as steps. Each of them still acts only through a person or the
   Manager agent's existing rules.
7. **Console producer.** `produce` with `producer: console` (a terminal
   assistant on the daemon writes the files). Its output goes through the
   same `verify`.

Slices 1–3 change nothing a company can see, except the read-only Workflow
tab. Slice 4 is the first slice that lets a company change its loop.

---

## 8. Risks, and what we explicitly do not take from Flow

### 8.1 Risks

- **Laundering claims through configuration.** If step types can be
  extended, one of them will eventually write evidence. Mitigations: a
  closed registry, no script step, invariant I5 and its tests.
- **The guard drifting from the graph.** If the graph and `close` disagree,
  a person could see "approve" in the workflow while `close` holds the task
  for another reason. Mitigation: the UI shows the guard's reason, which is
  read from the same function (`person_acceptance_reason`).
- **Two states.** The step rows and the events could disagree. Mitigation:
  one writer (`finish_step`) per transaction, and the history checker
  cross-checks them.
- **Numbers that cannot be compared.** Rework per task, the blocked share
  and procedure measurements mean something else under another workflow.
  Mitigation: store the workflow version on each run and each sprint's
  numbers, and compare only like with like (see the open questions).
- **The cost of slice 3.** Rewriting `_review_cycle_body` as step handlers
  touches the most tested and most load-bearing file. Mitigation: no
  behaviour change, and the golden event sequences.
- **Scope creep toward a general automation engine,** which `POSITIONING.md`
  says is not the product.

### 8.2 Not taken from Flow

| Flow feature | Why not |
|---|---|
| Temporal | §5.3 |
| Completion by the agent's own `finish_session` and its sub-step marks | An agent's report that it is finished is a claim (rule 2). Our steps end on observations. |
| `on_failure: skip` and `skip_policy: if_outputs_exist / manual` | A skip is a way around `verify` or a hold. Only `notify` may fail without effect. |
| Approval as a state change plus a signal, with no approver stored | Our approvals are chained events with an actor, and they stay that way. |
| `interactive` mode, where a person advances each step | A person decides at holds, not at every step. |
| A container, runtime and model per step (`required_agent_runtime`, `preferred_model`, per-runtime images) | The execution environment is the company's: daemon, sandbox, command policy, stack profile. A step must not pick a weaker sandbox. Models stay the company's per role (`company_models`). |
| Free-text `instructions` per step | That is what procedures are for. Procedures are versioned and measured. |
| Assets passed between steps | Our steps share the worktree and the evidence, both observed. |
| Chat, webhook and scheduled triggers, and `System`-scoped workflows | Out of scope. |
| An agent that writes workflows (Aixle Builder) | The loop's rules would then be edited by the party the rules check. |

What we **do** take, and will name in the pull requests:

- versioned definitions, with the version pinned on each run;
- `after` edges with a cycle check that names the path;
- ready-set scheduling, and an unsatisfiable graph treated as a failure;
- a retry as a new attempt that carries its count and the earlier errors;
- one binding per column, and a refusal to retire a definition that is
  still bound;
- the database as the authority, with wake-ups that are not verdicts
  (Flow's V2).

---

## 9. Open questions for the owner

1. **Can `review` (QA) be removed?** The proposal keeps at least one
   `review` in every workflow. `POSITIONING.md` says a green test outweighs
   a reviewer's `APPROVED`, so a company might want "tests and Verifier
   only" for some kinds. Is that allowed, and from which slice?
2. **Who activates a workflow: a Manager or the Owner?** The proposal says a
   Manager. Activating a workflow changes the loop for every future task,
   which is closer to changing autonomy (Owner-level in spirit).
3. **Columns people define.** Should La Fabrique ever have columns beyond
   the five statuses, as Flow does? The proposal says no: bindings attach to
   `todo`, `done` and kinds.
4. **Measuring workflows like procedures.** Should a workflow version be
   kept or reverted by the board's numbers after two sprints? And may the
   ScrumMaster *propose* workflow changes at retrospectives, adopted only by
   a person?
5. **Ceilings.** Are 5 for rework and 5 for unusable reviews the right hard
   limits?
6. **Running tasks.** Should a newly activated version apply only to tasks
   that start afterwards (proposed), or also to waiting tasks at their next
   attempt?
7. **`integrate`.** Integration is a sprint-level step. Should it be part of
   a separate sprint workflow, bound to the sprint's close, rather than a
   task step?
8. **Outside producers.** Should runs ingested from other producers (the
   Reviewer agent's queue) also get a configurable workflow, or stay out of
   scope?
9. **The `approve` actor.** Is "the company's `ManagerPolicy`, unchanged"
   the only way the Manager agent may appear in a workflow? Or may a
   workflow *narrow* the policy for one step, for example "a person only,
   even if a rule would allow the agent"? Narrowing is safe, widening is
   not.

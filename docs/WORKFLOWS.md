# Workflows per board column: a design

> **Status: proposal, 2026-11-09. Not built.** The owner's decisions are in
> [§9](#9-decisions) and applied throughout. Code ships in slices (§7).

The goal: let a company configure the steps a task goes through, per board
column, task kind or sprint close, without weakening the rules in
[`POSITIONING.md`](POSITIONING.md). Claims are never evidence, the Verifier
and the numbers decide, and a person approves where the policy says so.

The idea comes from Aixle Flow (Apache 2.0), read as data only; none of its
code was run.

## Summary

- Today the loop is one fixed sequence in code. The design writes it as
  data: a small DAG of typed steps, bound to a board column, a task kind or
  the sprint's close.
- Step types are a closed list in code. The rules live in a validator and
  in a terminal guard on `done`, not in the graph. `verify` and the guard
  are mandatory; QA's `review` is optional.
- Only an Owner activates a workflow. Custom columns are views over the five
  statuses. Each version is measured and reverts if clearly worse.
- With no configuration, the built-in default equals today's loop.

## 1. What is taken from Flow

Taken, and to be named in the pull requests:

- versioned definitions, with the version pinned on each run;
- `after` edges with a cycle check that names the path;
- ready-set scheduling, with an unsatisfiable graph treated as a failure;
- a retry as a new attempt that carries its count and earlier errors;
- one binding per column, and no retiring a definition that is still bound;
- the database as the authority, with wake-ups that are not verdicts.

Not taken: see §8.

## 2. What La Fabrique has today

The task worker (`services/loop/task_worker.py`; `docs/SPRINT_LOOP.md`)
runs claim, optional Architect advice, produce, text gate, deliver, install
and tests on the daemon, verify, delivery gate, QA review, holds, close,
and after done the sprint integration, local merge and publish.

It already has most of Flow's model: edges between tasks
(`BacklogItem.depends_on`), approval gates (the holds), retries (rework
budgets), durable resumption (Postgres, JetStream, advisory locks,
`recover_stale`) and an approval record (chained `TaskEvent`s with an
actor). What it lacks is a way to **configure the order and set of steps
inside a task**.

## 3. The proposed model

### 3.1 A workflow is a small DAG of typed steps

A definition is JSON: a name, a version and a list of steps. Each step has a
`key`, a `type`, `after` (the keys it depends on) and a `config` limited to
the fields its type allows.

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

**Rework is not an edge.** A send-back ends the current attempt. A new
attempt starts at the nearest upstream `produce` and counts against its
budget. The graph stays acyclic and the loop stays bounded.

Every workflow ends in an implicit terminal guard, `close`. A company cannot
place, configure or remove it.

| Lifecycle | Allowed types | Required | May change completion |
|---|---|---|---|
| work (`todo` column, kind) | `produce`, `run_tests`, `verify`, `review`, `approve`, `notify` | `produce`, `verify` | only through `close` |
| after done (`done` column) | `publish`, `notify` (local merge in slice 8) | none | never |
| sprint close | `integrate`, `publish`, `notify` | `integrate` | never |

In a work workflow, `review` and `approve` are optional. `run_tests` is
required while the company runs delivery tests; the validator adds it back.
After-done and sprint workflows consume a run already accepted. Their
failures are recorded on their own rows and never move a task out of
`done`.

### 3.2 Step types and their evidence

Each type declares in code what it consumes and produces, and whether each
item is a claim or an observation. A workflow cannot change this.

| Type | Actor | Produces | May decide |
|---|---|---|---|
| `produce` | a role; `producer: llm` or `console` | claims (answer, files, summary); observations recorded by the backend (tool calls, the daemon's write reply) | nothing; the text gate may send back |
| `run_tests` | the daemon | observations: install and test results, criteria, app check, sandbox report | nothing |
| `verify` | the Verifier | the `Verification` (verdict, checks, `risk_tier`) | the delivery gate: send back or block |
| `review` | a role (QA by default) | claim: `APPROVED:` or `NEEDS WORK:` | send back; never closes alone |
| `approve` | a person, or the Manager agent under a narrowed policy | a decision event or `ManagerDecision` | accept or send back |
| `integrate` | the daemon | `SprintIntegration` | nothing about a task |
| `publish` | the daemon, under a `publish` rule | `RunPublication` | nothing about a task |
| `notify` | the webhook outbox | a delivery record, not evidence | nothing; never blocks |

Only `verify` writes a `Verification`. No type writes an observation from a
claim. There is no script or custom type.

### 3.3 Parallel steps

Two steps with no path between them may run in parallel, if their type
allows it. `review` and `notify` are read-only and may. `produce` and
`run_tests` write to or run in the worktree and never run in parallel within
a task. A join waits for every branch; any `NEEDS WORK:` or refutation sends
the task back.

### 3.4 Retries with limits

- Defaults equal today's: rework **2** per counter
  (`AUTOPILOT_MAX_REWORK_CYCLES`, default `MAX_QA_REWORK_CYCLES = 2`), and
  **3** unusable answers per `review` (`MAX_QA_REVIEW_FAILURES`).
- Rework counts per source, as today: the text gate, the delivery gate and
  each reviewer have their own counter. A shared budget
  (`limits.rework_counting: "shared"`) is opt-in per workflow.
- A hard ceiling of **5** per budget, fixed in code. A company may lower a
  budget (to 0 for rework) or raise it to the ceiling.
- Infrastructure retries (daemon absent, lost lease) do not count.
- A spent budget always blocks and escalates to the ScrumMaster triage. No
  type may skip on failure, except `notify`, whose failure never affects the
  task.

### 3.5 Binding: columns, kinds and the sprint's close

**Custom columns.** A company may add columns such as "Design review". Each
maps to **exactly one** of the six statuses (`todo`, `in_progress`,
`in_review`, `done`, `blocked`, `discarded`), so a column is a view over a
status:

- Task events, the history checker, dependencies, the inbox and sprint
  closing read only the six statuses.
- A move between columns of one status changes no status and records no
  event. A move to another status's column is today's status move, with its
  rules.
- A custom column is a saved view
  ([`design/SAVED_VIEWS.md`](archive/design/SAVED_VIEWS.md)): a filter on its status,
  optionally refined, evaluated from the task projections.
- A column a binding names cannot be deleted until the binding is removed.

**Bindings.** A binding names a column and, optionally, a task kind:

| Binding point | Runs when | Default |
|---|---|---|
| a `todo` column | a task is dispatched or moved there | today's loop |
| a `done` column | a task closes `done` and lands there | nothing |
| `kind` | `BacklogItem.kind`, e.g. `acceptance_tests` | QA's test-writing variant |
| sprint close | the sprint closes | `integrate`, as today |

Only `todo` and `done` columns take a task workflow. A task workflow
resolves, most specific first, from the binding for `(column, kind)`, then
`(column, any kind)`, then (for `todo` only) the built-in for the kind, then
the built-in default. An unbound `done` column runs nothing.

**Versions and running tasks.** A run pins the definition's id, version and
server-computed sha256 when it starts. A new version applies to tasks
started after activation. A task in progress, including a waiting task that
is re-attempted, keeps its pinned version until it settles. A task carried
into another sprint is a new task. A sprint workflow pins its version at
close.

### 3.6 Measuring versions, proposals and reverts

Versions are measured as procedures are (`services/learning/procedures.py`),
by the board's numbers, never by a model:

- **Baseline.** On activation, the numbers of the last two completed
  sprints.
- **Measurement.** After two completed sprints of tasks pinned to the
  version: rework per task, blocked share, and first-attempt share.
- **Verdict.** `reverted` when rework per task rises by more than 0.25, the
  blocked share by more than 0.10, or the first-attempt share falls by more
  than 0.10. Otherwise `kept`. With fewer than two sprints, `unmeasured`.
- **Revert.** The platform reactivates the version it replaced, audited as
  `workflow_reverted`. It never activates anything the Owner had not
  activated before.
- **Proposals.** At a retrospective the ScrumMaster may propose a change in
  a fixed format. It becomes a draft in the inbox. Only an Owner activates
  it, at every autonomy level. A draft with the same sha256 as a reverted or
  rejected version is dropped.

The sprint workflow is measured on its integration outcomes. Its first
version is the built-in default.

## 4. Invariants a workflow can never break

Each is enforced twice: by the validator when a definition is saved or
activated, and by the engine and terminal guard at run time. Each has a
named refusal reason; `POST /v1/workflows/{id}/validate` returns them, and
activation is a 409 while any blocks.

| # | Invariant | How it holds |
|---|---|---|
| I1 | `verify` follows every `produce`. | Only `run_tests` may sit between them; `verify` cannot be turned off or skipped. At run time no step consumes the deliverable without a `Verification`, and `close` refuses `done` without one. |
| I2 | High risk always gets a person. | `close` reads `risk.is_high` and `person_acceptance_reason` from the latest Verification, whatever ran before. A workflow may add `approve` steps, never remove this hold. |
| I3 | The autonomy level still applies. | No workflow field reads or sets autonomy; `close` calls `sprint_control.needs_person_acceptance`. |
| I4 | Activation is the Owner's. | `change_workflow` joins `ALWAYS_ESCALATE`. The activate, deactivate and binding routes require the Owner role, so neither an API token nor the Manager agent can activate. |
| I5 | A step cannot mark a claim as evidence. | The registry fixes each type's inputs and outputs; `config` is schema-checked. Only `verification.py` writes a `Verification`. |
| I6 | The history checker still holds. | Every state change goes through `task_events.record`. `history_check.py` gains independent rules for the new payload fields and for `closed_on_evidence`. A property test runs random workflows through it. |
| I7 | A task always settles. | The graph is acyclic, every step reachable, every budget at most 5. An attempt that cannot proceed blocks for a person. |
| I8 | Dependencies between tasks are unchanged. | No workflow field touches `depends_on`; dispatch is gated before any workflow starts. |
| I9 | A step may narrow the Manager policy, never widen it. | `approve` takes `person_only` or a subset of the policy's rules. At run time only the intersection with the current policy applies. |
| I10 | No task closes on a claim. | `close` requires the latest Verification to be `verified`. Without `review`, the guard still decides whether a person must accept. |
| I11 | Custom columns are views over a status. | Bindings name only `todo` or `done` columns (or the sprint's close). Loop code reads only `BacklogItem.status`. |

## 5. Storage, resumption and API

**Tables**, each with a required `company_id`:

- `workflow_definitions`: name, version, scope (`task` / `sprint`), status
  (`draft` / `active` / `retired` / `reverted` / `rejected`), graph,
  server-computed `graph_sha256`, who created, proposed, activated and
  deactivated it, `supersedes_id`, and the measurement. An active version is
  never edited; `/revise` opens the next.
- `board_columns`: custom columns, each with one status.
  `BacklogItem.board_column_id` must match the card's status.
- `workflow_bindings`: point, column, optional landing column and kind,
  definition. Uniqueness uses two partial unique indexes, since NULL kinds
  would defeat a composite key.
- `task_workflow_runs`, `sprint_workflow_runs` and `task_step_runs`: the
  pinned version, state, attempt and timestamps. `(run_id, step_key,
  attempt)` is unique.

**Events stay the single writer.** Step rows are scheduling state and never
set a task's status. A finished step calls `workflow_engine.finish_step`,
which records the task event and updates the step row in one transaction.
The only new event kind is `closed_on_evidence` (actor `system`), for a task
closed without `review` on a verified run.

**Resumption without Temporal.** Postgres is the authority. Step attempts
advance through conditional updates; leases are advisory locks;
`recover_stale` resets idempotent steps (`run_tests`, `verify`, `review`)
and reads back side-effecting ones (`produce`, `publish`, `integrate`).
Temporal is not used: it is another cluster to host, needs replay-safe code,
and would be a second record outside the audit and event chains.

**Prerequisite for slice 3.** Synchronous `workspace.generate` deliveries
have no identity, so a blind retry could apply edits twice. Before the
engine ships: every delivery gets an id recorded before sending, the daemon
journals the reply under it, and a retry replays the reply or reconciles by
`workspace.hash`. An unclear result blocks for a person.

**API.** A Viewer reads, a Manager drafts, validates and edits custom
columns, and only an Owner activates, deactivates, rejects or binds. Routes
live under `/v1/companies/{id}/workflows`, `/v1/workflows/{id}`
(`validate`, `revise`, `activate`, `deactivate`, `reject`),
`/v1/companies/{id}/workflow_bindings`, `/v1/companies/{id}/board_columns`,
`/v1/tasks/{id}/workflow` and `/v1/sprints/{id}/workflow`. There is no new
approval endpoint.

## 6. UI

A Company → Workflows page lists definitions with their numbers. The editor
is an ordered list with `after` pickers; `verify` and `close` are locked
rows. A workflow without `review` says so. Board → Columns manages custom
columns, and the task page gets a Workflow tab linking each step to its run,
Verification and events.

## 7. Migration path in slices

Each slice ships on its own and leaves the product working.

1. Built-in default workflows as code, the validator, a read-only
   projection and the Workflow tab. No table, worker untouched.
2. Definitions, validation and Owner-only activation. Bindings not read yet.
3. The engine runs the defaults, after the delivery-identity prerequisite.
   Behaviour must be identical, checked by golden event sequences.
4. Custom columns, as views only.
5. Bindings, budgets within the ceiling, a second `review`, `approve`,
   `notify`, removing `review`, narrowing the Manager policy.
6. Measurement, revert and ScrumMaster proposals.
7. Parallel `review` branches.
8. After-done and sprint workflows configured.
9. The console producer.

Slices 1–4 change nothing about how work is done. Slice 5 is the first that
lets a company change its loop.

## 8. Risks, and what is not taken from Flow

**Risks:**

- Claims laundered through configuration. Mitigation: closed registry, no
  script step, I5.
- The guard drifting from the graph. Mitigation: the UI shows the guard's
  reason from the same function.
- Step rows and events disagreeing. Mitigation: one writer, cross-checked by
  the history checker.
- Numbers that cannot be compared across workflows. Mitigation: measure each
  version only over its pinned tasks.
- Removing QA. Mitigation: `verify`, the guard and the high-risk hold stay;
  the version is measured; the UI says no reviewer reads the work.
- Custom columns leaking into the loop. Mitigation: I11 and an import test.
- An automatic revert surprising the Owner. Mitigation: it returns only to
  a version the Owner activated, audited, and the brief and inbox say so.
- Slice 3 rewrites the most load-bearing file. Mitigation: no behaviour
  change, golden event sequences.
- Scope creep toward a general automation engine.

**Not taken from Flow:** Temporal; completion by the agent's own
`finish_session` (a claim); `skip` failure policies (a way around `verify`
or a hold); approvals with no approver stored; a person advancing every
step; a container, runtime or model per step (the environment and models
stay the company's); free-text instructions per step (procedures do that);
assets passed between steps; chat, webhook and scheduled triggers; an agent
that writes workflows.

## 9. Decisions

The owner answered on 2026-11-09. Calls marked *(best practice)* were made
for her where she asked for best practice.

1. **QA review is optional per workflow.** `verify` and the guard stay
   mandatory, and `run_tests` stays where delivery tests run. Without QA, a
   task closes on verified evidence plus the guard. (§3.1, I1, I10.)
2. **Activation is an Owner's action**, like `delete_sprint`. Binding is
   an activation too *(best practice)*. `change_workflow` joins
   `ALWAYS_ESCALATE`. (§5, I4.)
3. **Custom columns, yes**, each mapped to exactly one of the five
   statuses. (§3.5, I11.)
4. **Versions are measured and may revert** by the procedures' rule. The
   first-attempt tolerance of 0.10 is new *(best practice)*. The ScrumMaster
   may propose; only an Owner activates. (§3.6.)
5. **Retry budgets** default to rework 2 and unusable reviews 3, with a
   hard ceiling of 5. (§3.4, I7.)
6. **New versions apply to tasks started after activation.** A task in
   progress keeps its pinned version. (§3.5.)
7. **Integration is a separate sprint workflow**: `integrate`, then
   optionally `publish` and `notify`. Its default is today's
   integration. (§3.1, §3.5.)
8. **External producers are future work.** Their runs keep today's path:
   the Verifier, then a person's decision, with the Reviewer agent's
   suggestion and the Manager agent under its policy.
9. **A step may narrow the Manager policy, never widen it.** (§3.2, I9.)

## 10. Open questions

- Whether a procedure measured in a sprint where the workflow also changed
  is marked `unmeasured`. To confirm when slice 6 is built.
- Workflows for runs ingested from other producers (decision 8).

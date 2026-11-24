# The tasks context

A task's record: its events and their hash chain, the projections that answer
the board's questions, the comment-thread readers and the links between tasks.
It records and answers; it never dispatches, reviews or calls a model. Read
this before changing `backend/app/services/tasks/`; each module's docstring
has the detail.

- **Folder:** `backend/app/services/tasks/`
- **May import:** [contracts](contracts.md), [platform](platform.md).
- **Interface:** `board`, `dependencies`, `progress_snapshot`, `relations`, `sprint_board`, `task_events`, `task_projection`, `task_records`.
- **Modules:** `board`, `dependencies`, `events`, `progress`, `progress_snapshot`, `relations`, `saved_views`, `similarity`, `sprint_board`, `task_events`, `task_filter`, `task_projection`, `task_records`.

## How it works

Every transition of a task goes through `task_events.record`: it appends a
typed `TaskEvent` (a kind, an actor, the status after it), seals it in the
task's own hash chain and renders it as the board comment. The task worker,
carry-over, sprint control, a person's review and the Manager agent all write
through it. `TaskRecord` (in `task_projection.py`) reads a task's comments and
events once and answers every question a decision asks: the deliverable, the
pending rework, the block reason, whether it waits for a person.

`task_records.py` reads a task's record for the record tools
(`tools/board_tools`: `board__task`, `board__evidence`; moved from
`loop/worker/board_record`): `read_task` (the task's events and comments) and
`read_evidence` (a run's verification), each split into observations and
claims and bounded, `None` for a task that is not the company's; and the
scopes' task ids (`task_ids`: the task and its dependencies;
`sprint_task_ids`: the task's sprint). The loop's and planning's turns
compose the tools from them.

## Rules

- A task has six statuses: `todo`, `in_progress`, `in_review`, `done`,
  `blocked` and `discarded`. Done and discarded are settled; blocked is
  settled only while it waits for a person. Only a person discards, with a
  reason, as a `discarded` event; the history checker refuses one without.
- `task_events.record` is the only way a transition is written. The event's
  actor must match the comment's author. The caller commits.
- The chain is append-only and keyed with `AUDIT_SIGNING_KEY` (a chain apart
  from the audit log). `verify_chain` reports an unsigned event after a signed
  one as `downgrade`.
- The thread is re-read under the chain's lock before a comment is appended,
  so a stale writer never drops a comment committed meanwhile.
- A person's text is kept whole; text the platform writes is capped at
  `TEXT_LIMIT` (4000).
- A person's change carries who made it (`PERSON_FIELDS`: `by`, `user_id`,
  `token_id`, `identified`) on the event and its comment, read from the
  signed-in session (issue #767). With no signed-in person `identified` is
  false: the board shows "Unidentified", never someone's name or `user`.
- Advice (a console-advice note, the Architect's brief) is never a verdict.
  The phrases that keep a task blocked count only from the system, a gate or a
  person (`BLOCK_VERDICT_AUTHORS`), never from a deliverable.
- Advice kinds (`relation`, `board_note`, `effort_observed`…, listed in
  `task_projection._ADVICE_KINDS`) never change what the board answers.
- Progress is built from events, never from a task's mutable fields. A claim
  (`delivered`) and an observation (a `verified` verdict) are separate lines
  and never merged.
- A board note, a proposed relation and a likely duplicate are claims.
- A sprint with board notes or a progress snapshot is archived, never deleted.

## Components

### A task's life as events

`task_events.py` writes and verifies the chain; `task_projection.py` answers
from events when they cover the whole thread (`covers`) and falls back to
`board.py` otherwise. A loop over many tasks loads their events once
(`records_for`). `events.py` is a thin append-and-read helper over
`task_events`. Routes: `GET /v1/tasks/{id}/events` and
`GET /v1/sprints/{id}/events` (Viewer).

### The readers of a task's comment thread

`board.py`: pure readers shared by the worker, the inbox and carry-over (the
deliverable to review, the `NEEDS WORK:` requests, `block_reason`,
`blocked_item_next_step`). No engine, no I/O.

### Dependencies between a sprint's tasks

`dependencies.py`: `BacklogItem.depends_on` holds the tasks that must be done
first. A task is queued only when `is_ready`. A `todo` task waiting on one a
person must act on is held with an explicit block reason, and released once
its dependencies are done. `sprint_settled` reconciles the holds, so a sprint
never closes over a task that could run. The history checker refuses a task
that delivered before its dependencies closed.

### The sprint board

`sprint_board.py`: the short typed notes a sprint's tasks leave each other,
checked by `contracts/board_notes`, each written with a `board_note` event. A
person may mark one wrong. Design: [`SPRINT_BOARD.md`](../archive/design/SPRINT_BOARD.md).

### What holds a task back

`task_hold.py`: the board's `observed.hold` for each task, from its
`TaskRecord` (issue #766): `frozen`, `risk_hold`, `dependency` (with
`waiting_for`), `retry_pending`, `review_pending`, `person` (with the latest
verdict as `reason`) for a blocked task; `dependency` or `run_window` for a
`todo` one; `null` otherwise. `observed.waits_for_person` is true only for
the holds a person releases (`frozen`, `risk_hold`, `person`), so a task that
waits for another task is not shown as waiting for a person.

### Filters and saved views

`task_filter.py` evaluates a filter over an allowlist of fields from the
records the board reads (the Verifier's verdict, never the run's claims).
`board_observations` keeps `observed` and `reported` apart per task.
`saved_views.py`: a private view is its author's; a company view its author's
or a Manager's; a locked one a Manager's. Design:
[`SAVED_VIEWS.md`](../archive/design/SAVED_VIEWS.md).

### A sprint's progress

`progress.py` replays the events into a series of `claimed`, `verified` and
`gap`, with one canonical encoding. At close, `progress_snapshot.py` stores it
with a digest the backend computes and writes into the audit chain; a row is
never edited, and a reopened sprint gets the next `close_number`. `claim_gap`
is the number the procedures are measured against ([learning](learning.md)).

### Typed relations between tasks

`relations.py`: `blocked_by`, `duplicate` or `relates_to`, `proposed` or
`confirmed`, one row per pair. Every change is a `relation` event on both
tasks. Only a confirmed `blocked_by` within one sprint affects dispatch (it
adds to `depends_on`). `similarity.py` scores two titles without a model; a
likely duplicate is recorded as a *proposed* relation for a person to decide.

## See also

- How the worker uses the record: [the sprint loop](../SPRINT_LOOP.md).
- Who writes events: [loop](loop.md), [release](release.md), [management](management.md).

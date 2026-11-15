# The sprint board (issue #439)

The tasks of one sprint run apart: the Developer of task 3 does not know that
the Developer of task 1 decided to store timestamps in UTC, renamed
`save()` or found that the cache is per process, until QA sends one of them
back for contradicting the other. The sprint board is a shared working board
for a sprint's agents: an append-only list of short, typed notes, each
task's prompt showing the notes that concern it. This note records the
design, what this slice builds and leaves, and what was studied. The modules
are described in [contracts](../contexts/contracts.md),
[tasks](../contexts/tasks.md), [evidence](../contexts/evidence.md),
[loop](../contexts/loop.md), [learning](../contexts/learning.md) and the
[frontend](../contexts/frontend.md); the worker's steps in
[the sprint loop](../SPRINT_LOOP.md).

## A note is a claim

The design rule that shapes everything else (`docs/POSITIONING.md`, and its
2026-10-18 amendment: messages between agents are claims). A note says what
its author believes; it never decides a verdict and is never stored in an
evidence or observation field.

- The table has no field for an observation (`models/board_note.py`).
- Where a note names a file, the board shows beside it what the Verifier's
  records observed (`services/evidence/touched.verified_writers`): the latest
  task-worker run whose latest verdict is `verified` and whose daemon report
  says it wrote that file, or "not observed". A refuted run's write does not
  count. The API returns it as `observed`, a field apart from the note's
  own; the prompt says "observed: changed by verified run #N (task #T)" or
  "not observed" on its own line under the note.
- Each note is a `board_note` task event with its author, the run whose
  deliverable carried it, and `claim: true`. The event is in the task's
  hash chain but renders no comment and says nothing about the task's state
  (`task_projection._ADVICE_KINDS`).

## The note

`sprint_board_notes` (migration `0023`), company-scoped, kept with its
sprint:

| Field | What it holds |
|---|---|
| `kind` | `decision`, `interface_change`, `schema_change`, `gotcha`, `question` |
| `text` | At most 500 characters, whitespace folded |
| `author_role`, `author_name`, `author_task_id`, `run_id` | Who claimed it, from which task's delivery; `person` and an email for a person's note |
| `links` | `files`, `symbols`, `requirements`, `modules`, at most 10 each; files relative, never `..` or absolute |
| `supersedes_id` | The earlier active note of the same sprint this one replaces |
| `status`, `status_by`, `status_at`, `status_reason` | `active`, `superseded` (by `note #N`), `marked_wrong` (by a person, with a reason) |
| `created_at` | When it was written |

Append-only in the sense that matters: kind, text, links and author are never
edited. Only the status changes, and each change is recorded (an event; for a
person, an audit row too).

**Why a table and not only events.** The task events are per task and hash
chained, which suits "who claimed what, when"; but the board is read per
sprint, filtered by status and matched by links on every prompt. A table
answers that in one indexed query; the events keep the history and the
author. A person's note and a mark-wrong, which belong to no task, go on the
sprint's own chain (`task_id` null, which `task_events.seal` already
supports).

## Writing a note

**In a delivery** (`services/contracts/board_notes.parse`), in a fixed
format like the ScrumMaster's `DECISION … END`, outside every fenced block:

```text
NOTE kind=decision files=src/store.py,src/api.py requirements=REQ-1.2
Timestamps are stored in UTC; convert at the edges.
END
```

- `kind` is required; `files`, `symbols`, `requirements`, `modules`
  (comma-separated) and `supersedes` (a note id, `#7` or `7`) are optional.
  `interface-change` and `Interface Change` read as `interface_change`.
- Fenced blocks are skipped, so a file that contains `NOTE` is not a note,
  and prose such as `NOTE: remember…` is not a header.
- At most 5 notes a delivery. A block that is not a note (unknown kind or
  attribute, no text, no `END` before the next `NOTE`, an unsafe file path,
  over 500 characters, over 10 links of a kind, a sixth note) is ignored, and
  the reasons are recorded as one `board_note_rejected` event on the task.
- A `supersedes` that names no active note of the sprint is dropped and
  recorded; the note itself stands. A note the same task already left, word
  for word and still active, is not written twice (a rework repeats its
  deliverable).

The worker does this after recording the run (`services/loop/worker/sprint_notes.record_from_delivery`,
called from `deliver._deliver_artifacts`), whatever the verdict: a note is a
claim either way, and the observation beside it says what happened.

**By a person**, on the sprint's Board notes tab (`POST /v1/sprints/{id}/board_notes`,
Manager; audited as `board_note_added` under `sprint:<id>:board`).

**Not built: the MCP-shaped tool.** `board.add_note` / `board.read_notes` in
the tool loop would need the tools context to reach the board, which it may
not import (tools may import contracts, platform, evidence and workspace;
the board lives in tasks), and a new injection point in the worker's
toolbox, outside this slice. The parsed format covers writing; the prompt
section covers reading. Next: a toolbox the loop hands to
`developer_tools.toolbox_for`, as the workspace tools are handed the daemon.

## Reading the board

Every task prompt (`execute._developer_prompt`) gets a `sprint_board`
section (`sprint_notes.board_section`), priority 4: the prompt budget
(`prompt_budget.fit`) cuts it after the memory, the completed tasks and the
console notes (5–6), with the lessons (4), and before the repository and
related code (3) and the requirements (2); never before a required section.

- **Relevance** (`board_notes.relevance`, deterministic overlap). What the
  task touches (`sprint_notes.task_scope`): the files named in its title and
  description, the paths of the approved-architecture modules covering its
  requirements and the files its earlier runs wrote (the related code's
  seeds, `related.seeds_for`), those modules' ids and names, and its
  requirements. A note is relevant by a file (the same file, or a folder
  holding it either way), a module (id or name, case-insensitive) or a
  requirement (`REQ-1.2` meets `REQ-1`). A note with no links speaks to the
  whole sprint and is shown after the matched ones.
- **Order and bound.** Active notes only (superseded and marked-wrong left
  out), matched first, each group newest first, within
  `SPRINT_BOARD_MAX_CHARS` (2000) for the whole section; the header and the
  format (about 800 characters) are always there, the notes get the rest, and
  the section says how many relevant notes were left out. `0` turns the
  section off.
- The graph neighbours of the task's files are not added: they need the
  daemon (`workspace.related`) on every prompt. The seeds already carry the
  module paths and earlier writes; a note on a neighbour reaches the task
  through its module.
- QA's review prompt does not show the board yet (next).

## Marking a note wrong

A Manager marks a note wrong from the tab (`POST /v1/board_notes/{id}/mark_wrong`,
optional reason; audited as `board_note_marked_wrong`; a `board_note` event
with `action: marked_wrong` on the sprint's chain). The note leaves every
prompt; its text stays as written. A note already marked wrong is a 409.

## The retrospective

When a sprint closes, `retrospective.build` adds the board's counts, the
contradiction measure and the notes worth keeping: active decisions,
interface and schema changes and gotchas (not questions), at most 5
(`promotable_notes`). `retrospective.summary` lists them as "agents' claims,
not verified; candidates for memory or a procedure". That summary is the
text the ScrumMaster's retrospective message, the retrospective engram
(company memory) and the procedure proposals' prompt already carry, so the
notes reach memory, and the ScrumMaster sees them when it proposes
procedures, without a new path. Promotion is an offer: no note becomes a
procedure unless the ScrumMaster proposes one and the procedures' own rules
adopt it. A Manager choosing which note to promote, one by one, is next.

## The measure

The issue asks for rework requests caused by a contradiction with another
task of the same sprint, before and after. `sprint_board.contradiction_rework`
counts, per sprint, the `NEEDS WORK:` requests (the ones
`board.rework_request_count` counts) whose text

1. contains one of `contradict`, `conflict`, `inconsistent`, `incompatible`,
   `mismatch`, `disagree`, and
2. names another task of the same sprint: `task #N` or `#N`, its title (8
   characters or more), `note #N` of a note another task wrote, or a file a
   note of another task links.

It is read from the board, needs no model and gives the same number on every
read. `GET /v1/sprints` shows it per sprint as `contradiction_rework` beside
`rework`, the Board notes tab and `GET /v1/sprints/{id}/board_notes`
(`metrics`) show it with the notes' counts, and the retrospective carries it.
**Before and after:** the words and the references by task id or title
apply to every sprint, with or without notes, so sprints before this change
are measured the same way; the references through notes (`note #N`, a file
a note links) only exist after, so compare on the first two as well. Compare the share
`contradiction_rework / rework` over the completed sprints before and after
the board. The measure undercounts a reviewer who describes a contradiction
without those words or without naming the other task; it is a floor, not an
estimate.

## Threat notes

- Note text is agent output: it enters other prompts as data under a header
  that calls it a claim, bounded in size and count; the format is parsed by
  a fixed regular expression, never evaluated.
- Links are bounded strings of a safe character set; file links are relative
  without `..`. They are matched as strings and never opened.
- Writes from people need Manager; reads need Viewer of the sprint's
  company; both are company-scoped and audited.

## What was taken, and from where (design only, no code)

- **Blackboard architecture of multi-agent systems** (Hearsay-II, and the
  blackboard pattern in general): independent specialists read and write a
  shared structured store, and a control decides who sees what. Taken: a
  shared per-sprint store of typed entries, written by any task and read
  through a relevance filter rather than broadcast whole.
- **OpenHands' shared event stream** (`openhands/events`): every action and
  observation is a typed event with a source, persisted and replayable. Taken:
  each note is a typed event with its author and cause (the run), in the
  task's chain; the claim and the observation are different things.
- **Devin's session notes**: the agent keeps short notes for itself and later
  sessions. Taken: notes are short, few and written in the deliverable as
  part of the work, not a separate conversation.
- **The code graph** (#438, `services/tools/code_graph.py`,
  `services/evidence/touched.py`): notes link to the same nodes (files,
  modules), the related code's seeds decide relevance, and `touched` is the
  source of what was observed.

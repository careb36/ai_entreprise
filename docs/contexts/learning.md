# The learning context

How the company learns from its sprints: procedures kept or reverted by
numbers, the retrospective, the ScrumMaster's triage, the Architect's record
and the project wiki. Read this before changing
`backend/app/services/learning/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/learning/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md).
- **Interface:** `architect`, `findings`, `procedures`, `retrospective`, `scrum_master`, `wiki_compile`, `wiki_store`.
- **Modules:** `architect`, `findings`, `procedures`, `retrospective`, `scrum_master`, `wiki_compile`, `wiki_sources`, `wiki_store`.

## How it works

It reads the task record and asks models through the proxy; it never moves a
task. At sprint close the retrospective summarises the sprint, procedures are
measured and kept or reverted, and the wiki is compiled.

## Rules

- Agents' claims are never the measure. A task counts as verified only by
  the Verifier's verdict.
- A finding's outcome is an observation the platform writes;
  `record_outcome` refuses any other producer.
- A finding's specification hash is computed from its canonical text, never
  read from the file.
- A specification whose cycle was reverted does not start again unless a
  signed-in Manager or Owner of the company asks. An agent, an API token, a
  Viewer or another company's person is refused.
- In the wiki, a person's version is in force and locks the page; a
  compilation then only proposes. An unknown citation is refused.

## Components

### Procedures

`procedures.py`: procedures measured, kept or reverted, and proposed (step 8
of [the sprint loop](../SPRINT_LOOP.md)). `board_metrics` includes
`claim_gap_rate`, the tasks claimed delivered and not verified; `evaluate`
reverts a procedure under which it grew by more than `GAP_TOLERANCE`.
A role's active procedures open its task prompt (`prompt_context`) and their
titles close it (`delivery_checklist`, the prompt section of the same name), so
they are read again just before the agent delivers.

### The retrospective and triage

- `retrospective.py`: the lessons in the prompt and the message to the team
  (steps 2 and 7). Board notes it carries forward stay claims. Its numbers
  include the sprint's tool demand (`demand_of`, counted by the pure
  `contracts/tool_demand.count`): unknown tool names asked, final answers that
  were a tool call written as text, refused calls (a fetch to a host not
  allowed, a `run_tests` command not run as asked, a check script that may
  not run, a record outside the turn's scope) and runs that spent their tool
  rounds. It reads only the platform's records (each task run's latest
  verification, the audit turns' `tool_calls`, the system's retry and block
  texts), and the summary shows the line beside the other numbers, labelled as
  observations. The progress snapshot does not seal it.
- `scrum_master.py`: the triage of an escalation (step 6).

### Findings

`findings.py`: the retrospective's findings for the self-improvement loop,
each with its specification, target measure and cycle outcomes. Routes are
read-only; writing an outcome is a service function. Design:
[`rsi-loop.md`](../design/rsi-loop.md).

### The project wiki

`wiki_store.py`, `wiki_sources.py`, `wiki_compile.py`: pages and versions,
citations resolved against the company's records, and the compilation at
sprint close, bounded per sprint and per page. Design:
[`PROJECT_WIKI.md`](../archive/design/PROJECT_WIKI.md).

### The Architect's record

`architect.py`: see [producers](producers.md).

## See also

- How the context was built: [the migration record](../architecture/MIGRATION.md#step-8-learning).

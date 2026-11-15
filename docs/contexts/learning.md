# The learning context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/learning/`.

- **Folder:** `backend/app/services/learning/`
- **Responsibility:** How the company learns from its sprints: procedures kept or reverted by numbers, the retrospective, the ScrumMaster's triage and proposals, the Architect's record. It reads the task record and asks models through the proxy; it never moves a task.
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md) (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Modules:** `architect`, `procedures`, `retrospective`, `scrum_master`, `wiki_compile`, `wiki_sources`, `wiki_store`.

## Components

### The gap between claimed and verified as a procedure's number

**`backend/app/services/learning/procedures.py`** (issue #531) — `board_metrics` adds `claim_gap_rate`: the tasks claimed delivered and not verified over the tasks of the sprints measured, from each sprint's first sealed progress snapshot or, without one, its task events (`services/tasks/progress_snapshot.claim_gap`, [tasks](tasks.md)). It is recorded in a procedure's baseline at adoption and in its outcome; `evaluate` reverts a procedure under which it grew by more than `GAP_TOLERANCE` (0.10) besides the rework and blocked tolerances, and a baseline recorded before #531 (without the key) is not compared on it. `describe_outcome` and the proposal prompt's history name it. The agents' claims are never the measure: the gap counts a task as verified only by the Verifier's verdict.

## Described elsewhere

- `procedures` (measured, kept or reverted, proposed): step 8 of [the sprint loop](../SPRINT_LOOP.md). The calibration of the difficulty estimate against observed effort (issue #534) is in [models](models.md); it measures the estimator, not a procedure, so it is not one of the procedures' metrics.
- `scrum_master` (the triage of an escalation, `scrum_master.ask`): step 6 of [the sprint loop](../SPRINT_LOOP.md).
- `retrospective` (the lessons in the prompt, the message to the team): steps 2 and 7 of [the sprint loop](../SPRINT_LOOP.md); it also carries the sprint board's counts, the contradiction measure and the notes worth keeping (`promotable_notes`, as claims) into the summary the memory engram and the procedure proposals read ([the sprint board](../design/SPRINT_BOARD.md)).
- `wiki_store`, `wiki_sources`, `wiki_compile` (the project wiki, issue #445): the pages and their versions, the links derived from each body, a person's precedence (a person's version is in force and locks the page; a compilation then only proposes) and the lint's `stale`; each citation resolved against the company's records (runs and their verdict, verifications, tasks, notes, completed sprints' retrospectives, files with the daemon's sha256, requirements, ADRs and modules), an unknown one refused; the compilation at sprint close from structured inputs, in the project's language, by the TechWriter or else the ScrumMaster, bounded per sprint and per page: [the project wiki](../design/PROJECT_WIKI.md).
- `architect` (the Architect's record): the console assistants paragraph in [producers](producers.md).
- The tests of each (`test_procedures.py`, `test_scrum_master.py`, `test_architect.py`, `test_memory_lessons.py`): [testing](../TESTING.md).
- `CLAUDE.md` had no paragraph of its own for this context before it was split; the documents above hold what was written about it.
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-8).

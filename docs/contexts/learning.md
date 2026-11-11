# The learning context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/learning/`.

- **Folder:** `backend/app/services/learning/`
- **Responsibility:** How the company learns from its sprints: procedures kept or reverted by numbers, the retrospective, the ScrumMaster's triage and proposals, the Architect's record. It reads the task record and asks models through the proxy; it never moves a task.
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md) (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Modules:** `architect`, `procedures`, `retrospective`, `scrum_master`.

## Described elsewhere

- `procedures` (measured, kept or reverted, proposed): step 8 of [the sprint loop](../SPRINT_LOOP.md).
- `scrum_master` (the triage of an escalation, `scrum_master.ask`): step 6 of [the sprint loop](../SPRINT_LOOP.md).
- `retrospective` (the lessons in the prompt, the message to the team): steps 2 and 7 of [the sprint loop](../SPRINT_LOOP.md).
- `architect` (the Architect's record): the console assistants paragraph in [producers](producers.md).
- The tests of each (`test_procedures.py`, `test_scrum_master.py`, `test_architect.py`, `test_memory_lessons.py`): [testing](../TESTING.md).
- `CLAUDE.md` had no paragraph of its own for this context before it was split; the documents above hold what was written about it.
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-8).

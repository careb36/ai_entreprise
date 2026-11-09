# ADR 0014: task_worker.py is split by loop stage inside the loop context

## Status

Accepted (2026-11-09). Recorded as ADR-3 of the architecture in `docs/architecture/architecture.json` (issue #469); `docs/ARCHITECTURE.md` is rendered from it.

## Context

task_worker.py has 3,299 lines (process_backlog_tasks alone about 500, _review_cycle_body about 640) and imports 37 services; the size baseline lets it only shrink. Tests patch many of its private names (task_worker._execute_task, _deliver_artifacts, ...).

## Decision

Split it into backend/app/services/loop/worker/: intake.py (consumer, claim, queue markers, dispatch_todo), prompt.py (task context, memory, lessons, procedures), execute.py (LLM call, Architect consultation, edit plan), deliver.py (artifacts to the daemon, queued deliveries, delivery results), record.py (AgentRun recording and re-verification), review.py (review cycle, QA call, quality gate), close.py (sprint finalization, follow-up, retrospective, procedures), messages.py (agent messages, triage). Each under 600 lines; the two long functions are cut into steps. task_worker.py stays as a shim re-exporting the public names.

## Consequences

One pull request per stage, each moving its functions and the test patch targets to the stage module that owns them, so a patch never lands on a re-export. The file-size baseline entry for task_worker.py drops with each stage and is removed when the shim is under 100 lines. The shim goes when nothing imports it.

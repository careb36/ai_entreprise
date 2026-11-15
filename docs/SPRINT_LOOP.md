# The sprint loop: data flow for a sprint

What happens from `POST /v1/start_sprint` to the close of a sprint, step by step. Moved whole from `CLAUDE.md`, whose index is [here](../CLAUDE.md). The steps are run by the [loop](contexts/loop.md) context; they also describe the [evidence](contexts/evidence.md) (steps 4 to 6), the [learning](contexts/learning.md) context (steps 6 to 8), the [tasks](contexts/tasks.md) record and the [producers](contexts/producers.md) (step 9).

## Data Flow for a Sprint

Tasks are executed by the **task worker**, split by stage into `backend/app/services/loop/worker/` (ADR 0014): `intake` (the consumer, the claim, queue markers, dispatch) and `task_run` (one claimed task up to QA's handoff), `prompt`, `execute`, `deliver`, `record`, `review` (with `qa_review` and `quality_gate`), `close` (with `procedures_round`), `messages` and the shared `board_writes`; `services/loop/task_worker.py` is the facade that re-exports them (`process_backlog_tasks`, the `engine`), and `services/task_worker.py` a shim of it;
the LangGraph graph is only an optional kickoff conversation.

1. `POST /v1/start_sprint` writes the sprint with its team (`services/planning/sprint_team.py`; or `POST /v1/init_company` Sprint 0, which with `start_now: false` stays `planned` until `POST /v1/sprints/{id}/start`), one `BacklogItem` per task and one
   outbox event per task on `task.assign`; the outbox processor stores them in the
   JetStream stream `FABRIQUE_TASKS` (an outbox row completes only once stored).
2. The task worker consumes `task.assign` as the durable consumer `task-worker`,
   so assignments published while it was down are processed when it starts. It
   loads the item and the assigned agent
   (empty model → role default from `services/models/model_factory.py`) and calls the LLM proxy.
   Before each attempt `services/loop/task_routing.py` estimates the task's
   difficulty from its plan (`services/models/difficulty.py`: criteria
   covered, the approved architecture's modules and files for them,
   dependencies, the attempt, the rework requests and send-backs on its
   board; a score 1..5 in a band `low`/`medium`/`high`, with its inputs and
   weights), and, when the company or the deployment has a routing policy
   (`services/models/routing_policy.py`, `AGENT_MODEL_POLICY`) and no person
   chose the agent's model, requests the band's model instead of the role
   default. The estimate and the routing (`{policy, source, band, requested,
   chosen, reason}`) are stored on the task as an `estimated` event and on
   the run's evidence as `difficulty` and `routing`, beside the model the
   proxy reports serving (`AgentRun.model`, the observation); the request is
   never recorded as the served model. No policy: the role default, as before.
   The prompt asks for every file as a fenced block with `path=<relative path>`;
   on rework it also carries the pending `NEEDS WORK:` feedback. It also carries
   the company's past decisions from memory (scoped by `company_id`), and
   **Lessons from Recent Sprints**: the retrospective counts and the review
   requests from the company's last two completed sprints, read from the board
   (`_load_recent_lessons`), and the **Company Procedures** in force for the
   agent's role (QA's go into its review prompt). It also carries the
   **Sprint board**: the notes the sprint's other tasks left that concern
   this one (by the files, modules and requirements it touches; active ones
   only, newest first, within `SPRINT_BOARD_MAX_CHARS`), each a claim with
   what the Verifier's records observed beside the files it names, and how
   to write a note (`services/loop/worker/sprint_notes.py`,
   [design](design/SPRINT_BOARD.md)).
3. `parse_file_manifest` (`services/contracts/manifest.py`) turns the answer into files (a block marked `edit`
   carries SEARCH/REPLACE edits instead, `services/contracts/edit_blocks.py`; the daemon
   applies them exactly and writes a `.py` or `.json` file only if it still
   parses, `vscode-daemon/file_edits.py`). The worker sends them to the
   VS Code daemon with a NATS **request** on `workspace.generate`, targeting the
   company's project (`ProjectDiscovery.project_path`, recorded at company creation;
   `AGENT_WORKSPACE_ROOT` is the fallback). The daemon replies per file with the
   sha256 of the bytes it read back from disk. If no daemon answers, the files are
   queued on `workspace.deliver` (JetStream), the run is recorded as waiting for
   that job and the review loop holds the task; when a daemon connects it writes
   them and reports on `workspace.delivered`, and the run is re-verified against
   the hashes the backend recorded (`_on_delivery_result`).
4. Optionally (`DELIVERY_TESTS_ENABLED`) the daemon runs the project's tests via
   `workspace.execute` and returns the exit status. In a sprint planned from a
   specification, the plan puts a **test-writing task per requirement**
   (`BacklogItem.kind == "acceptance_tests"`, assigned to QA) before the
   implementation tasks, which depend on it: one test per acceptance criterion,
   named after its id (`REQ-1.2` → `test_req_1_2_…`), importing the code under
   test inside the test so a missing module fails that test and not the file.
   `services/evidence/acceptance.py` reads the runner's per-test lines back into
   criterion ids (`pass`, `fail`, `missing`; an observation, from the daemon's
   output) and the Verifier records them as `criteria_tests`: for the test task,
   every criterion must have run (failing is expected); for an implementation
   task, its own criteria must pass. `tests_run` excuses only the failing
   acceptance tests of criteria whose implementation is not done yet, never
   another failure; an implementation task that writes an acceptance test file
   is refuted (`acceptance_tests_untouched`) and sent back. Before its tests
   run, `acceptance.guard_test_files` asks the daemon for the sha256 of the
   acceptance test files on disk (`workspace.hash`) against what QA's delivery
   wrote; a file that differs or is missing is restored from QA's deliverable
   (`AgentRun.artifact_text`, parsed again) and the delivery refuted, so no
   criterion is judged against a weakened test. `services/evidence/criteria_tests.py`
   holds the reading of the runner's output and the two checks. A criterion
   is `verified` only from the most recent verification across the tasks
   covering it, and only when that run's whole verdict is verified.
   The PO's and the Architect's calls to the proxy (`spec_author`,
   `arch_author`, `spec_planning`) are `prepare`d with the request's session
   and run in a thread as plain values (`draft_from`, `plan_from`): a session
   never crosses a thread. The Developer's
   prompt names the test files and test names to make pass
   (`acceptance.section`), the specification's coverage says per criterion
   `verified` (its test passed in an observed run of an implementation task)
   with `last_result`, the publication's description carries how many criteria
   are verified, and `history_check.py` refuses a criterion marked passed with
   no observed test run.
5. The deliverable is ingested as an `AgentRun` (`producer: le-fabrique-task-worker`,
   `external_ref: task-<id>`) and verified deterministically (`files_written`,
   `tests_run`, …). Claims (declared files, the agent's summary) and observations
   (daemon reports) are kept apart; the board and Glassroom show the verdict.
6. The review loop sends a refuted/unconfirmed delivery or a failed test run back
   for rework without asking an LLM; otherwise QA reviews, and only an explicit
   `APPROVED:` closes the task. The Verifier also records a `risk_tier` check
   (`services/evidence/risk.py`: high for authentication, secrets, payments or the CI
   pipeline, medium for shell scripts, migrations, dependency manifests or
   container files, from the paths the daemon wrote and the producer declared;
   a producer may raise it with a reason, never lower it). An approved task
   whose tier is high waits for a person at every autonomy level, level 3
   included: the inbox lists it with `risk_hold`, a Manager accepts it (marks
   it done) or sends it back, and it counts neither as blocked in the
   procedures' numbers nor as a task to carry over on the company's own. Before any of that, the text checks on the
   deliverable (length, sections, balanced fences — `_validate_minimum_deliverable`)
   send it back with the reason as `Quality gate`, up to
   `AUTOPILOT_MAX_REWORK_CYCLES`, then block the task and escalate to the
   ScrumMaster. A sprint closes when every task is settled: done, or blocked
   until a person acts (nothing the worker retries, re-reviews or an operator
   froze). Blocked tasks stay on the board, appear in the retrospective and
   count in the procedures' `blocked_rate`; approvals close the sprint on the
   spot and the review cycle closes one whose last open task got blocked.
   `GET /v1/inbox` (Viewer, `routers/inbox.py`) lists what the company needs a
   person for: the tasks blocked until someone acts (not the ones the worker
   still retries or re-reviews), the procedure proposals waiting and how many
   runs of other producers nobody decided on (the company's own task worker
   and kickoff runs, `IN_HOUSE_PRODUCERS`, are decided by the review loop;
   `GET /v1/agent_runs?exclude_in_house=true` lists the rest; with
   `REVIEWER_AGENT_ENABLED` the Reviewer agent suggests a decision on each,
   kept on the run apart from the approval). The Review page shows them (a Manager can send a
   blocked task back to work, with how earlier second attempts went beside them) and the navigation counts them. A blocked task
   can also get another attempt in the next sprint (`services/loop/carry_over.py`):
   `POST /v1/start_sprint` takes `carry_over_ids` (counted in the scope), the
   "Plan a sprint" form offers them (with how earlier second attempts went,
   from the sprint list's `second_attempts`), a Manager moves one from the board into an
   open sprint or a new one (`POST /v1/backlog/{id}/carry_over`, `services/loop/board_moves.py`),
   and with `autonomy_level` 3 the follow-up
   sprint carries the closed sprint's blocked tasks on its own, once each. The
   new task carries what blocked the old one (a `Previous attempt` prompt
   section); the old one is marked and leaves the inbox, and the two are
   linked by a confirmed `relates_to` relation (`services/tasks/relations.py`,
   `rule:carry_over`; information only, never a dependency). The escalation gets
   an answer (`services/learning/scrum_master.py`): the ScrumMaster reads what blocked
   the task, the rework asked for and how the company's second attempts went
   (`track_record`: done out of carried, with and without its brief, counted
   from the boards of completed sprints, the same numbers the Sprints page
   shows) and decides, in a fixed `DECISION … END`
   format a parser checks, between another attempt with a brief for it or
   leaving it to a person. The decision is a `decision` message that names
   the escalation it answers (written by the worker, not the model), a claim: the
   inbox shows it as `triage`, the brief travels with the carried task into
   its prompt (and into QA's review prompt, so the second attempt is judged
   against the same context), and a task left for a person is not carried
   over on the company's own (a person still can). No usable answer keeps
   the default.
   Every question to the ScrumMaster (the triage, the procedure proposals)
   goes through `scrum_master.ask`, and every count of rework requests
   (the sprint list, the procedures' numbers) through
   `board.rework_request_count`.
7. Each hand-over between roles is also recorded as an `AgentMessage`
   (`agent_messages`), the company's conversation:
   - the Developer's question to the Architect and its answer (the Architect
     is told how tasks went after its earlier briefs, counted from the boards);
   - the handoff to QA, listing the declared files;
   - QA's decision or rework request, and the Verifier's;
   - escalations to the ScrumMaster when a task is blocked;
   - the ScrumMaster's retrospective to the team when a sprint closes.

   Messages are claims: comments still drive the review loop, and the evidence
   decides. So are the **sprint board's notes**: after a delivery is recorded,
   its `NOTE kind=… files=…` … `END` blocks become notes of the sprint
   (`sprint_notes.record_from_delivery`), each a `board_note` event with its
   author; a person adds one or marks one wrong on the Board notes tab. `GET /v1/messages` (Viewer; filters `sprint_id`, `backlog_item_id`,
   `kind`) feeds the sprint's Conversation tab and the task page.
8. When a sprint closes, procedures are measured and proposed
   (`services/learning/procedures.py`):
   - Each active procedure with two completed sprints since its adoption is
     kept, or reverted if rework per task or the share of blocked tasks got
     clearly worse. A reverted procedure brings back the version it replaced.
   - After a sprint with rework or blocked tasks, the ScrumMaster proposes up to
     two procedures in a fixed `PROCEDURE … END` format; anything else is
     ignored. With `autonomy_level` 3 a proposal is adopted at once; otherwise
     a Manager adopts or rejects it (`/v1/procedures`, the Procedures page).
     The ScrumMaster is shown what earlier procedures did (kept, reverted or
     unmeasured with the board's numbers, or rejected by a person;
     `procedures.history_prompt`), and a proposal with the same text as one
     reverted or rejected is dropped (`propose(skip_failed=True)`); a Manager
     writing a procedure can still bring one back.
   - Adoption records the numbers of the last two completed sprints as the
     baseline. Outcomes and proposals are posted to the team as `proposal`
     messages. The numbers decide, never a model.
9. With `AGENT_GRAPH_ENABLED=true`, `agent_worker` also runs the graph kickoff for
   the sprint; its transcript is ingested as an `AgentRun` (`le-fabrique-graph`).
   A failed kickoff never blocks the sprint.
10. Engram events go to Neo4j for episodic memory; embeddings to pgvector for RAG.

# The sprint loop: data flow for a sprint

What happens from `POST /v1/start_sprint` to the close of a sprint, step by step. Moved whole from `CLAUDE.md`, whose index is [here](../CLAUDE.md). The steps are run by the [loop](contexts/loop.md) context; they also describe the [evidence](contexts/evidence.md) (steps 4 to 6), the [learning](contexts/learning.md) context (steps 6 to 8), the [tasks](contexts/tasks.md) record and the [producers](contexts/producers.md) (step 9).

## Data Flow for a Sprint

Tasks are executed by the **task worker**, split by stage into `backend/app/services/loop/worker/` (ADR 0014): `intake` (the consumer, the claim, queue markers, dispatch) and `task_run` (one claimed task up to QA's handoff), `prompt`, `execute`, `deliver`, `record`, `review` (with `qa_review` and `quality_gate`), `close` (with `procedures_round`), `messages` and the shared `board_writes`; `services/loop/task_worker.py` is the facade that re-exports them (`process_backlog_tasks`, the `engine`), and `services/task_worker.py` a shim of it;
the LangGraph graph is only an optional kickoff conversation.

0. Before the sprint exists, `POST /v1/plan_sprint` asks the PO for a plan
   (`services/planning/sprint_planner.py`). The objective the person wrote is
   never cut: the plan carries it whole as `objective`, apart from the
   model's one-line `goal`, and `start_sprint` stores it as
   `Sprint.objective` (TEXT, migration `0056`). When the planner's model does
   not answer usably (no model, an error or timeout, an answer that is not a
   plan, too few tasks), the plan is made without it (`sprint_fallbacks`):
   its goal is the objective whole, the PO's task cites it whole, and when
   the objective lists explicit items (Markdown headings or a numbered list,
   read without a model by `objective_text`), each item is a task whose
   description is the item's text as written; items beyond the scope are
   named in `unplanned_items`, never dropped. The backend records the
   fallback with the reason it observed (`planner_fallback` in the audit
   log, `services/planning/planner_fallback.py`) and returns its `plan_id`;
   the plan preview says "the planner's model did not answer; this is a
   generic plan" with a "Retry planning" button. `start_sprint` with that
   `plan_id` reads the backend's own row back and records it on the sprint
   (`sprint:<id>:planning`); the board shows the note, the objective whole on
   demand, and, for a Manager, "Retry planning", which opens the planner
   with the objective. A `plan_id` the backend did not record (or another
   company's) records nothing.
1. `POST /v1/start_sprint` writes the sprint with its team (`services/planning/sprint_team.py`; or `POST /v1/init_company` Sprint 0, which with `start_now: false` stays `planned` until `POST /v1/sprints/{id}/start`, and which a folder the daemon observes to be a repository with code does not get at all unless the request asks — `services/loop/foundation_setup.py`, retrospective F8), one `BacklogItem` per task and one
   outbox event per task on `task.assign`; the outbox processor stores them in the
   JetStream stream `FABRIQUE_TASKS` (an outbox row completes only once stored).
2. The task worker consumes `task.assign` as the durable consumer `task-worker`,
   so assignments published while it was down are processed when it starts.
   When no workspace daemon was heard within the offline window and NATS
   answers "no responders", it does not claim the task: the sprint pauses
   with the reason (start the daemon with `./scripts/start-daemon.sh`, then
   resume), the task stays `todo` and no rework is counted; a run whose
   workspace tool calls were all answered "no responders" is discarded the
   same way instead of going to review (`services/loop/daemon_gate.py`). It
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
   Before a design-sensitive, reworked or hard task the Architect writes a
   brief for the prompt (`_consult_architect`). It may first read the project
   with the workspace's read tools alone (list, read, search, find_file on the
   task's root; no edit, move, delete or test run) for at most
   `ARCHITECT_TOOL_ROUNDS` rounds, each call an observation on its turn in
   `audit_log`, then answers within `BRIEF_MAX_TOKENS`. A tool call typed as
   text is no brief; without the daemon it is one plain call
   (`services/loop/worker/architect_brief.py`).
   With the company's Git workflow on, before the first turn the worker asks
   the daemon for the task's worktree (`workspace.console.prepare` with
   `private_git: false`: branch `fabrique/task-<id>` from the base, the done
   prerequisites merged, put back at its head) and every tool of the attempt
   (reads, the repository map, edits, move and delete, `run_tests`) runs
   there, from the first attempt; the person's checkout is never written and
   the delivery commits in that same worktree. Only when every daemon
   announces the `private_git` feature; otherwise a first attempt reads the
   person's checkout without the write tools, as before. The daemon's reply
   is kept as `tools_offered.task_worktree`
   ([design](design/first-attempt-in-task-worktree.md)).
   The prompt asks for every file as a fenced block with `path=<relative path>`;
   on rework it also carries the pending `NEEDS WORK:` feedback. It also carries
   the **Project wiki**: the company's pages in force (not stale, not proposed)
   for the task's modules and concepts, then the decisions in force and the
   glossary, within `WIKI_MAX_CHARS`, each statement with its citations and
   called a claim (`services/loop/worker/wiki_section.py`,
   [design](archive/design/PROJECT_WIKI.md)); only when no module or concept page
   applies, the company's past decisions from vector memory (scoped by
   `company_id`), and
   **Lessons from Recent Sprints**: the retrospective counts and the review
   requests from the company's last two completed sprints, read from the board
   (`_load_recent_lessons`), and the **Company Procedures** in force for the
   agent's role (QA's go into its review prompt). It also carries the
   **Sprint board**: the notes the sprint's other tasks left that concern
   this one (by the files, modules and requirements it touches; active ones
   only, newest first, within `SPRINT_BOARD_MAX_CHARS`), each a claim with
   what the Verifier's records observed beside the files it names, and how
   to write a note (`services/loop/worker/sprint_notes.py`,
   [design](archive/design/SPRINT_BOARD.md)).
   With tools, the call is the bounded tool loop
   ([tools](contexts/tools.md)): the run's evidence records `rounds_used`,
   `rounds_max` and `rounds_exhausted`, and a final answer that is only a tool
   call written as text is no deliverable. The first time, the task is queued
   again once (a `retried` event, "Final answer was a tool call written as
   text, queued again with a fresh tool budget"), and its next attempt opens
   a new toolbox; a second time it is blocked with the reason ("the model's
   last answer was a tool call written as text; the tool budget had run out
   (again, after one retry with a fresh tool budget)";
   `services/loop/worker/intake.py`, `requeue_after_transient_failure`).
3. `parse_file_manifest` (`services/contracts/manifest.py`) turns the answer into files, and only those (an answer without `path=` blocks or tool edits writes nothing and is text for QA; a block marked `edit`
   carries SEARCH/REPLACE edits instead, `services/contracts/edit_blocks.py`; the daemon
   applies them exactly and writes a `.py` or `.json` file only if it still
   parses, `vscode-daemon/file_edits.py`). A file the edit tool emptied and
   deleted (`workspace__delete`) travels as a `delete` entry with the hash the
   daemon reported, and the daemon removes it only while it still holds those
   bytes ([design](design/move-and-delete-files.md)). The worker sends them to the
   VS Code daemon with a NATS **request** on `workspace.generate`, targeting the
   company's project (`ProjectDiscovery.project_path`, recorded at company creation;
   `AGENT_WORKSPACE_ROOT` is the fallback). The daemon replies per file with the
   sha256 of the bytes it read back from disk. If no daemon answers, the files are
   queued on `workspace.deliver` (JetStream), the run is recorded as waiting for
   that job and the review loop holds the task; when a daemon connects it writes
   them and reports on `workspace.delivered`, and the run is re-verified against
   the hashes the backend recorded (`_on_delivery_result`).
4. Optionally (`DELIVERY_TESTS_ENABLED`) the daemon runs the project's tests via
   `workspace.execute` and returns the exit status. In a repository with a
   profile per folder it also runs the suites of the folders the daemon saw
   the delivery write in and the company's every-delivery commands
   (`services/loop/delivery_suites.py`), each judged by `required_commands`. In a sprint planned from a
   specification, the plan puts a **test-writing task per requirement**
   (`BacklogItem.kind == "acceptance_tests"`, assigned to QA) before the
   implementation tasks, which depend on it: one test per acceptance criterion,
   named after its id (`REQ-1.2` → `test_req_1_2_…`), importing the code under
   test inside the test so a missing module fails that test and not the file.
   `services/evidence/acceptance.py` reads the runner's per-test lines back into
   criterion ids (`pass`, `fail`, `missing`; an observation, from the daemon's
   output) and the Verifier records them as `criteria_tests`: for the test task,
   every criterion must have run (failing is expected); for an implementation
   task, its own criteria must pass. When it refutes, the observation — and so
   the task's comment and its block reason — leads with the failing tests the
   output named (at most 20, then how many it left out) and the per-criterion
   results, and only then the end of the output (finding F6 of the self-hosting
   retrospective). `tests_run` excuses only the failing
   acceptance tests of criteria whose implementation is not done yet, never
   another failure; an implementation task that writes an acceptance test file
   is refuted (`acceptance_tests_untouched`) and sent back. Before its tests
   run, `acceptance.guard_test_files` asks the daemon for the sha256 of the
   acceptance test files on disk (`workspace.hash`) against what QA's delivery
   wrote; a file that differs or is missing is restored from QA's deliverable
   (`AgentRun.artifact_text`, parsed again) and the delivery refuted, so no
   criterion is judged against a weakened test. Under the Git workflow the
   task's branch holds only its base and the done tasks it depends on: the
   guarded files are its lineage's (`acceptance.branch_lineage`), a sibling
   task's test file is guarded only when it is on the branch's disk, one that
   is not is recorded in `acceptance.not_on_branch` and neither restored nor
   run, and the sprint's integration runs the whole suite (#329
   retrospective). `services/evidence/criteria_tests.py`
   holds the reading of the runner's output and the two checks. The same
   output is read for tests named after an OWASP WSTG test
   (`test_wstg_athz_02_…`, `services/evidence/wstg_tests.py`, issue #539):
   the Verifier's `wstg_tests` check records them as items the project's
   WSTG checklist reads, and never changes the verdict. A criterion
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
   an area's words never mark prose, `.md`, `.mdx`, `.rst` or `.txt`;
   a producer may raise it with a reason, never lower it). An approved task
   whose tier is high waits for a person at every autonomy level, level 3
   included: the inbox lists it with `risk_hold`, a Manager accepts it (marks
   it done) or sends it back, and it counts neither as blocked in the
   procedures' numbers nor as a task to carry over on the company's own. Before any of that, the text checks on the
   deliverable (length, sections, balanced fences — `_validate_minimum_deliverable`)
   send it back with the reason as `Quality gate`. Every send-back, the
   quality gate's, the delivery gate's and QA's, counts against one budget per
   task, `MAX_REWORKS` (default 3, issue #766; a person's send-back resets
   it); when it is spent the task is blocked and escalated to the
   ScrumMaster. A rework asks the agent to change only what the problem
   needs on the task branch, never every file in full. QA is shown the
   Verifier's checks and the end of the test output beside the branch
   (`worker/review_evidence`), and before its verdict it may read the
   delivery itself: the read-only workspace tools (list, read, search,
   find_file) on the folder the verification recorded, for
   `QA_REVIEW_TOOL_ROUNDS` rounds (default 3), each call an observation on its
   turn (`worker/qa_review_reads`). The verdict's form and parser do not
   change; without a recorded delivery or a daemon the review is one call. A sprint closes when every task is settled: done, or blocked
   until a person acts (nothing the worker retries, re-reviews or an operator
   froze). Blocked tasks stay on the board, appear in the retrospective and
   count in the procedures' `blocked_rate`; approvals close the sprint on the
   spot, and each review cycle (every 30 seconds) closes every active sprint
   that has settled, whether its last open task got blocked or a person moved
   it to Done or Discarded on the board. A sprint that settles with no task
   done and at least one blocked pauses instead of completing, with the
   reason on the sprint (`sprint_close.nothing_done_reason`); a person
   resumes it once the blocked tasks are unblocked, done or discarded. A
   paused, planned or cancelled sprint is not closed.
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
8. When a sprint closes, each task's observed effort is read from its own
   records (tokens the proxy reported, claimed to done, rework, review
   failures, files the daemon wrote) and stored as an `effort_observed` event,
   the side the difficulty estimate is scored against
   (`services/models/effort.py`, `services/models/calibration.py`, issue
   #534; [models](contexts/models.md)). Then procedures are measured and proposed
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
   - Then the **project wiki** (`services/loop/wiki_close.py`,
     [design](archive/design/PROJECT_WIKI.md)): the TechWriter (else the ScrumMaster)
     updates the pages the sprint's verified runs touched, at most
     `WIKI_COMPILE_MAX_PAGES`, from the runs and the files the daemon observed
     them write, the retrospective and the board's active notes, in the
     project's language; every citation is resolved against the records (an
     unknown one rejects the page; an uncited statement, or a person's edit in
     force, makes the version a proposal). The decisions page is written from
     the approved architecture without a model. The deterministic lint then
     marks pages `stale` (a cited file missing or changed, the module changed by
     a later verified run, two pages contradicting each other on a fact), and,
     with the company's setting on, the compiled pages go to `docs/wiki/`
     through the daemon. A failure is recorded (`wiki_compiled`) and never stops
     the close.
9. With `AGENT_GRAPH_ENABLED=true`, `agent_worker` also runs the graph kickoff for
   the sprint; its transcript is ingested as an `AgentRun` (`le-fabrique-graph`).
   A failed kickoff never blocks the sprint.
10. Engram events go to Neo4j for episodic memory; embeddings to pgvector for RAG.

# La Fabrique backend: contexts and their dependency rule

Architecture v1 for the specification *Backend contexts and their dependency rule*. Written by La Fabrique from the approved architecture; edit it there, not here.

## Overview

The backend's services are divided into fourteen contexts, each with one responsibility and one folder under backend/app/services. They form layers: contracts (the shapes several contexts read) and platform (audit, locks, the event stream, memory) at the bottom; the task record, the models and the evidence above them; the workspace and the tools that reach the daemon; the planning, the learning and the producers; the task loop that drives them; the release (merge, publish, integrate, undo) and the integrations on top of the loop; and the management of the company, which may read everything. The evidence context, which holds the Verifier, imports only contracts and platform, so what judges a run never depends on the code that produced it or on the daemon that wrote it. The rule is data (docs/architecture/contexts.json) checked by scripts/check_architecture.py in CI; the 20 imports that break it today are an allowlist that only shrinks. No code moves in the change that adopts this: the migration moves one context per pull request, contracts first.

## Modules

### MOD-1: contracts

- Where: `backend/app/services/contracts/`
- Responsibility: The shapes several contexts exchange, as plain data and pure functions: the verdict names, the file manifest of a deliverable (parse_file_manifest, the allowed artifact extensions), SEARCH/REPLACE edit blocks, the daemon's delivery reply as the evidence reads it, the task record's fields, the role catalogue and the standards catalogue. It performs no I/O and imports no other context; it never decides anything.
- Covers: REQ-1, REQ-3

### MOD-2: platform

- Where: `backend/app/services/platform/`
- Responsibility: The infrastructure every context may use: the audit writes (AgentService), the advisory locks across replicas, idempotency claims, the Glassroom publisher, the KPI turn records, token usage, API tokens, run secrets, the health check, the knowledge graph reader and, after the migration, the engram processor from backend/memory. It knows nothing of tasks, runs or companies' decisions.
- Depends on: MOD-1 (contracts)
- Covers: REQ-1, REQ-5

### MOD-3: tasks

- Where: `backend/app/services/tasks/`
- Responsibility: A task's record: its events and their hash chain (task_events), the projections that answer the board's questions (task_projection, TaskRecord), the comment-thread readers (board), the dependencies between a sprint's tasks. It records and answers; it never dispatches, reviews or calls a model.
- Depends on: MOD-1 (contracts), MOD-2 (platform)
- Covers: REQ-1, REQ-3

### MOD-4: models

- Where: `backend/app/services/models/`
- Responsibility: Which model a role runs and what it costs: the models.dev catalogue, prices, the company's provider connections and model settings, difficulty routing, the suggestions per role and the records over completed sprints, and, after the migration, the role defaults from backend/llm/model_factory.py. It never calls a provider; the LLM proxy does.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks)
- Covers: REQ-1, REQ-5

### MOD-5: evidence

- Where: `backend/app/services/evidence/`
- Responsibility: Judging a run from what was observed: the Verifier's checks and verdict, the risk tier, the quality standards check, acceptance tests per criterion, the context-read check, the application and browser checks as read from the daemon's reply, SecOps scans, the gate before QA, the changes of a run and the ingestion of AgentRun records. It reads the daemon's reply as data from contracts; it never calls the daemon, the loop, the tools or a producer.
- Depends on: MOD-1 (contracts), MOD-2 (platform)
- Covers: REQ-1, REQ-2, REQ-3

### MOD-6: workspace

- Where: `backend/app/services/workspace/`
- Responsibility: The client of the workspace daemon over NATS: requests and their errors, the delivery test run, the project inspection and snapshot, the sandbox modes, the stack profiles and the Git workflow's delivery spec. It sends and returns observations; it never judges them.
- Depends on: MOD-1 (contracts), MOD-2 (platform)
- Covers: REQ-1, REQ-2

### MOD-7: tools

- Where: `backend/app/services/tools/`
- Responsibility: What a model may call during a task: the bounded tool loop, MCP servers and their configuration, the Developer's workspace tools and their replies, the checked edits and the lint after an edit, the repository map, the research tools and the compressed history. Each call is recorded as an observation; the tools never decide a verdict.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-5 (evidence), MOD-6 (workspace)
- Covers: REQ-1

### MOD-8: learning

- Where: `backend/app/services/learning/`
- Responsibility: How the company learns from its sprints: procedures kept or reverted by numbers, the retrospective, the ScrumMaster's triage and proposals, the Architect's record. It reads the task record and asks models through the proxy; it never moves a task.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks), MOD-4 (models)
- Covers: REQ-1

### MOD-9: planning

- Where: `backend/app/services/planning/`
- Responsibility: What is built and in which tasks: the specification and its check, the architecture and its check, their approval, the PO's sprint plan from a specification, the sprint planner and team, the project's stack discovery. It writes plans and documents; it never runs a task.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks), MOD-4 (models), MOD-5 (evidence), MOD-6 (workspace), MOD-7 (tools)
- Covers: REQ-1

### MOD-10: producers

- Where: `backend/app/services/producers/`
- Responsibility: The interchangeable producers of work other than the task worker's own model call: the terminal assistants and CLI producers, the optional LangGraph kickoff (agent_worker, and backend/agents/graph.py after the migration) and the GitHub adapter that ingests pull requests as runs. A producer's output is a claim handed to the evidence.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks), MOD-4 (models), MOD-5 (evidence), MOD-6 (workspace), MOD-7 (tools)
- Covers: REQ-1, REQ-5

### MOD-11: loop

- Where: `backend/app/services/loop/`
- Responsibility: Driving a sprint's tasks: the task worker (dispatch, prompt, execution, delivery, recording, review, closing), the review digest, the edit plan, board moves, carry-over, replicas, sprint control, close and archive, a person's run review, and the company bootstrap. It decides what happens next from the evidence; it never computes a verdict itself.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks), MOD-4 (models), MOD-5 (evidence), MOD-6 (workspace), MOD-7 (tools), MOD-8 (learning), MOD-9 (planning), MOD-10 (producers)
- Covers: REQ-1, REQ-6

### MOD-12: release

- Where: `backend/app/services/release/`
- Responsibility: What leaves the factory once accepted: publishing a branch, merging locally, a sprint's integration and its jobs and events, undoing a delivery from its snapshot, and bringing a request's comments back. It starts when the loop has closed work, through an event, never from inside the loop.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks), MOD-4 (models), MOD-5 (evidence), MOD-6 (workspace), MOD-9 (planning), MOD-11 (loop)
- Covers: REQ-1

### MOD-13: integrations

- Where: `backend/app/services/integrations/`
- Responsibility: Telling the outside world: the company's notification webhook and the outbox processor that posts it. It listens to changes of the task record and the evidence; nothing below it imports it.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks), MOD-5 (evidence)
- Covers: REQ-1

### MOD-14: management

- Where: `backend/app/services/management/`
- Responsibility: The owner as a client: the Manager agent with its inbox, policy, rules, observations, actions and record, the client's brief and its mail, the Reviewer agent and the Support agent. It may read and act through every other context; no other context imports it.
- Depends on: MOD-1 (contracts), MOD-2 (platform), MOD-3 (tasks), MOD-4 (models), MOD-5 (evidence), MOD-6 (workspace), MOD-7 (tools), MOD-8 (learning), MOD-9 (planning), MOD-10 (producers), MOD-11 (loop), MOD-12 (release), MOD-13 (integrations)
- Covers: REQ-1

### MOD-15: architecture rule

- Where: `scripts/check_architecture.py`
- Responsibility: The data and the check of this architecture: the contexts with their modules and allowed dependencies, the allowlist of edges that break the rule today, and the stdlib checker that reads the imports with ast and fails CI on a new violation, an unassigned module or a cycle. It imports no backend code.
- Covers: REQ-2, REQ-4, REQ-7

## Interfaces

### IF-1: Verdicts

- Kind: schema; provided by MOD-1 (contracts); used by MOD-5, MOD-3, MOD-11, MOD-7, MOD-14

```
VERDICT_VERIFIED = "verified"; VERDICT_REFUTED = "refuted"; VERDICT_UNVERIFIABLE = "unverifiable"
Gate authors: QUALITY_GATE_AUTHOR = "Quality gate", DELIVERY_GATE_AUTHOR = "Verifier"
(today in services/platform/orchestration_metrics.py and services/tasks/board.py; re-exported there after the move)
```

### IF-2: Delivery manifest

- Kind: function; provided by MOD-1 (contracts); used by MOD-5, MOD-7, MOD-11, MOD-10

```
parse_file_manifest(deliverable: str) -> list[{"path": str, "content": str, ...}]
expected_hashes(targets) -> dict[path, sha256]
ALLOWED_ARTIFACT_EXTENSIONS: frozenset[str]
edit_blocks.parse(body) -> list[dict] for blocks marked `edit`
(today task_worker.parse_file_manifest, imported lazily by standards, verification, acceptance and workspace_edits)
```

### IF-3: Delivery reply

- Kind: schema; provided by MOD-1 (contracts); used by MOD-6, MOD-5, MOD-11, MOD-12

```
DeliveryReply = {files: [{path, sha256, change: added|modified|unchanged, diff?}], git?: {branch, commit, parent, merged?}, snapshot?: {id, files}, test_run?: {command, exit_code, output, output_head?, stream_truncated?, sandbox}, app_run?: {...}, error?: str}
The workspace builds it from the daemon's answer; the evidence only reads it.
```

### IF-4: Task record

- Kind: schema; provided by MOD-1 (contracts); used by MOD-3, MOD-8, MOD-11, MOD-12, MOD-14

```
TaskEventShape = {event_id, event_type, actor, author, text, task_status, payload, seq, prev_hash, chain_hash}
TaskRecordView protocol: deliverable(), pending_rework_feedback(), block_reason(), awaits_risk_acceptance(), waits_for_a_person(), is_settled(), next_step()
Implemented by tasks.TaskRecord; read by learning, loop, release and management.
```

### IF-5: Platform services

- Kind: function; provided by MOD-2 (platform); used by MOD-3, MOD-4, MOD-5, MOD-6, MOD-7, MOD-8, MOD-9, MOD-10, MOD-11, MOD-12, MOD-13, MOD-14

```
locks.held(engine, name, ident) -> context manager | None
glassroom.publish_glassroom(nats, sprint_id, event)
orchestration_metrics.record_agent_turn(...)
AgentService(...).audit_interaction(...)
memory_graph readers raise GraphNotRecorded when the graph is off
```

### IF-6: Task record and events

- Kind: function; provided by MOD-3 (tasks); used by MOD-4, MOD-8, MOD-9, MOD-10, MOD-11, MOD-12, MOD-13, MOD-14

```
task_events.record(session, item, kind, actor, text=, author=, status=, run_id=, verification_id=, payload=) -> TaskEvent
task_events.verify_chain(session, item_id) -> dict
task_projection.TaskRecord / records_for(session, items)
dependencies.is_ready(session, item) -> bool
```

### IF-7: Model choice

- Kind: function; provided by MOD-4 (models); used by MOD-8, MOD-9, MOD-10, MOD-11, MOD-14

```
company_models.resolve(session, company_id, role, configured) -> str
model_routing.estimate_difficulty(...) -> int
model_suggestions.suggest(...) -> one suggestion per role
routing_record / model_record / calibration over completed sprints
effort.record_sprint(session, sprint_id) -> int (at sprint close)
```

### IF-8: Verification

- Kind: function; provided by MOD-5 (evidence); used by MOD-7, MOD-9, MOD-10, MOD-11, MOD-12, MOD-13, MOD-14

```
verification.verify_agent_run(session, run, evidence: dict | None) -> Verification
verification.aggregate_verdict(checks) -> verified | refuted | unverifiable
delivery_gate.gate(engine, company_id, item_id) -> (reason, author) | None
risk.classify(paths, declared_risk=None) -> dict
agent_run_service.ingest_agent_run(session, AgentRunCreate) -> AgentRun
```

### IF-9: Daemon client

- Kind: function; provided by MOD-6 (workspace); used by MOD-7, MOD-9, MOD-10, MOD-11, MOD-12, MOD-14

```
async daemon_request(nats, subject, payload, timeout) -> dict  (raises DaemonUnavailable, DaemonError)
async run_delivery_tests(nats, company, root, ...) -> test_run reply
inspect_project(...), materialized_snapshot(...)
git_workflow.delivery_spec(engine, sprint, item) -> dict | None (target: takes the dependency branches as a parameter instead of importing tasks.dependencies)
```

### IF-10: Tool loop

- Kind: function; provided by MOD-7 (tools); used by MOD-9, MOD-10, MOD-11, MOD-14

```
mcp_tools.chat_with_tools(client, messages, toolbox, rounds) -> (answer, tool_calls)
developer_tools.toolbox_for(session, company, item, role) -> CombinedToolbox
research_tools.toolbox_for_request(...)
async repo_map.fetch_section(nats, root) -> str
```

### IF-11: Procedures and triage

- Kind: function; provided by MOD-8 (learning); used by MOD-11, MOD-14

```
procedures.prompt_context(engine, company_id, role) -> str
procedures.evaluate(session, company_id) -> list[Procedure]
scrum_master.triage(...) -> the ScrumMaster's decision, or None without a usable answer
retrospective.build(engine, sprint_id) -> dict
```

### IF-12: Specification and architecture

- Kind: function; provided by MOD-9 (planning); used by MOD-11, MOD-12, MOD-14

```
spec_check.normalize(dict) -> dict; spec_check.check(dict) -> {ok, blocking, warnings, findings}
arch_check.normalize(dict) -> dict; arch_check.check(dict, requirement_ids) -> {...}; arch_check.as_markdown(arch, spec_title, version) -> str
spec_planning.plan(session, spec) -> tasks with covers; spec_planning.requirements_context(...) -> str
```

### IF-13: Producer

- Kind: function; provided by MOD-10 (producers); used by MOD-11, MOD-14

```
async console_producer.produce(...) -> deliverable text (a claim)
process_agent_tasks(nats) for the optional graph kickoff
github_adapter.pr_to_agent_run_payload(pr) -> AgentRunCreate
```

### IF-14: Sprint loop

- Kind: event; provided by MOD-11 (loop); used by MOD-12, MOD-14

```
async process_backlog_tasks(nats) consumes task.assign
sprint_control.pause / stop / dispatch_todo(session, sprint)
emits sprint.closed {nats_client, engine, sprint_id} in-process (platform.hooks.emit) when a sprint closes, its first close or one after a reopening, where it called integration_jobs.schedule_after_close; moving the event to the outbox, durable and with company_id and first_close, is a later, deliberate change
```

### IF-15: Release actions

- Kind: function; provided by MOD-12 (release); used by MOD-14

```
integration_jobs.on_sprint_closed, registered by integration_jobs.install() at startup, calls schedule_after_close(nats_client, engine, sprint_id) on sprint.closed
git_publish.publish_spec / record_publication
local_merge.run_merge_spec / record_run_merge
delivery_undo.undo_spec / record_undo
```

### IF-16: Notification webhook

- Kind: event; provided by MOD-13 (integrations); used by MOD-14

```
webhooks.install() registers a before_flush listener; events task_done, task_blocked, sprint_closed, run_refuted queued as OutboxEvent webhook_post, signed X-Fabrique-Signature-256: sha256=<HMAC>
```

### IF-17: contexts.json

- Kind: schema; provided by MOD-15 (architecture rule)

```
{"contexts": {name: {"path": str, "responsibility": str, "depends_on": [context], "modules": [flat module stem]}}, "http_adapters": {"modules": ["<context>.<stem>"]}, "allowlist": ["source -> target"]}
python3 scripts/check_architecture.py [--update] -> exit 0 | 1
```

## Decisions

### ADR-1: Contexts depend on each other in one direction, and the evidence on contracts and platform only

**Context.** backend/app/services holds 118 flat modules with 335 import edges and a 26-module cycle in which the Verifier (verification, acceptance, standards), the loop (task_worker) and the daemon client (workspace_daemon) import each other. The positioning makes the evidence the product: what judges a run must not depend on what produced it.
**Decision.** Fourteen contexts form a directed acyclic graph recorded in docs/architecture/contexts.json: contracts; platform; tasks, models, evidence; workspace; tools; learning; planning, producers; loop; release, integrations; management. A module may import its own context and the contexts its context names in depends_on, nothing else. evidence names contracts and platform only. Cycles inside one context are tolerated and listed (app_check, browser_smoke, delivery_gate, verification; arch_author and spec_planning; sprint_planner and sprint_team; manager_inbox and manager_inbox_extra); a cycle across contexts is not.
**Consequences.** Twenty edges break the rule today and are allowlisted: evidence to workspace (5), evidence to loop (3), producers to loop (3), evidence to tools (3), workspace to evidence (2), workspace to tasks, loop to release, evidence to producers and tools to loop (1 each). Removing them breaks the 26-module cycle into the four intra-context ones. A new feature has to say which context it lives in before it is written.

### ADR-2: A contracts context holds the shapes the cycle is made of

**Context.** Most of the 20 edges exist to reach a shape, not behaviour: standards, verification, acceptance and workspace_edits import task_worker lazily for parse_file_manifest and ALLOWED_ARTIFACT_EXTENSIONS; evidence imports the verdict names from orchestration_metrics; workspace_daemon imports criteria_tests and app_check to shape its reply; verification imports mcp_tools, research_tools and console_clis for the shape of their observations.
**Decision.** Create backend/app/services/contracts/ with verdicts.py, manifest.py (parse_file_manifest, expected_hashes, ALLOWED_ARTIFACT_EXTENSIONS, moved out of task_worker.py), delivery_reply.py (the daemon reply and the tool-call observation as TypedDicts with their readers), task_record.py (the event shape and the TaskRecordView protocol), and move roles, edit_blocks, standards_catalogue and sandbox_schemas into it. contracts imports nothing else in services, performs no I/O and holds no decision.
**Consequences.** The contracts step alone removes the 4 edges to task_worker from evidence and tools and lets the observation readers move to evidence. Old names stay importable through re-exports (task_worker.parse_file_manifest, orchestration_metrics.VERDICT_*) until their last importer is changed. contracts must stay small: a function that decides belongs to its context, not here.

### ADR-3: task_worker.py is split by loop stage inside the loop context

**Context.** task_worker.py has 3,299 lines (process_backlog_tasks alone about 500, _review_cycle_body about 640) and imports 37 services; the size baseline lets it only shrink. Tests patch many of its private names (task_worker._execute_task, _deliver_artifacts, ...).
**Decision.** Split it into backend/app/services/loop/worker/: intake.py (consumer, claim, queue markers, dispatch_todo), prompt.py (task context, memory, lessons, procedures), execute.py (LLM call, Architect consultation, edit plan), deliver.py (artifacts to the daemon, queued deliveries, delivery results), record.py (AgentRun recording and re-verification), review.py (review cycle, QA call, quality gate), close.py (sprint finalization, follow-up, retrospective, procedures), messages.py (agent messages, triage). Each under 600 lines; the two long functions are cut into steps. task_worker.py stays as a shim re-exporting the public names.
**Consequences.** One pull request per stage, each moving its functions and the test patch targets to the stage module that owns them, so a patch never lands on a re-export. The file-size baseline entry for task_worker.py drops with each stage and is removed when the shim is under 100 lines. The shim goes when nothing imports it.

### ADR-4: The parallel trees: merge what production imports, delete what only tests import

**Context.** Beside backend/app: backend/agents (7 files, 1,426 lines), backend/orchestration (1, 115), backend/llm (4, 939), backend/memory (1, 543), backend/eval (4, 383) and src/ (6, 368). Checked with grep on imports across backend, scripts and the workflows.
**Decision.** orchestration: delete (router.py is never included by main.py; only tests/test_circuit_breaker.py imports it). llm: merge model_factory.py into models (imported by company_models, manager_agent, model_routing, reviewer_agent, support_agent, roles, agents/graph, memory); delete circuit_breaker, fallback and retry (only tests and the orchestration router). agents: merge graph.py and tools/openproject_tools.py into producers (agent_worker imports graph); delete po_agent, po/prompts, python_dev_agent, ast_pre_validator, qa_context_optimizer (only tests). memory: merge engram_processor.py into platform (task_worker and agents/graph import it). eval: move verificador to bench/verifier/ (a measurement, only its test imports it). src/: delete (nothing imports it).
**Consequences.** About 1,400 lines and their tests leave the repository; the tests of deleted modules are deleted with them, the tests of merged ones follow their imports. agents/graph.py (720 lines) keeps its size baseline at its new path and may only shrink. The backend Dockerfile stops copying llm/, agents/ and memory/. ADR 0010 (the self-correction loop of src/ and agents/ast_pre_validator) and ADR 0011 (llm/fallback's FallbackRouter) describe code no production path runs; they are marked superseded by this decision in the step that deletes it, and the fallback that does run stays the LLM proxy's tiers.

### ADR-5: Migrate one context per pull request, bottom up, behind re-export shims and a ratchet

**Context.** The backend must keep working while 118 modules move; the e2e and the unit tests patch modules by their current path.
**Decision.** Order: (1) contracts, removing the edges to task_worker; (2) platform with backend/memory; (3) tasks; (4) models with llm/model_factory; (5) evidence, removing its edges to workspace, tools, loop and producers; (6) workspace; (7) tools; (8) learning; (9) planning; (10) producers with agents/graph; (11) loop, splitting task_worker by stage and replacing its call to integration_jobs by the sprint.closed event; (12) release; (13) integrations; (14) management; (15) delete the dead trees and the shims nothing imports. Each pull request moves the files of one context with git mv, leaves services/<module>.py as a shim (from services.<context>.<module> import *, plus the private names tests patch), changes no behaviour, and runs scripts/check_architecture.py --update so the allowlist shrinks.
**Consequences.** Every step is green on the backend tests, ruff 0.17.1, check_file_sizes.py and check_architecture.py, and can be reverted alone. The shims cost a few hundred one-line files for a while; the last step removes them. Nothing new may be added to the allowlist by any step.

### ADR-6: HTTP only in named adapter modules

**Context.** Service modules called HTTP libraries inline (the models.dev catalogue, a provider's model list, the notification webhook, the OpenProject tool, the research fetch, the pre-QA sandbox, the GitHub adapter, the health probes, the MCP client), so a domain decision and a transport exception lived in one function and nothing stopped the next one. Issue #511; the rule's idea is AixleHQ/flow's "no HTTP in domain services" design rule (Apache-2.0, design only).
**Decision.** Under backend/app/services, only the modules listed in http_adapters (docs/architecture/contexts.json) may import an HTTP library (httpx, httpx2, requests, aiohttp, urllib3, urllib.request). An adapter sends the request and returns data, raising named errors rather than transport exceptions; the domain module calls it and decides. scripts/check_architecture.py fails on an HTTP import outside the list and reports a listed module that no longer imports one, which --update drops.
**Consequences.** Five modules moved their HTTP into a new adapter beside them (integrations/webhook_client, models/provider_client, producers/openproject_client, tools/research_http, workspace/sandbox_http); four already were adapters or probes and are listed as they are (models/model_catalog, platform/healthcheck, producers/github_adapter, tools/mcp_client). The list may only shrink, unless a new adapter is reviewed in the pull request that adds it.

### ADR-7: Each context has a public interface, and other contexts import only it

**Context.** The dependency rule (ADR 0012) says which contexts may import which, not which of their modules: any module of a context it may import was fair game, so 416 imports across contexts reached 102 of 272 modules, among them a private helper (release/integration_publish imported evidence/review_changes._workspace_file), a lookup kept in the wrong context (management imported business_unit_id from planning/spec_author) and a step taken past its entry point (tools/edit_lint resolved the stack profile itself beside workspace/project_profile). Nothing told a sub-step from an interface, so the next import of an internal would pass too.
**Decision.** Each context in docs/architecture/contexts.json declares its public modules (public, relative to the context; ["*"] for contracts, which exists to be shared; the context's own __init__ is always public). A module may import, from another context its context may import, only that context's public modules. A declared list was chosen over services/<context>/__init__.py as the interface because it moves no code: the fourteen package __init__ files are docstrings, importing through them would rewrite all 416 imports, make every importer load the whole context at import time (the database models, the NATS client) and break the tests that patch the defining module, while the modules other contexts already use deliberately are the interface as it stands. scripts/check_architecture.py fails on an import of a non-public module of another context that is not in interface_allowlist (named by stems, like allowlist), reports an entry that no longer violates, and --update drops it and never adds one. Code outside backend/app/services (routers, models, the daemon) is out of scope.
**Consequences.** Declared public: the modules other contexts used by design, 94 across twelve contexts, 15 of them contracts' (integrations and management declare none: no other context imports them). Against those lists 9 imports reached internals. 3 were fixed in this change: business_unit_id moved to models/company_models, edit_lint calls project_profile.resolved, and review_changes._workspace_file became the public workspace_file. The other 6 were allowlisted, then fixed: loop/delivery_checks and loop/delivery_suites, which composed the delivery's daemon commands from workspace's dependency_install, run_logs, folder_commands and stack_roots, now go through one public entry point, workspace/delivery_commands (the install, the test run with its full log, the folders to run, the command in a folder, the application check), while the loop keeps choosing the commands and reading the output against the criteria; loop/task_routing now calls model_routing, whose route already took the estimate and the policy: estimate_task computes the estimate from the task's plan and board (the loop passes the architecture, which models may not read) and effective_policy names the company's policy, so difficulty and routing_policy stay internal. Nothing remains allowlisted. Making a module public is a reviewed change to contexts.json in the pull request that needs it.

## Conventions

- Every module of backend/app/services belongs to one context, listed in docs/architecture/contexts.json or placed in services/<context>/; a new module names its context in the same pull request.
- Import across contexts only along depends_on; to reach upward, publish an event through the outbox or take a callable as a parameter.
- The evidence reads observations as data from contracts and never calls the daemon, a model, a tool or a producer.
- No new module over 600 lines (scripts/check_file_sizes.py); a moved file over the limit keeps its baseline entry at its new path and may only shrink.
- A move leaves a shim at the old path until its last importer changes; tests patch the module that defines a name, never a shim.
- scripts/check_architecture.py --update may only remove allowlist entries; a pull request that needs a new entry changes the architecture first.
- Claims and observations never share a field; the contracts' shapes keep them apart as the positioning requires.
- HTTP only in the adapter modules listed in http_adapters (contexts.json): an adapter returns data and raises named errors, a domain module calls it; a new adapter is reviewed in the pull request that adds it.
- Import another context only through its public modules (contexts.json, public); a module becomes public in a reviewed change when it really is an interface, never to silence the check, and interface_allowlist only shrinks.

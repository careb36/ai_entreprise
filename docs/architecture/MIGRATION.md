# The migration to contexts (ADR 0016)

A record of how `backend/app/services` was moved into fourteen context folders, one context per pull request (issue #469, ADRs 0012 to 0016; ADR 0016 was accepted on 2026-11-09). The text was written step by step in `CLAUDE.md` as each step merged, and was moved here unchanged on 2026-11-11, when `CLAUDE.md` became an [index](../../CLAUDE.md). The steps appear in the order they were written. It is history: the current state is in the index, in [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) and in [`contexts.json`](contexts.json).

## The rule as it was adopted

**Contexts and the dependency rule** (issue #469, `docs/ARCHITECTURE.md`, ADRs 0012–0016). `backend/app/services` is divided into fourteen contexts (contracts, platform, tasks, models, evidence, workspace, tools, learning, planning, producers, loop, release, integrations, management), each with a folder `services/<context>/` and the contexts it may import; the evidence imports only contracts and platform. The assignment and the rule are data, `docs/architecture/contexts.json`, checked by `scripts/check_architecture.py` (stdlib, `ast`; `tests/test_check_architecture.py`, and a step of `backend-tests.yml`): a module in no context, a cycle between contexts or a new import against the rule fails; the 20 edges that broke it at adoption are an allowlist that `--update` only shrinks. A new module names its context in the same pull request. The records the product's own checks approved are `docs/architecture/specification.json` and `architecture.json` (post them with `POST /v1/specifications` and `POST /v1/architectures`); `docs/ARCHITECTURE.md` is their rendering by `arch_check.as_markdown`. The migration order is ADR 0016, one context per pull request behind re-export shims.

## The contexts at adoption

Generated from `docs/architecture/contexts.json` (the checker's data) at adoption, 2026-11-09. It was the appendix of ADR 0012 until that ADR became a rendering of the architecture record (issue #329), which has no place for an appendix.

| Context | Folder | May import | Modules | Lines |
|---|---|---|---|---|
| contracts | `backend/app/services/contracts/` | — | 4 | 551 |
| platform | `backend/app/services/platform/` | contracts | 10 | 1,422 |
| tasks | `backend/app/services/tasks/` | contracts, platform | 5 | 1,991 |
| models | `backend/app/services/models/` | contracts, platform, tasks | 9 | 1,628 |
| evidence | `backend/app/services/evidence/` | contracts, platform | 13 | 3,661 |
| workspace | `backend/app/services/workspace/` | contracts, platform | 6 | 2,086 |
| tools | `backend/app/services/tools/` | contracts, platform, evidence, workspace | 13 | 3,311 |
| learning | `backend/app/services/learning/` | contracts, platform, tasks, models | 4 | 1,057 |
| planning | `backend/app/services/planning/` | contracts, platform, tasks, models, evidence, workspace, tools | 11 | 4,492 |
| producers | `backend/app/services/producers/` | contracts, platform, tasks, models, evidence, workspace, tools | 6 | 2,098 |
| loop | `backend/app/services/loop/` | contracts, platform, tasks, models, evidence, workspace, tools, learning, planning, producers | 12 | 5,963 |
| release | `backend/app/services/release/` | contracts, platform, tasks, models, evidence, workspace, planning, loop | 9 | 1,978 |
| integrations | `backend/app/services/integrations/` | contracts, platform, tasks, evidence | 2 | 478 |
| management | `backend/app/services/management/` | contracts, platform, tasks, models, evidence, workspace, tools, learning, planning, producers, loop, release, integrations | 14 | 4,268 |
| **total** | | | **118** | **34,984** |

## Appendix: module assignment

| Context | Modules |
|---|---|
| contracts | `edit_blocks`, `roles`, `sandbox_schemas`, `standards_catalogue` |
| platform | `agent_service`, `api_tokens`, `glassroom`, `healthcheck`, `idempotency`, `locks`, `memory_graph`, `orchestration_metrics`, `run_secrets`, `token_usage` |
| tasks | `board`, `dependencies`, `events`, `task_events`, `task_projection` |
| models | `company_models`, `model_catalog`, `model_prices`, `model_record`, `model_routing`, `model_suggestions`, `provider_connections`, `routing_record`, `run_cost` |
| evidence | `acceptance`, `agent_run_service`, `app_check`, `browser_smoke`, `context_read`, `criteria_tests`, `delivery_gate`, `governance`, `review_changes`, `risk`, `secops_checks`, `standards`, `verification` |
| workspace | `git_workflow`, `sandbox`, `sandbox_manager`, `stack_profiles`, `workspace_daemon`, `workspace_snapshot` |
| tools | `developer_tool_replies`, `developer_tool_schemas`, `developer_tools`, `edit_lint`, `mcp_client`, `mcp_config`, `mcp_tools`, `repo_map`, `research_registries`, `research_schemas`, `research_tools`, `tool_history`, `workspace_edits` |
| learning | `architect`, `procedures`, `retrospective`, `scrum_master` |
| planning | `arch_author`, `arch_check`, `discovery_service`, `document_approval`, `spec_author`, `spec_check`, `spec_diff`, `spec_impact`, `spec_planning`, `sprint_planner`, `sprint_team` |
| producers | `agent_worker`, `console_assistants`, `console_capabilities`, `console_clis`, `console_producer`, `github_adapter` |
| loop | `answers`, `board_moves`, `carry_over`, `company_service`, `edit_plan`, `replicas`, `review_digest`, `run_review`, `sprint_archive`, `sprint_close`, `sprint_control`, `task_worker` |
| release | `delivery_undo`, `git_publish`, `git_setup`, `integration`, `integration_events`, `integration_jobs`, `integration_publish`, `local_merge`, `review_sync` |
| integrations | `outbox_service`, `webhooks` |
| management | `brief_mail`, `manager_actions`, `manager_actions_extra`, `manager_agent`, `manager_brief`, `manager_inbox`, `manager_inbox_extra`, `manager_observe`, `manager_policy`, `manager_record`, `manager_record_extra`, `manager_rules`, `reviewer_agent`, `support_agent` |

## Appendix: the allowlist at adoption

20 edges break the rule today; they may only be removed.

| Edge | From | To |
|---|---|---|
| `acceptance -> task_worker` | evidence | loop |
| `acceptance -> workspace_daemon` | evidence | workspace |
| `app_check -> sandbox` | evidence | workspace |
| `app_check -> stack_profiles` | evidence | workspace |
| `app_check -> workspace_daemon` | evidence | workspace |
| `console_producer -> carry_over` | producers | loop |
| `console_producer -> edit_plan` | producers | loop |
| `console_producer -> task_worker` | producers | loop |
| `context_read -> developer_tool_schemas` | evidence | tools |
| `git_workflow -> dependencies` | workspace | tasks |
| `standards -> task_worker` | evidence | loop |
| `task_worker -> integration_jobs` | loop | release |
| `verification -> console_clis` | evidence | producers |
| `verification -> mcp_tools` | evidence | tools |
| `verification -> research_tools` | evidence | tools |
| `verification -> sandbox` | evidence | workspace |
| `verification -> task_worker` | evidence | loop |
| `workspace_daemon -> app_check` | workspace | evidence |
| `workspace_daemon -> criteria_tests` | workspace | evidence |
| `workspace_edits -> task_worker` | tools | loop |

## Step 1: contracts

<a id="step-1"></a>

Step 1 is done: `services/contracts/` holds `roles`, `edit_blocks`, `standards_catalogue`, `sandbox_schemas` (moved), `manifest` (`parse_file_manifest`, `expected_hashes`, `ALLOWED_ARTIFACT_EXTENSIONS`, out of `task_worker`, which still re-exports them), `verdicts` (re-exported by `orchestration_metrics`) and `tool_calls` (the workspace tools' server name); it imports nothing else of `services`. Each old flat path (`services/roles.py`…) is a shim that puts the owner module itself in `sys.modules`, so imports and patches reach the owner; a shim stays listed in `contexts.json` under its owner's context and the checker fails if it is listed elsewhere. A module in a context folder belongs to that context by the folder. The allowlist is down to 15 edges and the 26-module cycle to 5 (`app_check`, `browser_smoke`, `delivery_gate`, `verification`, `workspace_daemon`).

## Step 2: platform

<a id="step-2"></a>

Step 2 is done: `services/platform/` holds `agent_service`, `api_tokens`, `glassroom`, `healthcheck`, `idempotency`, `locks`, `memory_graph`, `orchestration_metrics`, `run_secrets` and `token_usage` (each old flat path a shim, as in step 1), and `engram_processor`, moved from `backend/memory/` (ADR 0015); `backend/memory/` is gone (step 15: every importer uses the new path, and the image and compose no longer copy it).

## Step 3: tasks

<a id="step-3"></a>

Step 3 is done: `services/tasks/` holds `board`, `dependencies`, `events`, `task_events` and `task_projection` (each old flat path a shim, as in steps 1 and 2); inside the context they import each other and `platform` by the new paths, and the bench's `scorecard.py` reads `services.tasks.board`.

## Step 4: models

<a id="step-4"></a>

Step 4 is done: `services/models/` holds `company_models`, `model_catalog`, `model_prices`, `model_record`, `model_routing`, `model_suggestions`, `provider_connections`, `routing_record` and `run_cost` (each old flat path a shim, as before), and `model_factory`, moved from `backend/llm/` (ADR 0015); `backend/llm/model_factory.py` is gone (step 15: every importer uses the new path; platform may not import models, so `engram_processor` is handed the model by its callers, `burn_engram(llm=get_llm_for_role("EngramProcessor"))` in the loop's close and `producers/graph.py`), the Support, Reviewer and Manager agents and the LangGraph producer (now `producers/graph.py`) import the new path, `model_catalog`'s snapshot path counts one folder more, `scripts/update_models_snapshot.py` loads the new file, and `backend/llm/` is gone (its `circuit_breaker`, `fallback` and `retry`, which only tests imported, went in step 15), so the image and compose no longer copy it.

## Step 5: evidence

<a id="step-5"></a>

Step 5 is done: `services/evidence/` holds `acceptance`, `agent_run_service`, `app_check`, `browser_smoke`, `context_read`, `criteria_tests`, `delivery_gate`, `governance`, `review_changes`, `risk`, `secops_checks`, `standards` and `verification` (each old flat path a shim, as before), importing each other and `platform` by the new paths, plus two modules split out of other contexts so the Verifier no longer imports them: `sources` (`sources_of`, `packages_in`, `check_sources`, out of `research_tools`) and `console_run` (`check_console_run`, out of `console_clis`), both re-exported where they were. `contracts` gains `sandbox_report` (`check_sandbox`, out of `sandbox`, which re-exports it) and, in `tool_calls`, the research tools' server and tool names (`research_schemas` reads them) and `summarize` (out of `mcp_tools`, which re-exports it). The checker names an edge by its modules' stems whichever folder holds them, so an allowlisted edge keeps its name when its module moves. The allowlist is down to 11 edges; the evidence still imports the workspace for real daemon calls (`acceptance -> workspace_daemon`, `app_check -> workspace_daemon`, `sandbox`, `stack_profiles`), and `workspace_daemon` imports `app_check` and `criteria_tests` back, the one cross-context cycle left (`app_check`, `browser_smoke`, `delivery_gate`, `verification`, `workspace_daemon`) until the workspace step moves the application check's orchestration out of the evidence.

## Step 6: workspace

<a id="step-6"></a>

Step 6 is done: `services/workspace/` holds `git_workflow`, `sandbox`, `sandbox_manager`, `stack_profiles`, `workspace_daemon` and `workspace_snapshot` (each old flat path a shim, as before), plus `project_profile` (`Context`: a delivery's company settings and resolved stack profile, `detect_profile`, `resolved`) and `dependency_install` (`install_if_needed`, `changed_manifests`), both out of `app_check`. The workspace sends requests and returns observations; what combines the daemon's replies with the evidence moved to the loop, `services/loop/delivery_checks.py` (`run_delivery_tests` and `_run_tests` out of `workspace_daemon`, `check_application` and `_earlier_failure` out of `app_check`, `guard_test_files` out of `acceptance`; `task_worker` and `integration` call it there), and `app_check` keeps only the Verifier's `dependencies_installed`, `application_started` and `e2e_run` checks. `browser_smoke.with_browser_checks` takes the profile reader as an argument (`spec_planning` passes `project_profile.resolved`), `edit_lint` reads `project_profile.detect_profile`, and `depends_on_of` moves to `contracts/task_dependencies.py` (`tasks.dependencies` re-exports it) so `git_workflow` no longer imports the tasks. The allowlist is down to 4 edges (`console_producer -> carry_over`, `edit_plan`, `task_worker`; `task_worker -> integration_jobs`), and the evidence no longer imports the workspace.

## Step 7: tools

<a id="step-7"></a>

Step 7 is done: `services/tools/` holds `developer_tool_replies`, `developer_tool_schemas`, `developer_tools`, `edit_lint`, `mcp_client`, `mcp_config`, `mcp_tools`, `repo_map`, `research_registries`, `research_schemas`, `research_tools`, `tool_history` and `workspace_edits` (each old flat path a shim, as before), importing each other, the evidence (`context_read`, `delivery_gate`, `sources`) and the workspace (`sandbox`, `workspace_daemon`, `project_profile`) by the new paths. The move uncovered no edge against the rule: the tools already imported only contracts, platform, evidence and workspace, so the allowlist stays at 4 edges.

## Step 8: learning

<a id="step-8"></a>

Step 8 is done: `services/learning/` holds `architect`, `procedures`, `retrospective` and `scrum_master` (each old flat path a shim, as before), importing each other, the tasks (`board`, `task_events`, `task_projection`) and the models (`company_models`) by the new paths. The move uncovered no edge against the rule: the learning modules already imported only tasks and models, so the allowlist stays at 4 edges. `app.services.scrum_master` is now the same module as `services.learning.scrum_master`, so a test importing it gets conftest's stubbed proxy client like every other.

## Step 9: planning

<a id="step-9"></a>

Step 9 is done: `services/planning/` holds `arch_author`, `arch_check`, `discovery_service`, `document_approval`, `spec_author`, `spec_check`, `spec_diff`, `spec_impact`, `spec_planning`, `sprint_planner` and `sprint_team` (each old flat path a shim, as before), importing each other, the tasks (`dependencies`, `task_events`), the models (`company_models`), the evidence (`acceptance`, `browser_smoke`, `standards`), the workspace (`workspace_daemon`, `project_profile`) and the tools (`mcp_tools`, `research_tools`) by the new paths. The move uncovered no edge against the rule: planning already imported only contracts, tasks, models, evidence, workspace and tools, so the allowlist stays at 4 edges. The file-size baseline names `discovery_service` and `sprint_planner` by their new paths, at the same sizes, and the bench's `scorecard.py` and README name `services/planning/spec_check`.

## Step 10: producers

<a id="step-10"></a>

Step 10 is done: `services/producers/` holds `agent_worker`, `console_assistants`, `console_capabilities`, `console_clis`, `console_producer` and `github_adapter` (each old flat path a shim, as before), importing each other, the platform (`glassroom`), the tasks (`board`, `task_projection`), the models (`company_models`) and the workspace (`sandbox`, `git_workflow`) by the new paths. The console producer no longer imports the loop: what the loop owns reaches it through the host it is given (`console_producer.ConsoleHost`, a `Protocol`: the worker's engine, the procedures context, the project folder, the previous attempt and edit plan sections, recording the run and the board note), and the loop's side is `services/loop/console_host.py` (`TaskWorkerHost`, which reads each from `task_worker` when used, and `produce`, the call the worker makes), with the same prompts, records and order. The allowlist is down to 1 edge (`task_worker -> integration_jobs`, step 11's). The file-size baseline names `console_assistants` by its new path, at the same size. Within step 15 (ADR 0015), `backend/agents/graph.py` and `backend/agents/tools/openproject_tools.py` moved to `services/producers/graph.py` and `services/producers/openproject_tools.py`, in the context by their folder, with no shim: `agent_worker`, the tests and their patches import the new paths, and `backend/agents/` is gone, so the image and compose no longer copy it. Under `app/` both are linted and formatted; to keep graph.py within its 720-line baseline, its five node failures go through `_node_failed`, `_invoke_agent`'s three kickoff events through `emit` and its conditional edges through `on_success`, every event, route and log line as before. The file-size baseline names it by its new path, at 720.

## Step 11 (first part): loop

<a id="step-11-first-part"></a>

Step 11 (first part) is done: `services/loop/` holds `answers`, `board_moves`, `carry_over`, `company_service`, `console_host`, `delivery_checks`, `edit_plan`, `replicas`, `review_digest`, `run_review`, `sprint_archive`, `sprint_close`, `sprint_control` and `task_worker` (each old flat path a shim, as before, so `services.task_worker.X` patches and `console_host`'s lazy read of the worker still reach the owner), importing each other and the contexts below by the new paths. The loop no longer calls release: when a sprint closes, `task_worker._finalize_sprint_if_ready` emits `sprint.closed` through `services/platform/hooks.py` (an in-process registry of handlers by event name: `on`, `off`, `emit`, called in order in the emitter's thread and loop, a handler that raises logged and never holding the close back), and `integration_jobs.install()` (called by `main.py`, and by an autouse fixture in `tests/conftest.py`) registers `on_sprint_closed`, which calls `schedule_after_close` as the worker did, at the same moment, with the same arguments, after a reopening's close too. The allowlist is empty. Splitting `task_worker` by stage is step 11's second part. The file-size baseline names `task_worker` by its new path, at its new, smaller size.

## Step 11b, stage 1 (messages)

Step 11b, stage 1 (messages) is done: ADR 0014 splits `task_worker` by stage into `services/loop/worker/` (one pull request per stage: `messages`, then `intake`, `prompt`, `execute`, `deliver`, `record`, `review`, `close`), and `worker/messages.py` owns the agent messages and the ScrumMaster's triage (`MESSAGE_BODY_LIMIT`, `_message`, `_post_message`, `_triage_blocked_task`, `_handoff_body`); `task_worker` calls them as `messages.<name>` and re-exports them, a test patches or calls them on the stage module, and a test that runs the worker on its own engine points the worker and every stage at it with `tests/worker_stages.py` (`use_engine`; each stage adds its module to `STAGES`). `check_architecture.py` gives a module in a package inside a context folder to that context (`loop.worker.messages` is `loop`'s) and keeps one stem naming one module at any depth. The baseline entry for `task_worker` drops with each stage.

## Step 11b, stage 2 (prompt)

Step 11b, stage 2 (prompt) and the shared board writers are done: `worker/board_writes.py` holds the writers every stage records a task's transitions with (`_update_item_status`, `_append_item_comment`, through `task_events.record_by_id`), and `worker/prompt.py` the read-only prompt context (`FILE_MANIFEST_INSTRUCTIONS`, `_fetch_memory_context`, `LESSON_SPRINTS`, `LESSON_FEEDBACK_LIMIT`, `LESSON_FEEDBACK_CHARS`, `_is_lesson`, `_load_recent_lessons`, `_load_sprint_completed_tasks`, `_load_procedures_context`); `_load_task_context` stays for intake, since it may block a task assigned outside the sprint's team, and the retrospective helpers for close (`_load_recent_lessons` reads `retrospective.build` and `retrospective.summary` itself). `task_worker` calls them as `board_writes.<name>` and `prompt.<name>` and re-exports them; `console_host.TaskWorkerHost` reads `_load_procedures_context` and `_update_item_status` from those stage modules, so a patch of the stage reaches a console run, and a test patches or calls them there. Both modules are in `tests/worker_stages.STAGES`, and `test_console_producers` points the worker at its engine with `use_engine`. `task_worker` drops from 3118 to 2970 lines.

## Step 11b, stages 3 and 4 (record, deliver)

Step 11b, stages 3 and 4 (record, deliver) are done together: `worker/record.py` owns the `AgentRun` recording and re-verification (`_record_task_run`, `_reverify_run`, `_summarize_deliverable`, and the readers of `delivery_gate` the review cycle asks, `_delivery_gate`, `_delivery_risk`, `_latest_task_verification`), and `worker/deliver.py` the artifacts to the daemon, queued deliveries and their results (`_resolve_workspace_root`, `_normalize_artifact_content`, `_extract_artifact_targets`, `_build_review_payload`, `_delivery_comment`, the aliases `_detect_test_command`, `_detect_project_test_command` and `_run_delivery_tests`, `_plan_queued_delivery`, `_publish_queued_delivery`, `_queued_delivery_evidence`, `_on_delivery_result`, `_deliver_artifacts`), calling the record stage as `record._record_task_run`, `record._reverify_run` and `record._summarize_deliverable`. The consumer of `workspace.delivered` and `DELIVERY_QUEUED` stay in `task_worker`, which calls `deliver.<name>` and `record.<name>` and re-exports them; the review cycle's local `TaskRecord`, which would have shadowed the module, is now `task_record`. `console_host.TaskWorkerHost` reads `_resolve_workspace_root` and `_record_task_run` from those stage modules (only the engine still from `task_worker`), both modules are in `tests/worker_stages.STAGES`, and a test patches or calls them there (`worker_deliver`, `worker_record`). `task_worker` drops from 2970 to 2519 lines.

## Step 11b, stage 5 (execute)

Step 11b, stage 5 (execute) is done: `worker/execute.py` owns the LLM call, the Architect consultation and the edit plan's use (`LLMCallError`, `_execute_task`, cut into the steps `_developer_system_prompt` and `_developer_prompt` with no behaviour change, `_consult_architect`, `_sanitize_deliverable_text`, `ARCHITECT_CONSULT_ENABLED`, and `_architect_brief`, the consultation step `process_backlog_tasks` ran inline: the decision, the Glassroom events, the question and answer messages and the `ARCHITECT_CONSULTED:` comment); `_call_llm_for_review` and `_validate_minimum_deliverable` stay for the review stage, raising and testing for the same `execute.LLMCallError`. `task_worker` calls `execute.<name>` and re-exports the names; the module is in `tests/worker_stages.STAGES`, and a test patches its client, `_execute_task`, `record_agent_turn`, `architect_track_record`, `should_consult_architect` and the flag on the stage module (`worker_execute`), patching `task_worker.LLMProxyClient` as well only where the QA review runs too. `task_worker` drops from 2519 to 2150 lines.

## Step 11b, stage 6 (close)

Step 11b, stage 6 (close) is done: `worker/close.py` owns the sprint's finalisation, the follow-up sprint and the retrospective (`AUTO_SELF_IMPROVEMENT_ENABLED`, `_build_sprint_retrospective`, `_format_retrospective_summary`, `_coerce_json_dict`, `_build_sprint_kickoff_story`, `_complete_sprint_and_plan_follow_up`, cut into the steps `_plan_follow_up`, `_add_follow_up_tasks`, `_carry_into_follow_up` and `_kickoff_event`, and `_finalize_sprint_if_ready`, cut into `_learn_from_close`, `_burn_retrospective_engram` and `_announce_close`, the side effects in the same order; `sprint.closed` is emitted from it as before), importing `plan_self_improvement_sprint` and `build_self_improvement_objective` itself; `worker/procedures_round.py` the procedures round a close runs (`_evaluate_procedures`, `_ask_scrum_master_for_procedures`, `_propose_procedures`), a module of its own so `close` stays under 600 lines, reading `retrospective.summary` directly so it does not import `close` back. `task_worker` calls `close._finalize_sprint_if_ready` and re-exports the names; `replicas.close_once` still receives `close._complete_sprint_and_plan_follow_up`, looked up when the close runs, so a patch of the stage module reaches it. Both modules are in `tests/worker_stages.STAGES`, and a test patches the flag, the planner, `_complete_sprint_and_plan_follow_up`, `_build_sprint_retrospective` and the engine on `worker_close`, and the procedures round on `procedures_round`; `test_git_base` reads the write timeout from the deliver stage's `settings`, since `task_worker` no longer imports it. `task_worker` drops from 2150 to 1642 lines.

## Step 11b, stage 7 (review)

Step 11b, stage 7 (review) is done: `worker/review.py` owns the review cycle (`_review_cycle`, under `locks.REVIEW_CYCLE` with `replicas.recover_stale` first, `MAX_QA_REVIEW_FAILURES`, and `_review_cycle_body`, cut into the steps `_review_delivered` (each `in_review` task of a running sprint through `_review_task`: `_reviewer_of`, a frozen task kept blocked, `_block_exhausted_reviews`, `_block_missing_deliverable`, the delivery gate's `_send_back_from_delivery_gate`, then QA), `_retry_blocked` (`_recover_to_review`, `_requeue_blocked`) and `_closing_candidates`, in one session with the side effects, early returns and commits in the same order, the set of tasks blocked in the cycle passed to each step), and calls the intake's `_dispatch_todo_items` on `task_worker`, imported when used (it imports `review`); `worker/qa_review.py` owns QA's review of one task (`_qa_review`, cut into `_review_prompt`, `_person_hold` (the high risk tier and the autonomy level's hold), `_rework_budget`, `_record_verdict`, `_announce_verdict` and `_review_failed`), `_call_llm_for_review` (its local `messages`, which would have shadowed the module, is now `request`) and the rework budget the three gates share (`MAX_QA_REWORK_CYCLES`, `AUTOPILOT_QA_REWORK_ENABLED`, `AUTOPILOT_MAX_REWORK_CYCLES`; the delivery gate and the quality gate read `qa_review.AUTOPILOT_MAX_REWORK_CYCLES` when they run, so one patch reaches all three); `worker/quality_gate.py` owns the text checks before a delivery (`MIN_DELIVERABLE_WORDS`, `MIN_DOC_DELIVERABLE_WORDS`, `_validate_minimum_deliverable`, `_send_back_from_quality_gate`). They are three modules so each stays under 600 lines (469, 551 and 162). `_load_item_runtime_state` and `_sync_manual_freeze_state` stay for intake, their only caller. `task_worker` calls `quality_gate.<name>` and `review._review_cycle` and re-exports the moved names; `review` and `qa_review` are in `tests/worker_stages.STAGES` (`quality_gate` opens no session, so it has no engine to point), and a test patches `_call_llm_for_review`, `LLMProxyClient` and `AUTOPILOT_MAX_REWORK_CYCLES` on `qa_review` and `_review_cycle_body` on `worker_review`, and calls `_review_cycle`, `_call_llm_for_review` and `_validate_minimum_deliverable` on their stage modules. `task_worker` drops from 1642 to 791 lines.

## Step 12: release

<a id="step-12"></a>

Step 12 is done: `services/release/` holds `delivery_undo`, `git_publish`, `git_setup`, `integration`, `integration_events`, `integration_jobs`, `integration_publish`, `local_merge` and `review_sync` (each old flat path a shim, as before), importing each other, the platform (`agent_service`, `locks`, `hooks`), the tasks (`board`, `task_events`), the evidence (`review_changes`), the workspace (`git_workflow`, `workspace_daemon`), the planning (`spec_planning`) and the loop (`delivery_checks`, `run_review`) by the new paths. The move uncovered no edge against the rule: release already imported only contracts, platform, tasks, models, evidence, workspace, planning and loop, so the allowlist stays empty. `integration_jobs.install()` still registers the `sprint.closed` handler from `main.py` and `tests/conftest.py` through the shim. `integration` keeps under 600 lines (596): its `models` imports are written on two lines where the context imports took three more. `.gitignore` ignores every `release/` folder (Electron's packages), so it re-includes `backend/app/services/release/`.

## Step 11b, stage 8 (intake)

Step 11b, stage 8 (intake) is done, and with it the split: `worker/intake.py` owns the consumer, the claim, the queue markers and dispatch (`DISPATCH_REQUEUE_COOLDOWN_SECONDS`, `LAST_AUTONOMOUS_QUEUE_AT`, `_queued_recently`, `_mark_queued`, `_clear_queue_marker`, `DELIVERY_QUEUED`, `TASK_ASSIGN_CONSUMER`, `DELIVERY_RESULTS_CONSUMER`, `TASK_ASSIGN_ACK_WAIT_SECONDS`, `_dispatch_todo_items`, and `process_backlog_tasks`, cut into `_consume_assignments`, `_consume_delivery_results` and the review loop; each assignment through `_on_task_assign_held` (the task's lock) and `_on_task_assign`: `_claim` (context, queue marker, freeze, `dependencies.ready`, `replicas.claim_todo`), the run, and `_record_failure` (the credit pause back to `todo`, else blocked)); `worker/task_run.py` owns one claimed task (`_load_task_context`, `_load_item_runtime_state`, `_sync_manual_freeze_state`, and `_run_claimed_task`, cut into `_announce_claim`, `_gather_context`, `_announce_context`, `_produce`, `_still_in_progress`, `_passes_quality_gate` and `_hand_to_qa`), with the side effects, claims, locks, sessions, early returns and acknowledgements in the same order. The context readers went to `task_run` rather than `intake` so the two do not import each other (`intake` imports `task_run`). `review` still reaches `_dispatch_todo_items` through a helper that imports `intake` when called (`_intake`), since `intake` imports `review` for the review loop. `task_worker` is now a facade of 124 lines: `engine` (which `console_host` and `use_engine` read), `TASK_WORKER_PRODUCER`, `parse_file_manifest` and the stages' re-exports (`process_backlog_tasks` among them, which `lifecycle` starts); its file-size baseline entry is gone, since the checker lists only files over 600 lines. Two tests drive the intake where nothing did: `test_dependencies` (an assignment that arrives before its dependency is done is neither queued nor claimed) and `test_work_control` (a run without credit puts the task back to `todo` and pauses the sprint). Both modules are in `tests/worker_stages.STAGES`, and a test calls `_load_task_context` on `task_run` and `process_backlog_tasks`, `_dispatch_todo_items`, `_clear_queue_marker` and `DELIVERY_QUEUED` on `intake`.

## Step 13: integrations

<a id="step-13"></a>

Step 13 is done: `services/integrations/` holds `outbox_service` and `webhooks` (each old flat path a shim, as before), importing each other and the tasks (`board`) by the new paths; `main.py` still calls `webhooks.install()` through the shim, which is the owner module, so the `before_flush` listener is installed once, and `lifecycle` starts the outbox processor through `services.outbox_service` as before. The move uncovered no edge against the rule: the integrations already imported only the tasks, so the allowlist stays empty.

## Step 14: management

<a id="step-14"></a>

Step 14 is done: `services/management/` holds `brief_mail`, `manager_actions`, `manager_actions_extra`, `manager_agent`, `manager_brief`, `manager_inbox`, `manager_inbox_extra`, `manager_observe`, `manager_policy`, `manager_record`, `manager_record_extra`, `manager_rules`, `reviewer_agent` and `support_agent` (each old flat path a shim, as before, so `services.manager_agent.X` patches, `lifecycle`'s workers and its `manager_observe.bind` still reach the owner), importing each other and the migrated contexts (platform, tasks, models, evidence, workspace, learning, planning, loop) by the new paths and the release modules (`integration`, `delivery_undo`, `git_publish`, `local_merge`) by their flat shims while release moves. The move uncovered no edge against the rule: management is the top layer and no other context imported it, so the allowlist stays empty. Comments that pointed at the old paths name the new ones; none of the modules is in the file-size baseline.

## Step 15: the last shims

Step 15 is done: no flat `services/<module>.py` shim is left. Every importer, test patch string, comment and document names `services.<context>.<module>`, every context's `modules` list in `contexts.json` is empty, and the checker reports no shim and no allowlisted edge. `.github/CODEOWNERS` names `evidence/verification.py` and `tasks/board.py`.

## After the migration: the tests

The test files now have a context too: `tests` in `contexts.json` maps each `backend/tests/test_*.py` to the contexts it exercises (derived from its imports and routes, then read by hand), `check_architecture.py` fails a test without one, and `scripts/affected_tests.py` runs only the tests of a change's context and of the contexts that may import it (`docs/TESTING.md`, Only the tests a change can break).

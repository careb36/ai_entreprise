# ADR 0012: Contexts depend on each other in one direction, and the evidence on contracts and platform only

## Status

Accepted (2026-11-09). Recorded as ADR-1 of the architecture in `docs/architecture/architecture.json` (issue #469); `docs/ARCHITECTURE.md` is rendered from it.

## Context

backend/app/services holds 118 flat modules with 335 import edges and a 26-module cycle in which the Verifier (verification, acceptance, standards), the loop (task_worker) and the daemon client (workspace_daemon) import each other. The positioning makes the evidence the product: what judges a run must not depend on what produced it.

## Decision

Fourteen contexts form a directed acyclic graph recorded in docs/architecture/contexts.json: contracts; platform; tasks, models, evidence; workspace; tools; learning; planning, producers; loop; release, integrations; management. A module may import its own context and the contexts its context names in depends_on, nothing else. evidence names contracts and platform only. Cycles inside one context are tolerated and listed (app_check, browser_smoke, delivery_gate, verification; arch_author and spec_planning; sprint_planner and sprint_team; manager_inbox and manager_inbox_extra); a cycle across contexts is not.

## Consequences

Twenty edges break the rule today and are allowlisted: evidence to workspace (5), evidence to loop (3), producers to loop (3), evidence to tools (3), workspace to evidence (2), workspace to tasks, loop to release, evidence to producers and tools to loop (1 each). Removing them breaks the 26-module cycle into the four intra-context ones. A new feature has to say which context it lives in before it is written.

## Appendix: the contexts

Generated from `docs/architecture/contexts.json` (the checker's data) at adoption.

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

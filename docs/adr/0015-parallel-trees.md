# ADR 0015: The parallel trees: merge what production imports, delete what only tests import

## Status

Accepted (2026-11-09). Recorded as ADR-4 of the architecture in `docs/architecture/architecture.json` (issue #469); `docs/ARCHITECTURE.md` is rendered from it.

## Context

Beside backend/app: backend/agents (7 files, 1,426 lines), backend/orchestration (1, 115), backend/llm (4, 939), backend/memory (1, 543), backend/eval (4, 383) and src/ (6, 368). Checked with grep on imports across backend, scripts and the workflows.

## Decision

orchestration: delete (router.py is never included by main.py; only tests/test_circuit_breaker.py imports it). llm: merge model_factory.py into models (imported by company_models, manager_agent, model_routing, reviewer_agent, support_agent, roles, agents/graph, memory); delete circuit_breaker, fallback and retry (only tests and the orchestration router). agents: merge graph.py and tools/openproject_tools.py into producers (agent_worker imports graph); delete po_agent, po/prompts, python_dev_agent, ast_pre_validator, qa_context_optimizer (only tests). memory: merge engram_processor.py into platform (task_worker and agents/graph import it). eval: move verificador to bench/verifier/ (a measurement, only its test imports it). src/: delete (nothing imports it).

## Consequences

About 1,400 lines and their tests leave the repository; the tests of deleted modules are deleted with them, the tests of merged ones follow their imports. agents/graph.py (720 lines) keeps its size baseline at its new path and may only shrink. The backend Dockerfile stops copying llm/, agents/ and memory/. ADR 0010 (the self-correction loop of src/ and agents/ast_pre_validator) and ADR 0011 (llm/fallback's FallbackRouter) describe code no production path runs; they are marked superseded by this decision in the step that deletes it, and the fallback that does run stays the LLM proxy's tiers.

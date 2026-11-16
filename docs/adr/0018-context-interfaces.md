# ADR 0018: Each context has a public interface, and other contexts import only it

## Status

Accepted (2026-11-16). ADR-7 of architecture v1, *La Fabrique backend: contexts and their dependency rule*, for the specification *Backend contexts and their dependency rule*. Written by La Fabrique from the architecture record; edit it there, not here.

## Context

The dependency rule (ADR 0012) says which contexts may import which, not which of their modules: any module of a context it may import was fair game, so 416 imports across contexts reached 102 of 272 modules, among them a private helper (release/integration_publish imported evidence/review_changes._workspace_file), a lookup kept in the wrong context (management imported business_unit_id from planning/spec_author) and a step taken past its entry point (tools/edit_lint resolved the stack profile itself beside workspace/project_profile). Nothing told a sub-step from an interface, so the next import of an internal would pass too.

## Decision

Each context in docs/architecture/contexts.json declares its public modules (public, relative to the context; ["*"] for contracts, which exists to be shared; the context's own __init__ is always public). A module may import, from another context its context may import, only that context's public modules. A declared list was chosen over services/<context>/__init__.py as the interface because it moves no code: the fourteen package __init__ files are docstrings, importing through them would rewrite all 416 imports, make every importer load the whole context at import time (the database models, the NATS client) and break the tests that patch the defining module, while the modules other contexts already use deliberately are the interface as it stands. scripts/check_architecture.py fails on an import of a non-public module of another context that is not in interface_allowlist (named by stems, like allowlist), reports an entry that no longer violates, and --update drops it and never adds one. Code outside backend/app/services (routers, models, the daemon) is out of scope.

## Consequences

Declared public: the modules other contexts used by design, 94 across twelve contexts, 15 of them contracts' (integrations and management declare none: no other context imports them). Against those lists 9 imports reached internals; 3 were fixed in this change (business_unit_id moved to models/company_models for its two Manager callers, and edit_lint calls project_profile.resolved; besides, the private review_changes._workspace_file is now the public workspace_file) and 6 remain allowlisted: loop/delivery_checks to workspace/dependency_install and workspace/run_logs, loop/delivery_suites to workspace/folder_commands and workspace/stack_roots (the loop composes the delivery's daemon commands itself; the fix is one workspace entry point for them, which moves that composition and the tests that patch it), and loop/task_routing to models/difficulty and models/routing_policy (model_routing.route is the interface and takes the estimate and the policy as arguments; the fix moves the estimate from the task's plan and board into models). Making a module public is a reviewed change to contexts.json in the pull request that needs it.

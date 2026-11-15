# ADR 0012: Contexts depend on each other in one direction, and the evidence on contracts and platform only

## Status

Accepted (2026-11-09). ADR-1 of architecture v1, *La Fabrique backend: contexts and their dependency rule*, for the specification *Backend contexts and their dependency rule*. Written by La Fabrique from the architecture record; edit it there, not here.

## Context

backend/app/services holds 118 flat modules with 335 import edges and a 26-module cycle in which the Verifier (verification, acceptance, standards), the loop (task_worker) and the daemon client (workspace_daemon) import each other. The positioning makes the evidence the product: what judges a run must not depend on what produced it.

## Decision

Fourteen contexts form a directed acyclic graph recorded in docs/architecture/contexts.json: contracts; platform; tasks, models, evidence; workspace; tools; learning; planning, producers; loop; release, integrations; management. A module may import its own context and the contexts its context names in depends_on, nothing else. evidence names contracts and platform only. Cycles inside one context are tolerated and listed (app_check, browser_smoke, delivery_gate, verification; arch_author and spec_planning; sprint_planner and sprint_team; manager_inbox and manager_inbox_extra); a cycle across contexts is not.

## Consequences

Twenty edges break the rule today and are allowlisted: evidence to workspace (5), evidence to loop (3), producers to loop (3), evidence to tools (3), workspace to evidence (2), workspace to tasks, loop to release, evidence to producers and tools to loop (1 each). Removing them breaks the 26-module cycle into the four intra-context ones. A new feature has to say which context it lives in before it is written.

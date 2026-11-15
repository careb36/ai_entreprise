# ADR 0016: Migrate one context per pull request, bottom up, behind re-export shims and a ratchet

## Status

Accepted (2026-11-09). ADR-5 of architecture v1, *La Fabrique backend: contexts and their dependency rule*, for the specification *Backend contexts and their dependency rule*. Written by La Fabrique from the architecture record; edit it there, not here.

## Context

The backend must keep working while 118 modules move; the e2e and the unit tests patch modules by their current path.

## Decision

Order: (1) contracts, removing the edges to task_worker; (2) platform with backend/memory; (3) tasks; (4) models with llm/model_factory; (5) evidence, removing its edges to workspace, tools, loop and producers; (6) workspace; (7) tools; (8) learning; (9) planning; (10) producers with agents/graph; (11) loop, splitting task_worker by stage and replacing its call to integration_jobs by the sprint.closed event; (12) release; (13) integrations; (14) management; (15) delete the dead trees and the shims nothing imports. Each pull request moves the files of one context with git mv, leaves services/<module>.py as a shim (from services.<context>.<module> import *, plus the private names tests patch), changes no behaviour, and runs scripts/check_architecture.py --update so the allowlist shrinks.

## Consequences

Every step is green on the backend tests, ruff 0.17.1, check_file_sizes.py and check_architecture.py, and can be reverted alone. The shims cost a few hundred one-line files for a while; the last step removes them. Nothing new may be added to the allowlist by any step.

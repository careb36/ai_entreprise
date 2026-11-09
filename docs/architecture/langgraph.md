# LangGraph Orchestration Architecture

## Overview
La Fabrique uses LangGraph to orchestrate AI agents and validation gates. The graph represents a state machine where nodes are agents or tools, and edges represent the routing logic based on the artifact's state.

## Nodes
- **Dev Agent (`dev_agent`)**: Generates code artifacts based on task requirements. Parses validation errors to self-correct if a previous attempt failed.
- **Pre-Flight Check (`preflight_check`)**: Validates the artifact using AST parsing and linters. Populates `validation_errors` in the state if issues are found.

## Edges and Routing
- **Generation to Validation**: Unconditional edge from `dev_agent` to `preflight_check`.
- **Validation to Self-Correction Loop**: Conditional edge from `preflight_check`.
  - If `validation_errors` is empty -> `END` (Success).
  - If `validation_errors` has items AND `correction_attempts < max_correction_attempts` -> Route back to `dev_agent`.
  - If `validation_errors` has items AND `correction_attempts >= max_correction_attempts` -> `END` (Failure/Human Review).

## State Schema
The workflow state tracks:
- `artifact`: The current code payload.
- `validation_errors`: A list of strings detailing pre-flight check failures.
- `correction_attempts`: Integer tracking how many times the Dev agent has tried to fix the artifact.
- `max_correction_attempts`: Configuration for the maximum allowed loop iterations (default is 3).

# ADR 0010: Autonomous Dev Self-Correction Loop

## Status
Accepted

## Context
In Sprint 5, we introduced a Pre-Flight Check Node to validate AI-generated artifacts using AST parsing and linters before they reach QA. Currently, if an artifact fails validation, the pipeline halts or requires human intervention. To fully realize the autonomous capabilities of La Fabrique, the Dev agent should be able to receive these validation errors and correct its own work without human or QA intervention.

## Decision
We will implement an autonomous self-correction loop within the LangGraph orchestration:
1. **State Management**: The graph state will track `validation_errors` and `correction_attempts`.
2. **Conditional Routing**: A conditional edge will route the workflow from the `preflight_check` node back to the `dev_agent` node if `validation_errors` are present.
3. **Loop Termination**: To prevent infinite loops, the maximum number of correction attempts is capped by an environment variable `MAX_CORRECTION_ATTEMPTS` (default: 3). If this limit is reached, the workflow terminates and routes to `END`.
4. **Agent Parsing**: The Dev agent node will check for `validation_errors` in the state. If present, it will parse the error report and generate a corrected artifact.

## Consequences
### Positive
- **Increased Autonomy**: Reduces human intervention by allowing the system to fix syntax and linting errors automatically.
- **Faster Feedback Loop**: Immediate correction of pre-flight errors speeds up the overall delivery pipeline.
- **Resilience**: The system gracefully handles imperfect LLM outputs.

### Negative
- **Increased Costs**: Failed attempts and subsequent corrections consume additional LLM tokens and compute resources.
- **Latency**: Artifacts that require multiple correction attempts will take longer to reach the final state.

## Compliance
- **STD-ARCH-2**: Node responsibilities remain strictly isolated. The `dev_agent` generates/corrects, the `preflight_check` validates, and the graph definition handles routing.
- **STD-OPS-1**: The loop limit is configurable via the environment, and routing decisions emit structured logs.

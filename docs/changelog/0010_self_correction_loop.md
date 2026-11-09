# Changelog Update: Autonomous Dev Self-Correction Loop

## Added
- **Self-Correction Loop**: The LangGraph orchestration now feeds pre-flight validation errors back to the Dev agent, allowing it to correct its own code without human intervention.
- **Dev Agent Node**: Updated to parse `validation_errors` from the graph state and append fixes to the artifact automatically.
- **Configurable Limits**: Added `MAX_CORRECTION_ATTEMPTS` environment variable to prevent infinite loops (defaults to 3).
- **Structured Logging**: Added JSON-formatted logging to routing and agent nodes for better observability.

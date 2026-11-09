# Specification: PO Task Generation Prompt

## Requirements
The Product Owner agent's task generation prompt must enforce the breakdown of complex frontend features into smaller, bounded tasks. This prevents giant context windows that cause 110-second LLM execution timeouts.
- A complex UI feature MUST be broken into at least 3 distinct sub-tasks during planning.
- Tasks must follow a clear separation of concerns (e.g., state, presentation, integration).

## Quality Attributes (STD-PO-1)
- **Performance:** 
  - Context size limit: <= 8000 tokens per generated task context.
  - Planning latency: <= 15 seconds per feature breakdown generation.
- **Reliability:** 
  - Timeout prevention: 0 LLM timeouts (>110s) during execution of the generated tasks.
  - Task granularity: 100% of complex UI features must yield >= 3 sub-tasks.
- **Security:** 
  - Prompt injection prevention: 100% of user inputs to the planner must be sanitized.
  - Secret management: 0 secrets hardcoded in the prompt (STD-SEC-1).
- **Accessibility:** 
  - Generated UI tasks must include a requirement for WCAG 2.1 AA compliance (100% compliance target for UI tasks).

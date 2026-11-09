# Specification: PO Agent Task Planning

## Requirements
- The PO agent's task generation prompt must enforce smaller, bounded frontend tasks.
- A complex UI feature must be broken into at least 3 distinct sub-tasks during planning.
- The prompt must explicitly instruct the agent to prevent giant context windows that cause 110s timeouts.

## Quality Attributes
- **Security**: 0 secrets allowed in the prompt or the generated planning outputs (STD-SEC-1).
- **Performance**: The prompt processing overhead must add < 50ms to the LLM call setup.
- **Reliability**: 100% of generated task breakdowns for UI features must contain >= 3 sub-tasks, preventing 110s timeouts.
- **Accessibility**: 100% of generated tasks must include clear, measurable acceptance criteria for developers.

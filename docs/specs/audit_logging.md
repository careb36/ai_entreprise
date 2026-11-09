# Specification: Agent Exception Audit Logging

## Requirements
1. Track LLM timeout durations, retry counts, and self-correction loop iterations in the verification audit logs.
2. Store the audit records securely in PostgreSQL to provide robust data for future retro analysis.
3. Validate the tracking by simulating a timeout and inspecting the inserted database records.

## Quality Attributes

- **Security (SEC-1):** Audit logs must not expose any secrets. Secret scanning must return 0 violations. Database credentials must exclusively be loaded from the environment (STD-SEC-1).
- **Performance (PERF-1):** Writing a complete audit record to PostgreSQL must execute in under 50 milliseconds (P95) to avoid blocking agent execution loops.
- **Reliability (REL-1):** The audit logging service must handle 500 concurrent agent records per minute with a 99.99% success rate.
- **Accessibility (ACC-1):** Queries to the audit log table for dashboard visualization must return within 200 milliseconds (P95) for up to 1 million rows to ensure a responsive frontend.

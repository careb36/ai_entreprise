# Specification: Agent Exception Audit Logging

## Overview
To improve future retrospective analysis and provide actionable feedback on autonomous agent reliability, the AI verification audit logs must specifically track execution exceptions. This includes LLM timeout durations, retry counts, and self-correction loop iterations.

## Requirements
1. **Schema Enhancements**: The `AgentAuditLog` model must include:
   - `timeout_duration_seconds` (Float, nullable)
   - `retry_count` (Integer, default 0)
   - `self_correction_iterations` (Integer, default 0)
2. **Verification & Testing**: After a simulated timeout, these fields must be verifiable via PostgreSQL audit tables. Test suites must **not** skip execution if the primary database URL is missing; they must fallback to an in-memory SQLite database to guarantee CI coverage.

## Standards Compliance
- **STD-SEC-1**: No secrets in the repository. Database credentials must be provided via environment variables (e.g., `TEST_DB_URL`).
- **STD-AUD-1**: The schema strictly separates `agent_claims` (e.g., files written, statuses claimed by the agent) and `system_observations` (e.g., actual test results, execution times observed by the daemon).

## Quality Attributes (STD-PO-1)
- **Performance**: Database insertion of audit logs must complete in under `50ms` (P95) to prevent execution latency bottlenecks.
- **Reliability**: The logging mechanism must achieve a `99.9%` capture rate for exception metrics during simulated timeouts, with zero unhandled exceptions during the logging process itself.
- **Security**: Zero SQL injection vulnerabilities allowed. All queries must use parameterized SQLAlchemy ORM methods.

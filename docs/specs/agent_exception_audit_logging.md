# Product Specification: Agent Exception Audit Logging

## 1. Overview
As the Product Owner, I require our AI verification audit logs to track execution exceptions explicitly. To ensure dependable, audit-proof records of autonomous agent behavior, we must capture LLM timeout durations, retry counts, and self-correction loop iterations. This data is critical for retro analysis and optimizing trade-offs between verification depth and execution latency.

## 2. Requirements
- **REQ-1:** The audit log schema must capture `llm_timeout_duration_ms` as an integer representing the observed delay before a timeout.
- **REQ-2:** The audit log schema must capture `retry_count` as an integer representing the observed number of API retries.
- **REQ-3:** The audit log schema must capture `self_correction_loop_iterations` as an integer representing the observed number of self-correction cycles the agent went through.
- **REQ-4:** Claims made by the agent and observations made by the system MUST NOT share a field (per STD-AUD-1).

## 3. Quality Attributes (STD-PO-1)
- **Security-1:** 0 secrets (API keys, tokens) shall be written to the audit logs. Passwords and keys must be redacted before serialization.
- **Performance-1:** Audit log serialization and database insertion preparation must complete in < 15ms.
- **Reliability-1:** 100% of simulated timeouts must result in an audit log entry with `retry_count` > 0 and `llm_timeout_duration_ms` > 0.
- **Accessibility-1:** 100% of audit logs must be serialized in strictly typed JSON format to ensure compatibility with standard accessibility and reading tools used by compliance auditors.

## 4. Acceptance Criteria
- **AC-1:** The `AuditLog` model contains separate `AgentClaims` and `SystemObservations` structures.
- **AC-2:** `SystemObservations` includes the new fields: `llm_timeout_duration_ms`, `retry_count`, and `self_correction_loop_iterations`.
- **AC-3:** Automated tests verify the instantiation of these models with simulated timeout data, satisfying STD-QA-1.
- **AC-4:** Automated tests verify that PostgreSQL insertion payloads contain the correct timeout and retry metrics.

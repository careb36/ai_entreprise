# Specification: Audit Logging for Agent Exceptions

## 1. Overview
This specification defines enhancements to the AI verification audit logs to specifically track LLM timeout durations, retry counts, and self-correction loop iterations. This provides the necessary evidentiary integrity for future retrospective analysis of LangGraph agent resilience and autonomous execution performance.

## 2. Acceptance Criteria
- **Schema Enhancements**: The `ai_audit_logs` table must include fields for `llm_timeout_duration_ms` (float), `retry_count` (integer), and `self_correction_iterations` (integer).
- **Separation of Concerns (STD-AUD-1)**: Claims made by the agent (e.g., intended code generation) must be strictly separated from system observations (e.g., actual timeout durations, retry counts, and iterations observed by the daemon). They must never share a field.
- **Test Coverage**: Automated tests must be implemented for the audit schema to prevent QA regressions (addressing Sprint 2 retrospective findings).
- **Verification**: A simulated timeout must successfully write to the PostgreSQL audit tables, and the resulting row must accurately reflect the `retry_count` and `llm_timeout_duration_ms`.

## 3. Quality Attributes (STD-PO-1)
- **Performance**: Audit log insertion must complete in `< 50ms` (95th percentile) to avoid blocking the main LangGraph orchestration layer.
- **Reliability**: Audit logging must achieve a `99.99%` success rate. In the event of a database connection failure, logs must failover to a local disk queue rather than dropping records.
- **Security**: Audit logs must be append-only. Database roles must reject `UPDATE` or `DELETE` commands on the `ai_audit_logs` table. No sensitive tokens are logged (verified by PII scanners).
- **Accessibility**: The audit logs must be exposed via a paginated REST endpoint, queryable by `retry_count > 0`, with API response times `< 200ms` to ensure smooth rendering in the React exploration interface.

## 4. Product Decisions & Trade-offs
- **Latency vs. Verification Depth**: We prioritize capturing the exact timeout duration and retry count over capturing the full multi-megabyte stack trace of LangChain exceptions. This keeps the audit payload lightweight and guarantees insertion latency remains under the 50ms target.
- **Evidentiary Integrity**: The `self_correction_iterations` field is populated strictly by the backend orchestration layer (system observation), not by the agent's self-reported loop count.

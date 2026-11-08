# Specification: Autonomous Artifact Validation and Retry Loop

## 1. Overview
As part of our commitment to evidentiary standards and verifiability at La Fabrique, this specification defines the LangGraph verification gates for code artifacts. We must ensure that malformed Python or TypeScript code is rejected before deployment, providing a crisp feedback loop to the autonomous agents.

## 2. Requirements
- **State Schema:** The LangGraph state must explicitly separate agent `claims` from system `observations` (STD-AUD-1).
- **Validation Gates:** 
  - Python artifacts must pass `ast.parse` syntax checks.
  - TypeScript/JavaScript artifacts must pass a syntax validation check (e.g., via `node --check`).
- **Rejection Logic:** Any artifact failing syntax validation must populate the `validation_errors` state array and set `observations["syntax_valid"] = False`, triggering the LangGraph retry loop.

## 3. Quality Attributes (STD-PO-1)
- **PERF-1 (Performance):** Artifact validation must complete in under `200ms` per Python file and under `500ms` per TypeScript file at the 95th percentile.
- **REL-1 (Reliability):** The validation gate must have a false-positive rejection rate of `< 0.1%` over 10,000 mock executions.
- **SEC-1 (Security):** The validation environment must execute in a sandboxed process with memory limits strictly capped at `128MB` per validation task. No secrets or API keys shall be tracked in the repository; all environment-specific configurations must be injected via secure secret stores (STD-SEC-1).
- **AUD-1 (Auditability):** `100%` of validation outcomes must be recorded in the `observations` dictionary of the artifact, strictly isolated from the agent's `claims`.

## 4. Acceptance Criteria
- [x] LangGraph state schema defines `claims` and `observations` as separate fields.
- [x] `artifact_verification_node` correctly parses Python AST and rejects malformed code.
- [x] `artifact_verification_node` correctly shells out or validates TS/JS syntax.
- [x] Unit tests cover valid Python, invalid Python, valid TS, and invalid TS mock artifacts.

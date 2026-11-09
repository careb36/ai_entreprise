# ADR 004: Automated TypeScript Pre-validation & Single-Pass Self-Correction in Frontend Dev Agent

## Status
Accepted

## Context
During autonomous UI generation, the Frontend Dev agent generates React components and TypeScript modules that may contain syntactic or subtle type mismatch errors (e.g. incorrect prop types, broken interface implementations, or unresolved symbols). In previous sprints, such artifacts passed directly to QA or verification gates, creating unnecessary rework cycles and slowing down sprint velocity.

To harden the pipeline, the Frontend Dev agent requires an automated pre-validation gate running `tsc --noEmit` before handing off artifacts to QA. If compilation fails, structured compiler error diagnostics must be fed back to the agent for exactly one self-correction pass.

## Decisions

1. **Pre-Validation Node in LangGraph**:
   A dedicated `ts_prevalidation` LangGraph node executes `tsc --noEmit` against candidate frontend artifacts in an isolated execution sandbox.

2. **Single-Pass Self-Correction Loop**:
   - `max_retries` is capped at `1` to prevent infinite LLM loops and contain API token costs.
   - On the first compilation failure, structured error diagnostics (file, line, error code like `TS2322`, and compiler error description) are formatted and fed into `frontend_self_correction_node`.
   - The agent updates code artifacts and transitions back to `ts_prevalidation`.
   - If validation passes, artifacts route to `qa_handoff` with status `ready_for_qa`.
   - If validation fails again after the retry, artifacts route to `qa_handoff` marked as `validation_failed`, providing hard observation evidence to the QA Agent.

3. **Strict Adherence to STD-AUD-1**:
   Agent claims (what the agent claims it created or fixed) and compiler observations (actual stdout, stderr, exit code, and parsed `TSError` objects) reside in strictly separated fields:
   - `FrontendDevState.claims`: `AgentClaims`
   - `FrontendDevState.observations`: `CompilerObservations`

4. **Environment Isolation & Fast Fallback**:
   The compiler uses Vite/TypeScript configs from the repository (`frontend/tsconfig.json`) or an ephemeral sandbox, falling back to a structured syntax & type verifier if node/tsc is absent in isolated CI environments.

## Consequences

### Positive
- Prevents broken TypeScript artifacts from reaching QA.
- Significantly reduces QA rework cycles.
- Provides immediate diagnostic feedback loop for the LLM to fix hallucinated or mismatched types.
- Complete auditability and separation between agent claims and compilation observations.

### Trade-offs
- Adds a short compilation latency step (~10-45ms in memory/mock, ~1-3s with full `tsc --noEmit`).
- Self-correction pass consumes an additional model prompt if the initial generation contains type errors.

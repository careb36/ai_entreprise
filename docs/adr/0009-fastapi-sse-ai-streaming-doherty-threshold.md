# ADR 0009: FastAPI Server-Sent Events (SSE) AI Streaming for Doherty Threshold Compliance

## Status
Accepted

## Context
Under the Doherty Threshold principle (Laws of UX), productivity soars when a computer and its users interact at a pace that ensures neither has to wait on the other. Specifically, system feedback must occur in under 400ms (and under 200ms for immediate visual acknowledgement) to keep the user engaged in an uninterrupted cognitive flow.

Standard synchronous AI query and LangGraph execution pipelines introduce latency (1–5 seconds) while multi-stage nodes (retrieval, AST validation, synthesis, and gate checking) execute sequentially. Without streaming feedback, user interfaces freeze or display generic spinners, violating the Doherty Threshold and degrading UX.

## Decision
1. **Server-Sent Events (SSE) Protocol**:
   - Implement HTTP streaming responses (`text/event-stream`) via FastAPI's `StreamingResponse`.
   - Provide dual endpoint support: POST `/api/ai/stream/query` for rich JSON payloads and GET `/api/ai/stream/workflow` for native browser `EventSource` consumption.
2. **Immediate Handshake Event (<200ms TTFB)**:
   - The streaming engine emits an initial `connected` SSE frame with session metadata immediately upon connection (<20ms local processing), satisfying the sub-200ms acceptance criteria.
3. **Structured Frame Schema & STD-AUD-1 Separation**:
   - Every frame adheres to STD-AUD-1: `claim` (agent/system intent) and `observation` (system metrics, timestamps, latency observations) never share a field.
4. **Canonical Service Placement**:
   - The core engine lives in `backend/app/services/ai_streaming.py` with routers in `backend/app/routers/ai_stream.py`. Compatibility re-exports are maintained in `src/backend/streaming/ai_streaming_engine.py` to preserve backwards compatibility across legacy modules.
5. **STD-SEC-1 Enforcement**:
   - No bearer tokens, keys, or raw secrets are reflected in stream payloads or logged.

## Consequences
- **Positive**:
  - TTFB drops from >1500ms to <25ms.
  - Frontend receives immediate state changes and step progress.
  - Full adherence to audit integrity standards (STD-AUD-1).
  - Clean separation of service logic under `backend/app/services/` resolving QA directory structure feedback.
- **Negative / Mitigations**:
  - Requires maintaining open HTTP connections; mitigated through standard `keep-alive` headers and proxy unbuffering (`X-Accel-Buffering: no`).

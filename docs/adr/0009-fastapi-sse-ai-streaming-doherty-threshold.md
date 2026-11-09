# ADR 0009: FastAPI SSE AI Streaming for Doherty Threshold Compliance

## Status
Accepted

## Context
Interactive AI query execution and LangGraph validation workflows can take several seconds to execute full AST verification, graph traversals, and LLM completions. Under the **Doherty Threshold** (a core Law of UX), system response time must provide feedback within 400ms (and ideally <200ms TTFB) to keep the user engaged and maintain optimal productivity.

A traditional blocking REST endpoint (`POST /api/ai/query`) causes the frontend UI to display an unreactive spinner for multiple seconds, violating the Doherty Threshold and degrading developer experience.

## Decision
1. **Server-Sent Events (SSE)**: Implement FastAPI streaming endpoints using `StreamingResponse(generator, media_type="text/event-stream")`.
2. **Immediate Preamble Protocol (<200ms TTFB)**: The streaming generator emits an initial `connected` handshake event immediately prior to initiating any LLM inference, LangGraph dispatch, or database operations.
3. **Structured Event Protocol**:
   - `event: connected`: Immediate connection handshake (<200ms TTFB guarantee).
   - `event: node_transition`: Live LangGraph validation step events.
   - `event: token`: Chunked textual tokens for immediate visual rendering.
   - `event: completed`: Final payload and latency telemetry.
4. **STD-AUD-1 Compliance**: Stream payloads partition `claim` (expected action/parameters) and `observation` (measured TTFB, timestamps) into non-overlapping fields.
5. **STD-SEC-1 Compliance**: Zero secret or authentication tokens exposed over SSE channels.

## Consequences
- **Positive**: Frontends receive the initial byte within 10–25ms, well under the 200ms acceptance criteria.
- **Positive**: Native browser support via `EventSource` and standard fetch streaming readers without external WebSocket broker dependencies.
- **Trade-off**: Requires `X-Accel-Buffering: no` and explicit reverse-proxy buffer disabling in Nginx/traefik/K8s ingress.

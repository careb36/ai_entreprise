# Architecture: Sub-200ms Streaming Responses for AI Endpoints (Doherty Threshold)

## Executive Summary
The Doherty Threshold states that computer-human productivity increases exponentially when computer response time is kept under 400ms. In autonomous generative AI workflows and LangGraph pipelines, model invocation and tool validation often take several seconds. 

By implementing Server-Sent Events (SSE) with an immediate preamble handshake emitting within <200ms, the system guarantees that visual feedback begins instantaneously for the user while downstream LangGraph execution, AST verification, and vector lookups proceed asynchronously.

## Protocol and Architecture

### 1. Endpoint Contract
- `POST /v1/ai/query/stream`: Primary streaming endpoint for rich AI query payloads.
- `GET /v1/ai/query/stream`: Native browser `EventSource` endpoint for lightweight client views.
- `POST /v1/generate/stream`: LangGraph agent code and artifact generation stream.

### 2. Time-To-First-Byte (TTFB) Guarantee
1. **Immediate Preamble**: The generator yields `event: connected` before initializing the LangGraph state machine or LLM client calls.
2. **Reverse Proxy Buffering Disabled**: Responses include `X-Accel-Buffering: no` and `Cache-Control: no-cache, no-transform` to bypass upstream Nginx/Envoy buffer queues.
3. **Chunked State Streaming**: LangGraph transitions are streamed as `node_start`, `token`, `node_complete`, and `evidence_captured`.

### 3. Standards Compliance
- **STD-AUD-1 (Audit Trails)**: Every stream event separates `claim` (what the agent or client requested) from `observation` (system-measured latency, verification results, and SHA hashes).
- **STD-SEC-1 (Secrets)**: No secrets or credentials are passed through stream payloads or logged.

### 4. Event Schema

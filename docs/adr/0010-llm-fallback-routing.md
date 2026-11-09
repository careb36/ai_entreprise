# ADR 0010: LLM Fallback Routing for Resilient Execution

## Status
Accepted

## Context
Our orchestration layer relies on primary LLMs (e.g., GPT-4o or Claude 3.5 Sonnet) to execute complex autonomous tasks. Occasionally, these providers experience hard outages, rate limiting, or severe latency degradation that exhaust our exponential backoff retry mechanisms. The recent Sprint 3 retrospective highlighted blocked tasks due to `gemini 502 timed out after 110s`. Relying on a single model endpoint introduces a single point of failure.

To ensure system resilience and uninterrupted service, we need a mechanism to route requests to a secondary (fallback) model when the primary model fails repeatedly or times out.

## Decision
We will implement a custom Fallback Routing mechanism (`FallbackRouter`) within our orchestration layer:
1. The router wraps a primary model and a list of fallback models.
2. If the primary model raises an exception (e.g., `TimeoutError` or `502 Bad Gateway`), the router catches it, logs the failure in a structured format (per STD-OPS-1), and sequentially attempts the fallback models.
3. We avoid using LangChain's opaque `with_fallbacks` internally to ensure we can emit explicit, structured observability events (`llm_primary_failed`, `llm_fallback_start`) directly into our telemetry pipeline.

## Consequences
- **Positive:** Increased reliability and task completion rates during primary provider outages.
- **Positive:** Graceful degradation of service with explicit structured logging for operations monitoring.
- **Negative:** Increased complexity in model configuration, requiring prompts to be generally compatible across both primary and fallback models.

# ADR 0008: Sub-400ms Graph Caching Strategy

## Status
Accepted

## Context
La Fabrique's frontend relies on complex graph queries (e.g., dependency lineage, AI artifact execution paths) stored in Neo4j. As graph complexity and the volume of LangGraph orchestration events grow, deep traversal query execution times risk exceeding the Doherty Threshold (400ms). System response times above this threshold degrade user engagement and workflow fluidity. To maintain optimal UX and system responsiveness, we must implement a resilient caching layer for read-heavy graph operations.

## Decision
We will implement a hybrid caching architecture utilizing Redis and PostgreSQL to offload read pressure from Neo4j:

1. **Redis (Primary Ephemeral Cache):**
   - Used for caching JSON-serialized Neo4j query responses.
   - Target response time: <50ms.
   - Connection configured strictly via environment variables (`REDIS_URL`), adhering to STD-OPS-1.

2. **PostgreSQL (Secondary Materialized Cache):**
   - Used to store pre-aggregated summaries of graph executions.
   - Strictly separates claims (what an agent says it did) from observations (what the daemon saw), adhering to STD-AUD-1.

3. **Cache Invalidation Strategy:**
   - **Tag-Based Eviction:** Queries are cached with entity tags (e.g., `node:123`). Mutations to an entity trigger targeted deletion of associated cache keys.
   - **Fallback TTL:** All keys have a hard Time-To-Live (e.g., 60s for active executions, 15m for historical lineage) to ensure eventual consistency and prevent memory bloat.

## Consequences
- **Positive:** API response times for graph traversals will consistently fall below the 400ms Doherty Threshold, optimizing frontend UX.
- **Positive:** Reduced CPU and memory load on the Neo4j cluster during peak usage.
- **Negative:** Increased backend complexity. Developers must explicitly tag cache entries and trigger invalidation events during LangGraph node mutations.

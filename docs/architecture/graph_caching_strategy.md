# Neo4j Graph Caching & Latency Strategy

## 1. Executive Summary
To satisfy the Doherty Threshold (<400ms system response time) for optimal user experience, La Fabrique implements a dual-layered caching topology for Neo4j graph queries. This architecture relies on **Redis** for sub-50ms ephemeral read caching and **PostgreSQL** for persistent, materialized audit views, dramatically reducing the computational load on the Neo4j cluster during deep graph traversals.

## 2. Architectural Topology

### 2.1 Component Responsibilities (STD-ARCH-2)
- **FastAPI Backend:** Orchestrates cache lookups, query hashing, and invalidation triggers.
- **Redis (Primary Cache):** Key-value store for JSON-serialized Neo4j query responses. Optimized for high-throughput, read-heavy endpoints (e.g., UI dashboard load, lineage visualization).
- **PostgreSQL (Secondary Materialized Cache):** Relational store for pre-aggregated graph metrics and audit summaries. Ensures strict separation of *claims* (agent logs) and *observations* (daemon logs) per **STD-AUD-1**.
- **Neo4j (Source of Truth):** The primary graph database for complex relationships, AI artifact lineage, and dynamic orchestration states.

## 3. Data Flow

### 3.1 Read Path (Query Execution)
1. **Request Initiation:** The frontend requests a graph visualization payload via a FastAPI endpoint.
2. **Key Generation:** FastAPI generates a deterministic SHA-256 hash of the Cypher query and its parameters.
3. **Redis Lookup:**
   - **Cache Hit:** Redis returns the serialized JSON payload. FastAPI serves the response to the client. Target latency: **<50ms**.
   - **Cache Miss:** FastAPI executes the query against Neo4j. Target latency: **100ms - 350ms**.
4. **Cache Hydration:** On a cache miss, the backend asynchronously writes the Neo4j response to Redis, tagging it with relevant entity identifiers (e.g., `tag:execution_id`, `tag:node_id`), and applies a default Time-To-Live (TTL).

### 3.2 Write Path (Mutation & Invalidation)
1. **Graph Mutation:** A LangGraph validation node or backend service executes a state-mutating Cypher query (e.g., adding an evaluation edge).
2. **Audit Persistence:** An observation of the mutation is written to PostgreSQL (**STD-AUD-1**).
3. **Cache Eviction:** The mutating service identifies the affected entity tags and issues a targeted invalidation command to Redis, evicting all cached queries associated with those tags.

## 4. Cache Invalidation Strategy

Maintaining cache coherence without sacrificing performance requires a hybrid invalidation approach:

### 4.1 Tag-Based Eviction (Active Invalidation)
Every cached query is mapped to one or more entity tags using Redis Sets.
- **Mapping:** `SADD graph_cache:tag:artifact:102 graph_cache:query:hash_abc`
- **Eviction:** When `artifact:102` is mutated, the backend retrieves all query hashes from the set and deletes them via `DEL`.

### 4.2 Time-To-Live (Passive Invalidation)
To prevent memory leaks and ensure eventual consistency in edge cases where active invalidation fails:
- **Dynamic Data (e.g., Active LangGraph Executions):** 60-second TTL.
- **Semi-Static Data (e.g., Historical Lineage):** 15-minute TTL.

## 5. Security & Operations
- **Configuration (STD-OPS-1):** Redis connection strings and TTL parameters are injected strictly via environment variables (`REDIS_URL`).
- **Secrets (STD-SEC-1):** No credentials or sensitive tokens are cached in Redis or hardcoded in the invalidation logic.

graph TD
    subgraph Client Tier
        UI[React + Vite SPA]
    end

    subgraph Edge & API Tier
        GW[API Gateway / Ingress]
        API[FastAPI BFF & Contract Layer]
    end

    subgraph Orchestration Tier
        LG[LangGraph Orchestrator]
        subgraph Agents
            A1[Planning Agent]
            A2[Execution Agent]
            A3[Critic Agent]
        end
    end

    subgraph Persistence & Memory Tier
        PG[(PostgreSQL + pgvector)]
        NEO[(Neo4j Engram Memory)]
        REDIS[(Redis / Task Queue)]
    end

    subgraph Observability Tier
        OTEL[OpenTelemetry Collector]
        LS[LangSmith / LangFuse]
    end

    UI -->|HTTPS / SSE| GW
    GW --> API
    API -->|Async / Sync| LG
    LG --> A1 & A2 & A3
    
    A1 & A2 & A3 -->|Relational & Vector RAG| PG
    A1 & A2 & A3 -->|Episodic & Semantic Graph| NEO
    API -->|Background Tasks| REDIS
    LG -->|State Checkpointing| REDIS

    API -.->|Traces| OTEL
    LG -.->|LLM Traces| LS
    OTEL -->|Metrics/Logs| PROM((Prometheus/Loki))
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from config import Settings
from database import register_exception_handlers, shutdown, startup
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from metrics import setup_metrics
from routers import (
    agent_runs_router,
    agents_router,
    ai_stream_router,
    auth_router,
    enterprise_router,
    github_router,
    inbox_router,
    mcp_router,
    messages_router,
    procedures_router,
    reviews_router,
    sprint_control_router,
    sprint_router,
    support_router,
    workspace_router,
)
from security import get_cors_origins, setup_security
from services.healthcheck import aggregate

settings = Settings()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Runs the startup/shutdown handlers for the app's lifetime.

    Starlette 1.0 removed `add_event_handler`, so this is the only way left to
    hook the lifecycle.
    """
    await startup(application)
    try:
        yield
    finally:
        await shutdown(application)


app = FastAPI(
    lifespan=lifespan,
    title="Fabrique Enterprise API",
    description="Autonomous AI Engineering Factory API with Multi-Tenancy, Governance, and Auto-Healing support.",
    version="2026.4.1",
    openapi_tags=[
        {"name": "Core", "description": "Factory initialization and sprint management"},
        {"name": "Enterprise", "description": "Multi-tenancy, Quotas, and Governance"},
        {"name": "Support", "description": "System health, logs, and bug reporting"},
        {"name": "Agents", "description": "LangGraph orchestration and Glassroom SSE"},
        {"name": "AI Streaming", "description": "Sub-200ms SSE streaming for AI queries & LangGraph"},
        {"name": "Auth", "description": "User registration and company membership"},
        {
            "name": "Workspace",
            "description": "The developer's machine via the workspace daemon",
        },
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The support logs download names its file in this header.
    expose_headers=["Content-Disposition"],
)

# Setup security (rate limiting + security headers)
setup_security(app)

# Setup Prometheus metrics
setup_metrics(app)

app.include_router(auth_router)
app.include_router(enterprise_router)
app.include_router(sprint_router)
app.include_router(sprint_control_router)
app.include_router(agents_router)
app.include_router(agent_runs_router)
app.include_router(ai_stream_router)
app.include_router(support_router)
app.include_router(github_router)
app.include_router(inbox_router)
app.include_router(messages_router)
app.include_router(procedures_router)
app.include_router(reviews_router)
app.include_router(workspace_router)
app.include_router(mcp_router)

# Register exception handlers
register_exception_handlers(app)


def _nats_state(request: Request) -> str:
    return getattr(request.app.state, "nats_state", "connecting")


@app.get("/health")
async def health_check(request: Request):
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "nats": _nats_state(request),
        "database": getattr(request.app.state, "db_state", "disconnected"),
        "components": await aggregate(request.app),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

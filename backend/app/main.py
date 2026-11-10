"""
Fabrique Enterprise API — FastAPI application entry point.

Responsibilities:
- Creates the FastAPI app and configures CORS
- Mounts all API routers (auth, enterprise, sprint, agents, support)
- Registers global exception handlers
- Registers lifecycle handlers (startup/shutdown)
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import nats_auth
from config import Settings
from exception_handlers import register_exception_handlers
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from lifecycle import shutdown, startup
from metrics import setup_metrics
from routers import (
    agent_runs_router,
    agents_router,
    api_tokens_router,
    architectures_router,
    auth_router,
    board_moves_router,
    console_producers_router,
    costs_router,
    enterprise_router,
    events_router,
    github_router,
    inbox_router,
    integration_router,
    manager_router,
    mcp_router,
    messages_router,
    model_settings_router,
    procedures_router,
    providers_router,
    reviews_router,
    run_secrets_router,
    specifications_router,
    sprint_control_router,
    sprint_crud_router,
    sprint_router,
    support_router,
    webhooks_router,
    workspace_router,
)
from security import get_cors_origins, setup_security
from services.integrations import webhooks
from services.platform.healthcheck import aggregate
from services.release import integration_jobs

settings = Settings()
# A delivery for the company's webhook joins every change it reports.
webhooks.install()
# A closed sprint's integration starts from the loop's sprint.closed event (issue #314).
integration_jobs.install()


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
app.include_router(console_producers_router)
app.include_router(enterprise_router)
app.include_router(events_router)
app.include_router(sprint_router)
app.include_router(sprint_control_router)
app.include_router(sprint_crud_router)
app.include_router(board_moves_router)
app.include_router(costs_router)
app.include_router(agents_router)
app.include_router(agent_runs_router)
app.include_router(api_tokens_router)
app.include_router(support_router)
app.include_router(github_router)
app.include_router(inbox_router)
app.include_router(integration_router)
app.include_router(manager_router)
app.include_router(messages_router)
app.include_router(procedures_router)
app.include_router(reviews_router)
app.include_router(run_secrets_router)
app.include_router(specifications_router)
app.include_router(architectures_router)
app.include_router(workspace_router)
app.include_router(mcp_router)
app.include_router(providers_router)
app.include_router(model_settings_router)
app.include_router(webhooks_router)

# Register exception handlers
register_exception_handlers(app)


def _nats_state(request: Request) -> str:
    return getattr(request.app.state, "nats_state", "connecting")


def _nats_health(request: Request) -> dict:
    """The bus as ``/health`` shows it: state, JetStream, auth and TLS, no secret."""
    client = getattr(request.app.state, "nats_client", None)
    security = getattr(client, "security", None) or nats_auth.describe()
    connection = getattr(client, "nc", None)
    return {
        "state": _nats_state(request),
        "connected": bool(getattr(connection, "is_connected", connection is not None)),
        "jetstream": bool(getattr(request.app.state, "jetstream", False)),
        "authenticated": bool(security.get("authenticated")),
        "tls": bool(security.get("tls")),
    }


@app.get("/health")
async def health_check(request: Request):
    nats_health = _nats_health(request)
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "nats": nats_health,
        "jetstream": nats_health["jetstream"],
    }


@app.get("/health/deep")
async def deep_health_check(request: Request):
    nats_client = getattr(request.app.state, "nats_client", None)
    return await aggregate(nats_client, nats_state=_nats_state(request))

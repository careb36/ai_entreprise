"""
Fabrique Enterprise API — FastAPI application entry point.

Responsibilities:
- Creates the FastAPI app and configures CORS
- Mounts all API routers (auth, enterprise, sprint, agents, support)
- Registers global exception handlers
- Registers lifecycle handlers (startup/shutdown)
"""

from datetime import datetime

from config import Settings
from exception_handlers import register_exception_handlers
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from lifecycle import shutdown, startup
from metrics import setup_metrics
from routers import (
    agent_runs_router,
    agents_router,
    auth_router,
    enterprise_router,
    github_router,
    sprint_router,
    support_router,
)
from security import get_cors_origins, setup_security

settings = Settings()

app = FastAPI(
    title="Fabrique Enterprise API",
    description="Autonomous AI Engineering Factory API with Multi-Tenancy, Governance, and Auto-Healing support.",
    version="2026.4.1",
    openapi_tags=[
        {"name": "Core", "description": "Factory initialization and sprint management"},
        {"name": "Enterprise", "description": "Multi-tenancy, Quotas, and Governance"},
        {"name": "Support", "description": "System health, logs, and bug reporting"},
        {"name": "Agents", "description": "LangGraph orchestration and Glassroom SSE"},
        {"name": "Auth", "description": "User registration and company membership"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup security (rate limiting + security headers)
setup_security(app)

# Setup Prometheus metrics
setup_metrics(app)

app.include_router(auth_router)
app.include_router(enterprise_router)
app.include_router(sprint_router)
app.include_router(agents_router)
app.include_router(agent_runs_router)
app.include_router(support_router)
app.include_router(github_router)

# Register exception handlers
register_exception_handlers(app)


# Register lifecycle handlers
async def on_startup():
    await startup(app)


async def on_shutdown():
    await shutdown(app)


app.add_event_handler("startup", on_startup)
app.add_event_handler("shutdown", on_shutdown)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

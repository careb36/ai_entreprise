"""
Fabrique Enterprise API — FastAPI application entry point.

Creates the application, installs middleware, registers lifecycle handlers, and
mounts the API routers.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lifecycle import configure_lifecycle
from routers import (
    agent_runs_router,
    agents_router,
    auth_router,
    core_router,
    enterprise_router,
    github_router,
    sprint_router,
    support_router,
)

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

configure_lifecycle(app)

app.include_router(core_router)
app.include_router(auth_router)
app.include_router(enterprise_router)
app.include_router(sprint_router)
app.include_router(agents_router)
app.include_router(agent_runs_router)
app.include_router(support_router)
app.include_router(github_router)

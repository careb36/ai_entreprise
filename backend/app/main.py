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
from routers.access_review import router as access_review_router
from routers.agent_runs import router as agent_runs_router
from routers.agents import router as agents_router
from routers.api_tokens import router as api_tokens_router
from routers.architectures import router as architectures_router
from routers.audit_anchors import router as audit_anchors_router
from routers.auth import router as auth_router
from routers.bench import router as bench_router
from routers.board_moves import router as board_moves_router
from routers.board_notes import router as board_notes_router
from routers.chat_approvals import router as chat_approvals_router
from routers.chat_interactions import router as chat_interactions_router
from routers.company_architecture import router as company_architecture_router
from routers.console_producers import router as console_producers_router
from routers.costs import router as costs_router
from routers.documentation import router as documentation_router
from routers.documentation_manifest import router as documentation_manifest_router
from routers.enterprise import router as enterprise_router
from routers.enterprise_agents import router as enterprise_agents_router
from routers.enterprise_analytics import router as enterprise_analytics_router
from routers.events import router as events_router
from routers.findings import router as findings_router
from routers.forge_policy import router as forge_policy_router
from routers.github import router as github_router
from routers.inbox import router as inbox_router
from routers.intake import router as intake_router
from routers.integration import router as integration_router
from routers.integration_reviews import router as integration_reviews_router
from routers.invitations import router as invitations_router
from routers.manager import router as manager_router
from routers.mcp import router as mcp_router
from routers.messages import router as messages_router
from routers.model_settings import router as model_settings_router
from routers.person_sections import router as person_sections_router
from routers.procedures import router as procedures_router
from routers.protected_paths import router as protected_paths_router
from routers.providers import router as providers_router
from routers.reviews import router as reviews_router
from routers.rsi_cycles import router as rsi_cycles_router
from routers.run_logs import router as run_logs_router
from routers.run_secrets import router as run_secrets_router
from routers.security_tests import router as security_tests_router
from routers.sod import router as sod_router
from routers.specifications import router as specifications_router
from routers.sprint import router as sprint_router
from routers.sprint_control import router as sprint_control_router
from routers.sprint_crud import router as sprint_crud_router
from routers.sprint_progress import router as sprint_progress_router
from routers.sso import router as sso_router
from routers.sso_admin import router as sso_admin_router
from routers.sso_domains import router as sso_domains_router
from routers.sso_link import router as sso_link_router
from routers.support import router as support_router
from routers.task_relations import router as task_relations_router
from routers.task_views import router as task_views_router
from routers.teams import router as teams_router
from routers.usage import router as usage_router
from routers.webhooks import router as webhooks_router
from routers.wiki import router as wiki_router
from routers.workspace import router as workspace_router
from security import cors_allows_credentials, get_cors_origins, setup_security
from services.integrations import (
    anchoring,
    chat_approvals,
    chat_regressions,
    webhooks,
)
from services.platform.healthcheck import aggregate
from services.release import integration_jobs

settings = Settings()
# A delivery for the company's webhook joins every change it reports.
webhooks.install()
# A risk hold posts its approval to the company's Slack or Teams (issue #512).
chat_approvals.install()
# A regression the factory observed is posted there too, information only (issue #682).
chat_regressions.install()
# A closed sprint or a release anchors the audit chain's head (issue #542).
anchoring.install()
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

_cors_origins = get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=cors_allows_credentials(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
    # The support logs download names its file in this header; the run log
    # view reads its integrity observation from the X-Log-* ones.
    expose_headers=[
        "Content-Disposition",
        "X-Fabrique-CSRF",
        "X-Log-Integrity",
        "X-Log-Sha256-Recorded",
        "X-Log-Sha256-Observed",
        "X-Log-Size",
        "X-Log-Observed-By",
        "X-Log-Verified",
        "X-Log-Unverified-Reason",
        "Content-Range",
        "Accept-Ranges",
    ],
)

# Setup security (rate limiting + security headers)
setup_security(app)

# Setup Prometheus metrics
setup_metrics(app)

app.include_router(auth_router)
app.include_router(sso_router)
app.include_router(sso_admin_router)
app.include_router(sso_domains_router)
app.include_router(sso_link_router)
app.include_router(console_producers_router)
app.include_router(enterprise_router)
app.include_router(enterprise_analytics_router)
app.include_router(enterprise_agents_router)
app.include_router(events_router)
app.include_router(findings_router)
app.include_router(sprint_router)
app.include_router(sprint_control_router)
app.include_router(sprint_crud_router)
app.include_router(board_moves_router)
app.include_router(costs_router)
app.include_router(agents_router)
app.include_router(audit_anchors_router)
app.include_router(agent_runs_router)
app.include_router(run_logs_router)
app.include_router(api_tokens_router)
app.include_router(support_router)
app.include_router(github_router)
app.include_router(inbox_router)
app.include_router(intake_router)
app.include_router(integration_router)
app.include_router(integration_reviews_router)
app.include_router(manager_router)
app.include_router(messages_router)
app.include_router(board_notes_router)
app.include_router(task_views_router)
app.include_router(sprint_progress_router)
app.include_router(task_relations_router)
app.include_router(wiki_router)
app.include_router(procedures_router)
app.include_router(reviews_router)
app.include_router(run_secrets_router)
app.include_router(specifications_router)
app.include_router(architectures_router)
app.include_router(company_architecture_router)
app.include_router(documentation_router)
app.include_router(documentation_manifest_router)
app.include_router(person_sections_router)
app.include_router(security_tests_router)
app.include_router(workspace_router)
app.include_router(mcp_router)
app.include_router(providers_router)
app.include_router(model_settings_router)
app.include_router(webhooks_router)
app.include_router(chat_approvals_router)
app.include_router(chat_interactions_router)
app.include_router(sod_router)
app.include_router(protected_paths_router)
app.include_router(forge_policy_router)
app.include_router(rsi_cycles_router)
app.include_router(access_review_router)
app.include_router(teams_router)
app.include_router(invitations_router)
app.include_router(usage_router)
app.include_router(bench_router)

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

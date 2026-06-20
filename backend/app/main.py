"""
Fabrique Enterprise API — FastAPI application entry point.

Responsibilities:
- Mounts all API routers (auth, enterprise, sprint, agents, support).
- Manages NATS client lifecycle: connects on startup, drains on shutdown.
- Stores infrastructure singletons on ``app.state`` (not as module globals)
  so routers access them safely via ``request.app.state`` — guaranteed to be
  populated after the startup event fires, and never imported as ``None``.
- Bridges NATS Glassroom events to per-sprint SSE queues so the frontend
  can receive real-time agent logs without polling.
- Stores background task references so they can be cancelled cleanly on shutdown.
- Installs a global exception handler that reports critical failures to the
  vendor support webhook and optionally creates a NATS maintenance ticket.
"""

import asyncio
import json
import os
import traceback
from datetime import datetime
from typing import Set

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import Settings
from database import create_db_and_tables, get_neo4j_driver
from logging_config import logger
from nats_client import NATSClient
from memory_client import MemoryClient
from llm_proxy_client import LLMProxyClient
from routers import auth_router, enterprise_router, sprint_router, agents_router, support_router
from routers.agents import publish_sse_event, sse_connections
from services.outbox_service import process_outbox
from services.agent_worker import process_agent_tasks
from services.task_worker import process_backlog_tasks

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(enterprise_router)
app.include_router(sprint_router)
app.include_router(agents_router)
app.include_router(support_router)

# Initialise state slots so type checkers and docs know what lives here.
# Actual values are set in the startup handler — never accessed before that.
app.state.nats_client: NATSClient = None
app.state.memory_client: MemoryClient = None
app.state.llm_client: LLMProxyClient = None

# Retained task references for graceful cancellation on shutdown.
_background_tasks: Set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    """Log unexpected exits from background worker tasks."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background worker task '%s' exited with an exception: %s",
            task.get_name(), exc, exc_info=exc,
        )


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all exception handler that:
    1. Logs the full traceback (truncated to 1 000 chars to avoid log flooding).
    2. POSTs a structured error report to the vendor support webhook (if configured).
    3. Publishes a ``system.maintenance.create_ticket`` NATS event so the on-call
       agent can create an incident ticket automatically.
    """
    error_type = type(exc).__name__
    error_msg = str(exc)
    trace = traceback.format_exc()[-1000:]

    logger.error(f"System Exception: {error_msg}", extra={"error_details": trace})

    vendor_webhook = os.getenv("FABRIQUE_VENDOR_SUPPORT_URL")
    if vendor_webhook:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(vendor_webhook, json={
                    "vendor_id": "CAROLINA_PEREIRA_PROD",
                    "system_version": "2026.4.1",
                    "error_type": error_type,
                    "error_message": error_msg,
                    "traceback": trace,
                    "endpoint": f"{request.method} {request.url.path}",
                }, timeout=2.0)
        except Exception as report_e:
            logger.error(f"Failed to reach Vendor Support: {report_e}")

    nats = request.app.state.nats_client
    if nats:
        try:
            await nats.publish("system.maintenance.create_ticket", json.dumps({
                "subject": "[SYSTEM ERROR] Fabrique Engine Failure",
                "description": "A report has been sent to the vendor (Carolina Pereira) for resolution.",
                "priority": "critical",
            }).encode())
        except Exception:
            pass

    return JSONResponse(
        status_code=500,
        content={"detail": "Fabrique Engine Error. A technical report has been sent to Vendor Support (Carolina Pereira)."},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    """
    Application startup handler.

    Always succeeds — NATS and Neo4j connection failures are caught and logged
    as warnings, so DB-only endpoints (discovery, init_company, sprint board,
    etc.) remain available even when those services are not yet running.

    Infrastructure clients are stored on ``app.state`` (not module globals) so
    that request handlers only see them after startup completes.  Callers must
    guard against ``None`` before using NATS/Neo4j features.

    Background tasks are launched only when NATS is reachable:
    - ``_listen_to_nats()`` — subscribes to ``glassroom.>`` for SSE fanout.
    - ``process_outbox()`` — transactional outbox → NATS publisher.
    - ``process_agent_tasks()`` — NATS consumer → LangGraph runner.
    """
    logger.info("Initializing Enterprise Backend Components...")
    create_db_and_tables()

    # Warn loudly if the JWT secret key has not been changed from the insecure default.
    if settings.JWT_SECRET_KEY == "change-me-in-production":
        logger.warning(
            "⚠️  JWT_SECRET_KEY is set to the insecure default value. "
            "Set a strong random secret via the JWT_SECRET_KEY environment variable "
            "before exposing this service to any network."
        )

    # NATS is required for agent workflow and SSE streaming, but the DB-only
    # endpoints (discovery, init_company, sprints, etc.) must remain available
    # even when the NATS broker is not yet running.
    app.state.nats_client = None
    try:
        app.state.nats_client = NATSClient(os.getenv("NATS_URL", "nats://localhost:4222"))
        await app.state.nats_client.connect()

        # Background tasks only make sense when NATS is connected.
        worker_coros = {
            "nats_glassroom_listener": _listen_to_nats(),
            "outbox_processor": process_outbox(app.state.nats_client),
            "agent_worker": process_agent_tasks(app.state.nats_client),
            "task_worker": process_backlog_tasks(app.state.nats_client),
        }
        for name, coro in worker_coros.items():
            task = asyncio.create_task(coro, name=name)
            _background_tasks.add(task)
            task.add_done_callback(_on_task_done)

        logger.info(
            "NATS connected — background workers started: %s",
            list(worker_coros.keys()),
        )
    except Exception as exc:
        logger.warning(
            "NATS unavailable at startup (%s). "
            "Agent workflow and SSE streaming are DISABLED. "
            "Start NATS (docker compose up nats) and restart the backend to enable agents.",
            exc,
        )

    # Neo4j is used for memory search and lineage; degrade gracefully if absent.
    app.state.memory_client = None
    try:
        app.state.memory_client = MemoryClient(neo4j_driver=get_neo4j_driver())
        logger.info("Neo4j connected — memory features enabled.")
    except Exception as exc:
        logger.warning(
            f"Neo4j unavailable at startup ({exc}). "
            "Memory search and lineage endpoints will return errors until Neo4j is reachable."
        )

    app.state.llm_client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://localhost:5100"))


@app.on_event("shutdown")
async def shutdown():
    """
    Graceful shutdown: cancel background tasks, then drain the NATS connection.

    Cancelling stored task references ensures the outbox poller and NATS
    listener exit cleanly instead of being killed mid-operation.
    """
    logger.info("Initiating graceful shutdown...")

    for task in list(_background_tasks):
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)

    if app.state.nats_client:
        await app.state.nats_client.close()
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# Internal NATS listener — pushes Glassroom events to SSE queues
# ---------------------------------------------------------------------------

async def _listen_to_nats():
    """
    Internal NATS subscriber that bridges Glassroom events to SSE queues.

    Subscribes to the wildcard subject ``glassroom.>`` so that any event
    published to ``glassroom.<sprint_id>`` is picked up here.  If a browser
    client has an active SSE connection for that sprint, the raw JSON payload
    is placed on its ``asyncio.Queue`` and flushed to the response stream.
    """
    async def callback(msg, data=None):
        subject = msg.subject
        if subject.startswith("glassroom."):
            sprint_id = subject.split(".")[1]
            if sprint_id in sse_connections:
                await publish_sse_event(sprint_id, msg.data.decode())

    try:
        await app.state.nats_client.subscribe("glassroom.>", callback)
    except Exception as e:
        logger.error(f"Failed to subscribe to Glassroom events: {e}")


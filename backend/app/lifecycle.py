"""Application lifecycle and process-level error handling."""

import asyncio
import json
import os
import traceback
from typing import Set

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from auth_tokens import is_auth_configured
from audit_signing import is_signing_enabled
from crypto import is_encryption_enabled
from database import create_db_and_tables, ensure_memory_graph_schema, get_neo4j_driver
from llm_proxy_client import LLMProxyClient
from logging_config import logger
from memory_client import MemoryClient
from nats_client import NATSClient
from routers.agents import publish_sse_event, sse_connections
from services.agent_worker import process_agent_tasks
from services.outbox_service import process_outbox
from services.task_worker import process_backlog_tasks

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


async def _listen_to_nats(app: FastAPI) -> None:
    """Bridge Glassroom NATS events to the active per-sprint SSE queues."""
    async def callback(msg, data=None):
        subject = msg.subject
        if subject.startswith("glassroom."):
            sprint_id = subject.split(".")[1]
            if sprint_id in sse_connections:
                await publish_sse_event(sprint_id, msg.data.decode())

    try:
        await app.state.nats_client.subscribe("glassroom.>", callback)
    except Exception as exc:
        logger.error("Failed to subscribe to Glassroom events: %s", exc)


def configure_lifecycle(app: FastAPI) -> None:
    """Register infrastructure initialization, shutdown, and exception handling."""
    app.state.nats_client: NATSClient = None
    app.state.memory_client: MemoryClient = None
    app.state.llm_client: LLMProxyClient = None

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Report unexpected failures to vendor support and the maintenance bus."""
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

    @app.on_event("startup")
    async def startup():
        """Initialize shared clients and launch workers when their services are available."""
        logger.info("Initializing Enterprise Backend Components...")
        create_db_and_tables()

        app.state.nats_client = None
        try:
            app.state.nats_client = NATSClient(os.getenv("NATS_URL", "nats://localhost:4222"))
            await app.state.nats_client.connect()

            worker_coros = {
                "nats_glassroom_listener": _listen_to_nats(app),
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

        app.state.memory_client = None
        try:
            driver = get_neo4j_driver()
            ensure_memory_graph_schema(driver)
            app.state.memory_client = MemoryClient(neo4j_driver=driver)
            logger.info("Neo4j connected — memory features enabled.")
        except Exception as exc:
            logger.warning(
                f"Neo4j unavailable at startup ({exc}). "
                "Memory search and lineage endpoints will return errors until Neo4j is reachable."
            )

        app.state.llm_client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://localhost:5100"))

        if not is_encryption_enabled():
            logger.warning(
                "FERNET_SECRET_KEY is not configured. Project credentials will be "
                "stored unencrypted with a 'plaintext:' prefix. Generate a key with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        if not is_signing_enabled():
            logger.warning(
                "AUDIT_SIGNING_KEY is not configured. Audit rows will be stored with "
                "an 'unsigned:' prefix. Generate a key with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )

        if not is_auth_configured():
            logger.warning(
                "JWT_SECRET_KEY is not configured. POST /v1/auth/login will return 503 until it is set."
            )

    @app.on_event("shutdown")
    async def shutdown():
        """Cancel background workers and close the NATS connection."""
        logger.info("Initiating graceful shutdown...")

        for task in list(_background_tasks):
            task.cancel()
        if _background_tasks:
            await asyncio.gather(*_background_tasks, return_exceptions=True)

        if app.state.nats_client:
            await app.state.nats_client.close()
        logger.info("Shutdown complete.")

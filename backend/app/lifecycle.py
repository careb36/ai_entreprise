"""
Application lifecycle handlers for startup and shutdown events.
"""

import asyncio
import os

from audit_signing import is_signing_enabled
from auth_tokens import is_auth_configured
from crypto import is_encryption_enabled
from database import create_db_and_tables, ensure_memory_graph_schema, get_neo4j_driver
from llm_proxy_client import LLMProxyClient
from logging_config import logger
from memory_client import MemoryClient
from nats_client import NATSClient
from services.agent_worker import process_agent_tasks
from services.outbox_service import process_outbox
from services.task_worker import process_backlog_tasks

# Retained task references for graceful cancellation on shutdown.
_background_tasks: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    """Log unexpected exits from background worker tasks."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background worker task '%s' exited with an exception: %s",
            task.get_name(),
            exc,
            exc_info=exc,
        )


async def startup(app):
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

    # NATS is required for agent workflow and SSE streaming, but the DB-only
    # endpoints (discovery, init_company, sprints, etc.) must remain available
    # even when the NATS broker is not yet running.
    app.state.nats_client = None
    try:
        app.state.nats_client = NATSClient(
            os.getenv("NATS_URL", "nats://localhost:4222")
        )
        await app.state.nats_client.connect()

        # Background tasks only make sense when NATS is connected.
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
    except Exception as exc:  # noqa: BLE001 - Start in degraded mode if the optional broker fails.
        logger.warning(
            "NATS unavailable at startup (%s). "
            "Agent workflow and SSE streaming are DISABLED. "
            "Start NATS (docker compose up nats) and restart the backend to enable agents.",
            exc,
        )

    # Neo4j is used for memory search and lineage; degrade gracefully if absent.
    app.state.memory_client = None
    try:
        driver = get_neo4j_driver()
        ensure_memory_graph_schema(driver)
        app.state.memory_client = MemoryClient(neo4j_driver=driver)
        logger.info("Neo4j connected — memory features enabled.")
    except Exception as exc:  # noqa: BLE001 - Start in degraded mode if the optional memory backend fails.
        logger.warning(
            f"Neo4j unavailable at startup ({exc}). "
            "Memory search and lineage endpoints will return errors until Neo4j is reachable."
        )

    app.state.llm_client = LLMProxyClient(
        os.getenv("LLM_PROXY_URL", "http://localhost:5100")
    )

    # Credential encryption degrades gracefully like the services above: without
    # a key the backend still runs, but stored credentials are not encrypted.
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


async def shutdown(app):
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


async def _listen_to_nats(app):
    """
    Internal NATS subscriber that bridges Glassroom events to SSE queues.

    Subscribes to the wildcard subject ``glassroom.>`` so that any event
    published to ``glassroom.<sprint_id>`` is picked up here.  If a browser
    client has an active SSE connection for that sprint, the raw JSON payload
    is placed on its ``asyncio.Queue`` and flushed to the response stream.
    """
    from routers.agents import publish_sse_event, sse_connections

    async def callback(msg, data=None):
        subject = msg.subject
        if subject.startswith("glassroom."):
            sprint_id = subject.split(".")[1]
            if sprint_id in sse_connections:
                await publish_sse_event(sprint_id, msg.data.decode())

    try:
        await app.state.nats_client.subscribe("glassroom.>", callback)
    except Exception as e:  # noqa: BLE001 - A streaming subscription failure must not abort startup.
        logger.error(f"Failed to subscribe to Glassroom events: {e}")

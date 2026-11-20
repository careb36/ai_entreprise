"""
Application lifecycle handlers for startup and shutdown events.
"""

import asyncio
import os

from audit_signing import is_signing_enabled
from auth_tokens import is_auth_configured
from config import settings
from crypto import is_encryption_enabled
from database import ensure_memory_graph_schema, get_neo4j_driver, prepare_database
from llm_proxy_client import LLMProxyClient
from logging_config import logger
from memory_client import MemoryClient
from nats_auth import exposed_bind_warning
from nats_client import NATSClient
from services.integrations.anchoring import cadence_loop as anchor_cadence_loop
from services.integrations.outbox_service import process_outbox
from services.loop import run_window_gate
from services.loop.task_worker import process_backlog_tasks
from services.producers import console_capabilities
from services.producers.agent_worker import process_agent_tasks
from services.release import integration_jobs
from services.workspace import daemon_liveness

# Retained task references for graceful cancellation on shutdown.
_background_tasks: set[asyncio.Task] = set()

# NATS connection state exposed by the health endpoints.
NATS_STATE_CONNECTING = "connecting"
NATS_STATE_CONNECTED = "connected"

# Reconnect backoff for the initial NATS connection: 1s, 2s, 4s, ... capped.
NATS_CONNECT_BACKOFF_BASE_SECONDS = 1.0
NATS_CONNECT_BACKOFF_MAX_SECONDS = 30.0


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


async def _connect_nats_and_start_workers(app, client: NATSClient) -> None:
    """
    Connect to NATS, retrying forever, then start the background workers once.

    The delay between attempts starts at ``NATS_CONNECT_BACKOFF_BASE_SECONDS``
    and doubles up to ``NATS_CONNECT_BACKOFF_MAX_SECONDS``.  Cancelling the
    task (as ``shutdown`` does) interrupts a pending attempt or sleep.
    """
    delay = NATS_CONNECT_BACKOFF_BASE_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            await client.connect()
            break
        except Exception as exc:  # noqa: BLE001 - Keep retrying; the API stays up in degraded mode.
            logger.warning(
                "NATS unavailable (attempt %d: %s). Agent workflow and SSE "
                "streaming are paused; retrying in %.1fs.",
                attempt,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, NATS_CONNECT_BACKOFF_MAX_SECONDS)

    # Streams must exist before the outbox publishes and the workers consume.
    app.state.jetstream = await client.enable_jetstream()
    app.state.nats_client = client
    app.state.nats_state = NATS_STATE_CONNECTED
    await _start_workers(app, client)


def _engine():
    from database import engine

    return engine


async def _start_workers(app, client: NATSClient) -> None:
    """Start the NATS-dependent background workers, at most once per app."""
    if getattr(app.state, "nats_workers_started", False):
        return
    app.state.nats_workers_started = True

    # The daemon's heartbeats, observed on this backend's clock: liveness in
    # GET /v1/workspace/status (services/workspace/daemon_liveness.py).
    try:
        await daemon_liveness.listen(client)
    except Exception as exc:  # noqa: BLE001 - The request-based check stays as the fallback.
        logger.warning(f"Not observing the daemon's heartbeat: {exc}")

    # Ask the daemon once which console assistants it can run, so the task
    # worker does not wait out a timeout per task for a CLI that is absent.
    if settings.CONSOLE_ASSISTANTS_ENABLED:
        await console_capabilities.probe_console_assistants(client)

    worker_coros = {
        "nats_glassroom_listener": _listen_to_nats(app),
        "outbox_processor": process_outbox(client),
        "agent_worker": process_agent_tasks(client),
        "task_worker": process_backlog_tasks(client),
        # Sprint integrations the daemon could not take at close (issue #314).
        "integration_retry": integration_jobs.retry_loop(client, _engine()),
        # The audit chain's head anchored outside on the Owner's cadence (issue #542).
        "audit_anchor_cadence": anchor_cadence_loop(_engine()),
        # Work held for the company's run window, released at its boundary (issue #736).
        "run_window_release": run_window_gate.worker(_engine()),
    }
    for name, coro in worker_coros.items():
        task = asyncio.create_task(coro, name=name)
        _background_tasks.add(task)
        task.add_done_callback(_on_task_done)

    logger.info(
        "NATS connected — background workers started: %s",
        list(worker_coros.keys()),
    )


def _start_memory(app, has_vector_memory: bool) -> None:
    """Memory search on pgvector, with the knowledge graph only when enabled.

    The graph is optional (issue #328), on by default: without it the memory
    client works on pgvector alone and graph readers answer "not recorded".
    ``app.state.memory_graph`` says whether the graph is kept. Enabled but not
    reachable yet (it starts slower than the backend), it is tried again in
    the background (``_connect_graph_later``) instead of staying off until a
    restart; the backend never waits for it.
    """
    app.state.memory_client = None
    app.state.memory_graph = False
    driver = None
    if not settings.NEO4J_ENABLED:
        logger.info("Knowledge graph off (NEO4J_ENABLED=false): not recorded.")
    else:
        try:
            driver = get_neo4j_driver()
            ensure_memory_graph_schema(driver)
            app.state.memory_graph = True
            logger.info("Neo4j connected: the knowledge graph is recorded.")
        except Exception as exc:  # noqa: BLE001 - Start in degraded mode if the optional graph fails.
            driver = None
            logger.warning(
                f"Neo4j enabled but unavailable at startup ({exc}). "
                "Trying again in the background; the graph is not recorded meanwhile."
            )
            try:
                task = asyncio.get_running_loop().create_task(
                    _connect_graph_later(app, has_vector_memory),
                    name="memory_graph_connect",
                )
            except RuntimeError:  # no event loop (a synchronous caller)
                task = None
            if task is not None:
                _background_tasks.add(task)
                task.add_done_callback(_on_task_done)
    if has_vector_memory:
        app.state.memory_client = MemoryClient(neo4j_driver=driver)
    else:
        logger.info("No vector_memory table: memory search and store are off.")


GRAPH_RETRY_SECONDS = 15.0
GRAPH_RETRY_ATTEMPTS = 40  # ten minutes, then the graph stays off until a restart


async def _connect_graph_later(app, has_vector_memory: bool) -> bool:
    """Connect the enabled graph once Neo4j answers; ``True`` when it did."""
    for _ in range(GRAPH_RETRY_ATTEMPTS):
        await asyncio.sleep(GRAPH_RETRY_SECONDS)
        try:
            driver = await asyncio.to_thread(get_neo4j_driver)
            await asyncio.to_thread(ensure_memory_graph_schema, driver)
        except Exception as exc:  # noqa: BLE001 - Keep trying while Neo4j starts.
            logger.debug("Neo4j still unavailable: %s", exc)
            continue
        if has_vector_memory:
            app.state.memory_client = MemoryClient(neo4j_driver=driver)
        app.state.memory_graph = True
        logger.info("Neo4j connected: the knowledge graph is recorded from now on.")
        return True
    logger.warning(
        "Neo4j stayed unavailable for %.0f minutes: the graph is not recorded "
        "until the backend restarts with it reachable.",
        GRAPH_RETRY_SECONDS * GRAPH_RETRY_ATTEMPTS / 60,
    )
    return False


def log_prompt_sections_off() -> None:
    """Warn about ``PROMPT_SECTIONS_OFF``: unknown or required names, and what is off (#443)."""
    from services.loop.worker.prompt_budget import startup_warnings

    for warning in startup_warnings():
        logger.warning(warning)


async def startup(app):
    """
    Application startup handler.

    Always succeeds — NATS and Neo4j connection failures are caught and logged
    as warnings, so DB-only endpoints (discovery, init_company, sprint board,
    etc.) remain available even when those services are not yet running.

    Infrastructure clients are stored on ``app.state`` (not module globals) so
    that request handlers only see them after startup completes.  Callers must
    guard against ``None`` before using NATS/Neo4j features.

    NATS is connected by a background task that retries with exponential
    backoff, so a broker that starts after the backend still brings agents up.
    Once connected, that task starts these workers exactly once:
    - ``_listen_to_nats()`` — subscribes to ``glassroom.>`` for SSE fanout.
    - ``process_outbox()`` — transactional outbox → NATS publisher.
    - ``process_agent_tasks()`` — NATS consumer → LangGraph runner.
    - ``process_backlog_tasks()`` — NATS consumer → backlog task execution.
    """
    logger.info("Initializing Enterprise Backend Components...")
    has_vector_memory = prepare_database()

    # NATS is required for agent workflow and SSE streaming, but the DB-only
    # endpoints (discovery, init_company, sprints, etc.) must remain available
    # even when the NATS broker is not yet running.  ``app.state.nats_client``
    # stays ``None`` until the connection succeeds.
    app.state.nats_client = None
    app.state.nats_state = NATS_STATE_CONNECTING
    app.state.nats_workers_started = False
    app.state.jetstream = False
    bind_warning = exposed_bind_warning()
    if bind_warning:
        logger.warning(bind_warning)
    log_prompt_sections_off()
    client = NATSClient(os.getenv("NATS_URL", "nats://localhost:4222"))
    connect_task = asyncio.create_task(
        _connect_nats_and_start_workers(app, client), name="nats_connector"
    )
    app.state.nats_connect_task = connect_task
    _background_tasks.add(connect_task)
    connect_task.add_done_callback(_on_task_done)

    # Snoozed intake requests come back when their date passes (issue #532).
    from database import engine as intake_engine
    from services.planning.intake_queue import run_intake_janitor

    intake_task = asyncio.create_task(
        run_intake_janitor(intake_engine), name="intake_janitor"
    )
    _background_tasks.add(intake_task)
    intake_task.add_done_callback(_on_task_done)

    # SSO domains are looked up in DNS again every SSO_DOMAIN_RECHECK_HOURS.
    from services.platform.sso_domains import nameserver_warning, run_rechecks

    if dns_warning := nameserver_warning():
        logger.warning(dns_warning)
    recheck_task = asyncio.create_task(
        run_rechecks(intake_engine), name="sso_domain_recheck"
    )
    _background_tasks.add(recheck_task)
    recheck_task.add_done_callback(_on_task_done)

    # The Support agent needs only the database and the LLM proxy.
    if settings.SUPPORT_AGENT_ENABLED:
        from database import engine
        from services.management.support_agent import run_support_agent

        support_task = asyncio.create_task(
            run_support_agent(engine), name="support_agent"
        )
        _background_tasks.add(support_task)
        support_task.add_done_callback(_on_task_done)

    # So does the Reviewer agent, which suggests on other producers' runs.
    if settings.REVIEWER_AGENT_ENABLED:
        from database import engine
        from services.management.reviewer_agent import run_reviewer_agent

        reviewer_task = asyncio.create_task(
            run_reviewer_agent(engine), name="reviewer_agent"
        )
        _background_tasks.add(reviewer_task)
        reviewer_task.add_done_callback(_on_task_done)

    # And the Manager agent, which suggests on the inbox (issue #331).
    if settings.MANAGER_AGENT_ENABLED:
        from database import engine
        from services.management.manager_agent import run_manager_agent

        manager_task = asyncio.create_task(
            run_manager_agent(engine, lambda: app.state.nats_client),
            name="manager_agent",
        )
        _background_tasks.add(manager_task)
        manager_task.add_done_callback(_on_task_done)

        # The client's brief by mail, only where SMTP is configured.
        if settings.SMTP_HOST.strip():
            from services.management.brief_mail import run_brief_mail

            brief_task = asyncio.create_task(
                run_brief_mail(engine), name="manager_brief_mail"
            )
            _background_tasks.add(brief_task)
            brief_task.add_done_callback(_on_task_done)

    _start_memory(app, has_vector_memory)

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
            "JWT_SECRET_KEY is not configured, or is shorter than its algorithm "
            "needs (32 bytes for HS256). POST /v1/auth/login will return 503 "
            "until it is set."
        )
    from session_cookies import warn_if_insecure

    insecure = warn_if_insecure()
    if insecure:
        logger.warning(insecure)
    from services.tools.mcp_policy import startup_warnings

    for warning in startup_warnings():
        logger.warning(warning)
    log_capabilities(app)


def log_capabilities(app) -> None:
    """The startup self-check: the effective capability set, names and reasons only (#751)."""
    from services.platform import capabilities

    try:
        capabilities.log_effective(capabilities.from_state(app.state))
    except Exception as exc:  # noqa: BLE001 - A self-check never stops the start.
        logger.warning("Capability self-check failed: %s", exc)


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

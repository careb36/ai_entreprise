import asyncio
import json
import os
import traceback
from datetime import datetime

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
from routers.agents import sse_connections
from services.outbox_service import process_outbox

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

# Module-level singletons used by routers via `from main import ...`
nats_client: NATSClient = None
memory_client: MemoryClient = None
llm_client: LLMProxyClient = None


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
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

    if nats_client:
        try:
            await nats_client.publish("system.maintenance.create_ticket", json.dumps({
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
    global nats_client, memory_client, llm_client

    logger.info("Initializing Enterprise Backend Components...")
    create_db_and_tables()

    nats_client = NATSClient(os.getenv("NATS_URL", "nats://localhost:4222"))
    await nats_client.connect()

    memory_client = MemoryClient(
        neo4j_driver=get_neo4j_driver(),
    )

    llm_client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://localhost:5100"))

    asyncio.create_task(_listen_to_nats())
    asyncio.create_task(process_outbox(nats_client))


@app.on_event("shutdown")
async def shutdown():
    logger.info("Initiating graceful shutdown...")
    if nats_client:
        await nats_client.close()
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# Internal NATS listener — pushes Glassroom events to SSE queues
# ---------------------------------------------------------------------------

async def _listen_to_nats():
    async def callback(msg):
        subject = msg.subject
        if subject.startswith("glassroom."):
            sprint_id = subject.split(".")[1]
            if sprint_id in sse_connections:
                queue = sse_connections[sprint_id]
                if isinstance(queue, asyncio.Queue):
                    await queue.put(msg.data.decode())
        await msg.ack()

    try:
        await nats_client.subscribe("glassroom.>", callback)
    except Exception as e:
        logger.error(f"Failed to subscribe to Glassroom events: {e}")


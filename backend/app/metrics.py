"""
Prometheus metrics for the Fabrique Enterprise API.

Exposes application metrics at /metrics endpoint.
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from logging_config import logger


# Request metrics
REQUEST_COUNT = Counter(
    "fabrique_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "fabrique_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)

# Active connections
ACTIVE_CONNECTIONS = Gauge(
    "fabrique_active_connections",
    "Number of active connections",
)

# Agent run metrics
AGENT_RUNS_INGESTED = Counter(
    "fabrique_agent_runs_ingested_total",
    "Total agent runs ingested",
    ["producer"],
)

AGENT_RUNS_VERIFIED = Counter(
    "fabrique_agent_runs_verified_total",
    "Total agent runs verified",
    ["verdict"],
)

# Sprint metrics
SPRINTS_CREATED = Counter(
    "fabrique_sprints_created_total",
    "Total sprints created",
    ["scope"],
)

SPRINTS_COMPLETED = Counter(
    "fabrique_sprints_completed_total",
    "Total sprints completed",
    ["status"],
)

# Error metrics
ERRORS_TOTAL = Counter(
    "fabrique_errors_total",
    "Total errors",
    ["type"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect request metrics."""

    async def dispatch(self, request: Request, call_next):
        import time

        ACTIVE_CONNECTIONS.inc()
        start_time = time.time()

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.url.path,
                status=response.status_code,
            ).inc()

            REQUEST_LATENCY.labels(
                method=request.method,
                endpoint=request.url.path,
            ).observe(duration)

            return response
        finally:
            ACTIVE_CONNECTIONS.dec()


def setup_metrics(app):
    """Configure metrics middleware and endpoint on the app."""
    app.add_middleware(MetricsMiddleware)

    @app.get("/metrics")
    async def metrics_endpoint():
        """Prometheus metrics endpoint."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    logger.info("Prometheus metrics configured at /metrics")


# Helper functions for recording metrics
def record_agent_run_ingested(producer: str):
    """Record an agent run ingestion."""
    AGENT_RUNS_INGESTED.labels(producer=producer).inc()


def record_agent_run_verified(verdict: str):
    """Record an agent run verification."""
    AGENT_RUNS_VERIFIED.labels(verdict=verdict).inc()


def record_sprint_created(scope: str):
    """Record a sprint creation."""
    SPRINTS_CREATED.labels(scope=scope).inc()


def record_sprint_completed(status: str):
    """Record a sprint completion."""
    SPRINTS_COMPLETED.labels(status=status).inc()


def record_error(error_type: str):
    """Record an error."""
    ERRORS_TOTAL.labels(type=error_type).inc()

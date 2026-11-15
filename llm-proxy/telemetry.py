"""
OpenTelemetry tracing for the LLM proxy.

``instrument(app)`` runs once, when ``app.py`` is imported: it registers the
proxy as service ``llm-proxy``, spans every inbound Flask request and injects
``traceparent`` into every outbound ``requests`` call, so a provider round
trip shows up under the backend's trace. Spans are exported over OTLP/HTTP
only when an endpoint is configured.

Environment variables
---------------------
``OTEL_SDK_DISABLED``
    Truthy turns the exporter off whatever else is set.
``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` / ``OTEL_EXPORTER_OTLP_ENDPOINT``
    Base URL of the OTLP/HTTP collector; ``/v1/traces`` is appended.
``OTEL_EXPORTER_OTLP_ENABLED``
    Truthy with no endpoint exports to ``http://localhost:4318``.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_otlp_endpoint() -> str | None:
    """The collector's base URL, or ``None`` when nothing is exported."""
    if _is_truthy(os.getenv("OTEL_SDK_DISABLED")):
        return None
    explicit_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if explicit_endpoint:
        return explicit_endpoint.rstrip("/")
    if _is_truthy(os.getenv("OTEL_EXPORTER_OTLP_ENABLED")):
        return "http://localhost:4318"
    return None


def instrument(flask_app) -> str | None:
    """Set the tracer provider and instrument Flask and ``requests``.

    Returns the collector's base URL, or ``None`` when export is off (the
    instrumentation still runs, so trace context still propagates).
    """
    tracer_provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "llm-proxy"}))
    endpoint_base = resolve_otlp_endpoint()
    if endpoint_base:
        otlp_exporter = OTLPSpanExporter(endpoint=endpoint_base + "/v1/traces")
        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(tracer_provider)
    FlaskInstrumentor().instrument_app(flask_app)
    RequestsInstrumentor().instrument()
    return endpoint_base

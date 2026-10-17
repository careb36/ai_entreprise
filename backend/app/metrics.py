from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "fabrique_http_requests_total",
    "Total number of HTTP requests handled by the backend.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "fabrique_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)


def record_http_request(method: str, route: str, status: int, duration: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient
from starlette.routing import Route

from app.security import SecurityMiddleware


def test_api_responses_include_security_headers(test_client):
    response = test_client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert (
        response.headers["Permissions-Policy"]
        == "camera=(), microphone=(), geolocation=()"
    )


def test_cors_allows_configured_origin_and_rejects_unknown_origin(test_client):
    allowed = test_client.get("/health", headers={"Origin": "http://localhost:5173"})
    rejected = test_client.get(
        "/health", headers={"Origin": "https://untrusted.example"}
    )

    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "Access-Control-Allow-Origin" not in rejected.headers


def test_rate_limit_returns_retry_after_for_excess_api_requests():
    async def endpoint(_request):
        return PlainTextResponse("ok")

    rate_limited_app = Starlette(routes=[Route("/v1/test", endpoint)])
    rate_limited_app.add_middleware(
        SecurityMiddleware, max_requests=1, window_seconds=60
    )
    rate_limited_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET"],
    )

    with TestClient(rate_limited_app) as client:
        headers = {"Origin": "http://localhost:5173"}
        assert client.get("/v1/test", headers=headers).status_code == 200
        limited = client.get("/v1/test", headers=headers)

    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.headers["X-Frame-Options"] == "DENY"
    assert limited.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert limited.headers["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )


def test_hsts_is_only_set_for_https_requests():
    async def endpoint(_request):
        return PlainTextResponse("ok")

    secure_app = Starlette(routes=[Route("/v1/test", endpoint)])
    secure_app.add_middleware(SecurityMiddleware)

    with TestClient(secure_app, base_url="https://testserver") as client:
        response = client.get("/v1/test")

    assert response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )

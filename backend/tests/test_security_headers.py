"""The security header baseline, per class of route (issue #541).

WSTG-CONF-14 and CONF-12 at latest@87cf4a4 (other security headers, CSP),
WSTG-v42-CLNT-09 (clickjacking), WSTG-v42-ATHN-06 (browser cache) and
WSTG-v42-CONF-05 (the documentation, which a deployment turns off). Classes:
an authenticated JSON answer, a public one, the documentation, errors (404,
401, 422 and an unhandled 500, which is built outside the middleware stack)
and a streamed answer that sets its own ``Cache-Control``.
"""

import secrets

import auth_tokens
import pytest
from config import settings
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from models import User
from security import API_CSP, SecurityHeadersMiddleware

BASELINE = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-xss-protection": "0",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
}


def _baseline(response):
    for name, value in BASELINE.items():
        assert response.headers.get(name) == value, name
    assert "max-age=31536000" in response.headers["strict-transport-security"]


def _api(response):
    _baseline(response)
    csp = response.headers["content-security-policy"]
    assert csp == API_CSP
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert response.headers["cache-control"] == "no-store"


@pytest.fixture
def bearer(sqlite_session, monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    user = User(
        email=f"headers-{secrets.token_hex(6)}@example.com",
        full_name="Headers",
        password_hash="unused",
    )
    sqlite_session.add(user)
    sqlite_session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def test_an_authenticated_json_answer_is_not_cached(test_client, bearer):
    response = test_client.get("/v1/auth/me", headers=bearer)
    assert response.status_code == 200
    _api(response)


def test_a_public_json_answer_gets_the_baseline(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200
    _api(response)


@pytest.mark.parametrize(
    "method, path, body, status",
    [
        ("get", "/nonexistent-endpoint", None, 404),
        ("post", "/v1/auth/login", {"email": 1}, 422),
    ],
)
def test_an_error_answer_gets_the_baseline(test_client, method, path, body, status):
    response = (
        getattr(test_client, method)(path, json=body)
        if body
        else getattr(test_client, method)(path)
    )
    assert response.status_code == status
    _api(response)


def test_a_refused_answer_gets_the_baseline(test_client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    response = test_client.get("/v1/auth/me")
    assert response.status_code == 401
    _api(response)


def test_an_unhandled_error_gets_the_baseline_and_no_detail(test_client, monkeypatch):
    from app import main

    def boom(request):
        raise RuntimeError("/srv/secret/path.py failed: SELECT * FROM users")

    monkeypatch.setattr(main, "_nats_health", boom)
    response = test_client.get("/health")
    assert response.status_code == 500
    _api(response)
    body = response.json()
    assert set(body) == {"detail", "error_id"}
    assert "secret" not in response.text and "SELECT" not in response.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_documentation_keeps_its_own_csp_and_cannot_be_framed(test_client, path):
    response = test_client.get(path)
    assert response.status_code == 200
    _baseline(response)
    csp = response.headers["content-security-policy"]
    assert "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'" in csp
    assert "frame-ancestors 'none'" in csp
    # Unauthenticated and static: left to the browser's default caching.
    assert "cache-control" not in response.headers


def test_redoc_allows_external_scripts_and_google_fonts(test_client):
    csp = test_client.get("/redoc").headers["Content-Security-Policy"]
    assert (
        "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com "
        "'unsafe-inline'"
    ) in csp
    assert "font-src https://fonts.gstatic.com" in csp


@pytest.mark.parametrize(
    "path", ["/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"]
)
def test_the_documentation_can_be_turned_off(test_client, monkeypatch, path):
    monkeypatch.setattr(settings, "API_DOCS_ENABLED", False)
    response = test_client.get(path)
    assert response.status_code == 404
    assert "swagger" not in response.text.lower()
    assert "openapi" not in response.text.lower()
    _api(response)


def test_the_documentation_is_on_by_default():
    from config import Settings

    assert Settings().API_DOCS_ENABLED is True


def test_a_stream_keeps_its_own_cache_control():
    """The Glassroom stream answers ``Cache-Control: no-cache``; the
    middleware adds the rest of the baseline and does not override it."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/stream")
    def stream():
        return StreamingResponse(
            iter(["data: x\n\n"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    response = TestClient(app).get("/stream")
    _baseline(response)
    assert response.headers["content-security-policy"] == API_CSP
    assert response.headers["cache-control"] == "no-cache"


def test_the_baseline_can_be_turned_off(test_client, monkeypatch):
    monkeypatch.setattr(settings, "SECURITY_HEADERS_ENABLED", False)
    response = test_client.get("/health")
    assert "content-security-policy" not in response.headers

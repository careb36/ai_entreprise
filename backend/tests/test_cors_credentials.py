"""CORS with credentials (issue #524, ``docs/archive/design/SESSIONS.md``, S6): the
session cookies cross origins only to the explicit origins in
``CORS_ORIGINS``, never to ``*``, since Starlette answers a wildcard with
credentials by echoing whatever origin asks."""

from __future__ import annotations

import security
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient


def _app(origins_setting: str, monkeypatch) -> TestClient:
    monkeypatch.setattr(security.settings, "CORS_ORIGINS", origins_setting)
    origins = security.get_cors_origins()
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=security.cors_allows_credentials(origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/ping",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-fabrique-csrf",
        },
    )


def test_explicit_origins_get_credentials(monkeypatch):
    client = _app("https://app.example.test, http://localhost:5173", monkeypatch)
    allowed = _preflight(client, "https://app.example.test")
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.test"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "x-fabrique-csrf" in allowed.headers["access-control-allow-headers"].lower()
    other = _preflight(client, "https://evil.example.test")
    assert other.status_code == 400
    assert "access-control-allow-origin" not in other.headers
    simple = client.get(
        "/ping", headers={"Origin": "https://evil.example.test", "Cookie": "a=b"}
    )
    assert "access-control-allow-origin" not in simple.headers


def test_a_wildcard_never_pairs_with_credentials(monkeypatch):
    assert security.cors_allows_credentials(["*"]) is False
    assert security.cors_allows_credentials(["https://app.example.test"]) is True
    client = _app("*", monkeypatch)
    response = client.get(
        "/ping",
        headers={"Origin": "https://evil.example.test", "Cookie": "fabrique_session=x"},
    )
    assert response.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_a_wildcard_among_origins_counts_as_a_wildcard(monkeypatch):
    monkeypatch.setattr(security.settings, "CORS_ORIGINS", "https://a.test, *")
    assert security.get_cors_origins() == ["*"]
    monkeypatch.setattr(security.settings, "CORS_ORIGINS", "https://a.test,,")
    assert security.get_cors_origins() == ["https://a.test"]


def test_the_app_pairs_credentials_with_its_configured_origins():
    """The running app: the default CORS_ORIGINS are explicit, so cookies may
    cross to them and to nothing else."""
    from app.main import app

    (cors,) = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert "*" not in cors.kwargs["allow_origins"]
    assert cors.kwargs["allow_credentials"] is True

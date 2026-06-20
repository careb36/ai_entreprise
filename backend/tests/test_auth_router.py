"""
Integration tests for the auth router endpoints.

Uses FastAPI's TestClient with an in-memory SQLite database to exercise:
- ``POST /v1/auth/register`` — creates a user.
- ``POST /v1/auth/login``    — returns a JWT for valid credentials.
- ``POST /v1/auth/login``    — returns 401 for wrong credentials.
- ``GET  /health``           — public endpoint, no auth required.
- JWT protection: a protected endpoint returns 401 without a token.

A lightweight FastAPI app is constructed with *only* the auth and enterprise
routers so the test module does not pull in LangGraph / LangChain transitive
dependencies that are not installed in the CI environment.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from database import get_session


def _make_test_app() -> TestClient:
    """
    Build a minimal FastAPI app with only the auth + enterprise routers and
    an in-memory SQLite database.  This avoids importing ``main.py`` (which
    pulls in the full LangGraph / LangChain dependency tree).
    """
    # ── Import models first so they register with SQLModel.metadata ──────────
    import models  # noqa: F401 — side-effect: registers all table models

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()

    # Health endpoint ─ public, no auth
    @app.get("/health")
    def health():
        return {"status": "ok"}

    from routers.auth import router as auth_router
    from routers.enterprise import router as enterprise_router
    app.include_router(auth_router)
    app.include_router(enterprise_router)

    app.dependency_overrides[get_session] = _override_session
    return TestClient(app, raise_server_exceptions=False)


client = _make_test_app()


# ---------------------------------------------------------------------------
# Health endpoint (public — no auth)
# ---------------------------------------------------------------------------

def test_health_endpoint_is_public():
    response = client.get("/health")
    assert response.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_new_user_returns_user_id_and_email(self):
        response = client.post("/v1/auth/register", json={
            "email": "test_reg@example.com",
            "full_name": "Test User",
            "password": "secure-password-123",
        })
        assert response.status_code == 200
        data = response.json()
        assert "user_id" in data
        assert data["email"] == "test_reg@example.com"

    def test_register_duplicate_email_returns_400(self):
        payload = {
            "email": "dup@example.com",
            "full_name": "Dup User",
            "password": "pass1",
        }
        client.post("/v1/auth/register", json=payload)
        response = client.post("/v1/auth/register", json=payload)
        assert response.status_code == 400
        assert "already registered" in response.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestLogin:
    def _register_and_login(self, email: str, password: str):
        client.post("/v1/auth/register", json={
            "email": email,
            "full_name": "Login User",
            "password": password,
        })
        return client.post(
            "/v1/auth/login",
            data={"username": email, "password": password},
        )

    def test_valid_credentials_return_access_token(self):
        response = self._register_and_login("login_ok@example.com", "mypassword")
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["email"] == "login_ok@example.com"
        assert "role" in data

    def test_wrong_password_returns_401(self):
        client.post("/v1/auth/register", json={
            "email": "wrong_pw@example.com",
            "full_name": "Wrong PW",
            "password": "correct-password",
        })
        response = client.post(
            "/v1/auth/login",
            data={"username": "wrong_pw@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401

    def test_unknown_user_returns_401(self):
        response = client.post(
            "/v1/auth/login",
            data={"username": "nobody@example.com", "password": "pass"},
        )
        assert response.status_code == 401

    def test_token_is_a_valid_jwt(self):
        """The returned token should decode to a payload with expected claims."""
        import jwt
        from config import settings

        response = self._register_and_login("jwt_check@example.com", "mypassword")
        token = response.json()["access_token"]

        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        assert payload["sub"] == "jwt_check@example.com"
        assert "user_id" in payload
        assert "role" in payload
        assert "exp" in payload


# ---------------------------------------------------------------------------
# Protected endpoints require auth
# ---------------------------------------------------------------------------

class TestAuthProtection:
    def _get_token(self, email: str = "auth_test@example.com", password: str = "pw") -> str:
        client.post("/v1/auth/register", json={
            "email": email,
            "full_name": "Auth Test",
            "password": password,
        })
        resp = client.post("/v1/auth/login", data={"username": email, "password": password})
        return resp.json()["access_token"]

    def test_enterprise_quotas_requires_auth(self):
        """GET /v1/enterprise/quotas should return 401 without a token."""
        response = client.get("/v1/enterprise/quotas?company_id=1")
        assert response.status_code == 401

    def test_enterprise_quotas_accessible_with_valid_token(self):
        """GET /v1/enterprise/quotas should be accessible with a valid token."""
        token = self._get_token("quota_user@example.com")
        auth_header = " ".join(["Bearer", token])
        response = client.get(
            "/v1/enterprise/quotas?company_id=1",
            headers={"Authorization": auth_header},
        )
        # 200 (empty list) or 404 — either way, not 401/403.
        assert response.status_code not in (401, 403)

    def test_invite_endpoint_requires_auth(self):
        """POST /v1/company/invite should return 401 without a token."""
        response = client.post("/v1/company/invite", json={
            "company_id": 1,
            "email": "nobody@example.com",
            "human_role": "Viewer",
        })
        assert response.status_code == 401

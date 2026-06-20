"""
Unit tests for backend/app/auth_dependencies.py — JWT utilities.

Tests cover:
- create_access_token mints a valid HS256 JWT with the expected claims.
- get_current_user (sync-called) returns TokenData for a valid token.
- get_current_user raises HTTP 401 for an expired token.
- get_current_user raises HTTP 401 for a tampered token.
- require_roles allows access for sufficient roles.
- require_roles raises HTTP 403 for insufficient roles.
"""

import asyncio
from datetime import timedelta

import jwt
import pytest

# auth_dependencies reads ``settings`` at import time; conftest.py already
# sets the required env vars before any module is imported.
from auth_dependencies import (
    TokenData,
    create_access_token,
    get_current_user,
    require_roles,
)
from config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(email: str = "alice@example.com", user_id: int = 1, role: str = "Manager",
                expires_delta: timedelta | None = None) -> str:
    return create_access_token(
        {"sub": email, "user_id": user_id, "role": role},
        expires_delta=expires_delta,
    )


# ---------------------------------------------------------------------------
# create_access_token
# ---------------------------------------------------------------------------

class TestCreateAccessToken:
    def test_returns_string(self):
        token = _make_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_claims_present(self):
        token = _make_token(email="bob@example.com", user_id=42, role="Owner")
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["sub"] == "bob@example.com"
        assert payload["user_id"] == 42
        assert payload["role"] == "Owner"
        assert "exp" in payload
        assert "iat" in payload

    def test_custom_expiry(self):
        """Token with a very short TTL should contain an exp close to now."""
        import time
        token = _make_token(expires_delta=timedelta(seconds=30))
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        now = int(time.time())
        assert payload["exp"] - now <= 35  # a few seconds of tolerance


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------

class TestGetCurrentUser:
    def _run(self, token: str) -> TokenData:
        """Call the async dependency synchronously for testing convenience."""
        return asyncio.run(get_current_user(token))

    def test_valid_token_returns_token_data(self):
        token = _make_token(email="carol@example.com", user_id=7, role="Viewer")
        data = self._run(token)
        assert isinstance(data, TokenData)
        assert data.email == "carol@example.com"
        assert data.user_id == 7
        assert data.role == "Viewer"

    def test_expired_token_raises_401(self):
        from fastapi import HTTPException
        token = _make_token(expires_delta=timedelta(seconds=-1))
        with pytest.raises(HTTPException) as exc_info:
            self._run(token)
        assert exc_info.value.status_code == 401

    def test_tampered_token_raises_401(self):
        from fastapi import HTTPException
        token = _make_token()
        # Tamper: flip last character
        tampered = token[:-1] + ("X" if token[-1] != "X" else "Y")
        with pytest.raises(HTTPException) as exc_info:
            self._run(tampered)
        assert exc_info.value.status_code == 401

    def test_wrong_secret_raises_401(self):
        from fastapi import HTTPException
        # Sign with a different key
        bad_token = jwt.encode(
            {"sub": "x@x.com", "user_id": 1, "role": "Viewer", "exp": 9999999999},
            "wrong-secret",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc_info:
            self._run(bad_token)
        assert exc_info.value.status_code == 401

    def test_missing_required_claim_raises_401(self):
        """Token missing the 'role' claim should be rejected."""
        from fastapi import HTTPException
        token = jwt.encode(
            {"sub": "y@y.com", "user_id": 2, "exp": 9999999999},  # no 'role'
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc_info:
            self._run(token)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# require_roles
# ---------------------------------------------------------------------------

class TestRequireRoles:
    """
    require_roles returns a dependency closure (_check).
    We call it directly with a pre-built TokenData to avoid injecting a real
    HTTP request.
    """

    def _call(self, required_role: str, user_role: str) -> TokenData:
        """
        Call the inner _check dependency with a synthetic TokenData.
        Returns the TokenData on success; re-raises HTTPException on failure.
        """
        async def _run():
            user = TokenData(user_id=1, email="test@test.com", role=user_role)
            import auth_dependencies as _auth
            from unittest.mock import AsyncMock, patch
            with patch.object(_auth, "get_current_user", new=AsyncMock(return_value=user)):
                checker = require_roles(required_role)
                return await checker(current_user=user)

        return asyncio.run(_run())

    def test_owner_can_access_owner_endpoint(self):
        result = self._call("Owner", "Owner")
        assert result.role == "Owner"

    def test_owner_can_access_manager_endpoint(self):
        result = self._call("Manager", "Owner")
        assert result.role == "Owner"

    def test_manager_can_access_manager_endpoint(self):
        result = self._call("Manager", "Manager")
        assert result.role == "Manager"

    def test_manager_cannot_access_owner_endpoint(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call("Owner", "Manager")
        assert exc_info.value.status_code == 403

    def test_viewer_cannot_access_manager_endpoint(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call("Manager", "Viewer")
        assert exc_info.value.status_code == 403

    def test_viewer_can_access_viewer_endpoint(self):
        result = self._call("Viewer", "Viewer")
        assert result.role == "Viewer"

"""Creation and validation of signed human-user access tokens."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt


class AuthTokenError(Exception):
    """Base class for authentication-token failures."""


class AuthNotConfiguredError(AuthTokenError):
    """Raised when the JWT signing key is not configured."""


class InvalidTokenError(AuthTokenError):
    """Raised when a token is malformed, invalid, or expired."""


def _configured_key() -> Optional[str]:
    """Return the configured JWT key, or ``None`` when unset or blank."""
    from config import settings

    key = getattr(settings, "JWT_SECRET_KEY", None)
    if key is None:
        return None
    key = key.strip()
    return key or None


def is_auth_configured() -> bool:
    """True when a JWT signing key is configured."""
    return _configured_key() is not None


def create_access_token(
    *,
    user_id: int,
    email: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed access token for a human user."""
    from config import settings

    key = _configured_key()
    if key is None:
        raise AuthNotConfiguredError("JWT_SECRET_KEY is not configured")

    now = datetime.now(timezone.utc)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    claims = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": expires_at,
        "typ": "access",
    }
    return jwt.encode(claims, key, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Verify an access token's signature, expiration, and token type."""
    from config import settings

    key = _configured_key()
    if key is None:
        raise AuthNotConfiguredError("JWT_SECRET_KEY is not configured")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp", "typ"]},
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise InvalidTokenError("Invalid or expired token") from exc

    if claims.get("typ") != "access":
        raise InvalidTokenError("Invalid token type")
    return claims

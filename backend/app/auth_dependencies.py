"""
JWT authentication utilities and FastAPI dependency functions.

Usage
-----
Import ``get_current_user`` as a dependency on any endpoint that requires
an authenticated caller::

    from auth_dependencies import get_current_user, TokenData

    @router.get("/protected")
    async def protected(current_user: TokenData = Depends(get_current_user)):
        ...

Import ``require_roles`` for role-gated endpoints (Owner > Manager > Viewer)::

    from auth_dependencies import require_roles

    @router.post("/admin-only")
    async def admin(current_user: TokenData = Depends(require_roles("Owner"))):
        ...

Token lifecycle
---------------
- Tokens are signed HS256 JWTs with a configurable expiry (default 8 h).
- The ``sub`` claim stores the user's email.
- The ``role`` claim stores the user's ``human_role`` (Owner / Manager / Viewer).
- The ``user_id`` claim stores the numeric PK for downstream queries.

Generating tokens
-----------------
Call ``create_access_token(data)`` in ``POST /v1/auth/login`` after
validating credentials.  ``data`` must contain at least ``{"sub": email}``.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from config import settings
from logging_config import logger

# ---------------------------------------------------------------------------
# OAuth2 bearer scheme — reads the token from the Authorization: ******
# header and exposes it to the ``get_current_user`` dependency.
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TokenData:
    """Decoded JWT payload attached to every authenticated request."""

    __slots__ = ("user_id", "email", "role")

    def __init__(self, user_id: int, email: str, role: str):
        self.user_id = user_id
        self.email = email
        self.role = role


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Mint a signed JWT access token.

    Args:
        data:           Payload dict; **must** include ``"sub"`` (email),
                        ``"user_id"`` (int), and ``"role"`` (str).
        expires_delta:  Custom TTL; defaults to ``settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES``.

    Returns:
        Encoded JWT string suitable for the ``Authorization: Bearer`` header.
    """
    payload = dict(data)
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload["exp"] = expire
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Dependency: authenticate any request
# ---------------------------------------------------------------------------

async def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenData:
    """
    FastAPI dependency — decode and validate the JWT from the Authorization header.

    Raises HTTP 401 for any token problem (expired, invalid signature, missing
    claims) so the caller never receives a partially-validated identity.

    Returns:
        ``TokenData`` carrying the authenticated user's id, email and role.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub", "user_id", "role"]},
        )
        email: str = payload["sub"]
        user_id: int = int(payload["user_id"])
        role: str = payload["role"]
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        raise credentials_exception
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise credentials_exception

    return TokenData(user_id=user_id, email=email, role=role)


# ---------------------------------------------------------------------------
# Dependency factory: role-gated access
# ---------------------------------------------------------------------------

_ROLE_RANK: dict[str, int] = {"Viewer": 1, "Manager": 2, "Owner": 3}


def require_roles(*allowed_roles: str):
    """
    Return a FastAPI dependency that enforces a minimum role.

    Roles are ordered ``Viewer < Manager < Owner``.  Passing ``"Manager"``
    also grants access to Owners.

    Example::

        @router.delete("/company/{id}", dependencies=[Depends(require_roles("Owner"))])

    Args:
        *allowed_roles: One or more role names whose rank is sufficient.
    """
    min_rank = min((_ROLE_RANK.get(r, 0) for r in allowed_roles), default=0)

    async def _check(current_user: TokenData = Depends(get_current_user)) -> TokenData:
        if _ROLE_RANK.get(current_user.role, 0) < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient role. Required: {list(allowed_roles)}",
            )
        return current_user

    return _check

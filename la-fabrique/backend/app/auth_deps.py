"""FastAPI dependencies for resolving authenticated human users and API tokens."""

import auth_tokens
from database import get_session
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from models import User
from services import api_tokens
from sqlmodel import Session

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> User:
    """Resolve a bearer access token to its current user row (or an API token)."""
    unauthorized_headers = {"WWW-Authenticate": "Bearer"}
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers=unauthorized_headers,
        )

    # Try a human JWT first.
    try:
        claims = auth_tokens.decode_access_token(credentials.credentials)
        user_id = int(claims["sub"])
        user = session.get(User, user_id)
        if user is not None:
            return user
    except (auth_tokens.AuthTokenError, KeyError, TypeError, ValueError):
        pass

    # Fall back to an API token.
    token = api_tokens.verify(session, credentials.credentials)
    if token is not None:
        synthetic = User(
            id=-token.id,
            email=f"api-token:{token.id}",
            full_name=token.name,
            password_hash="",
        )
        synthetic._api_token_company_id = token.company_id  # type: ignore[attr-defined]
        synthetic._api_token_role = "Manager"  # type: ignore[attr-defined]
        return synthetic

    raise HTTPException(
        status_code=401,
        detail="Invalid or expired token",
        headers=unauthorized_headers,
    )

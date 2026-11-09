"""API tokens for external callers (issue #327).

A Manager creates a token for one company, with a role (``Viewer`` or
``Manager``, never above the creator's own) and an expiry. Only its sha256 is
stored; the raw token is shown once. A revoked or expired token is refused.
A caller holding a token acts as ``api-token:<id>``, so the audit rows its
requests write name the token.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from models import ApiToken
from sqlmodel import Session, select

TOKEN_ROLES = ("Viewer", "Manager")
DEFAULT_ROLE = "Viewer"
DEFAULT_DAYS = 90
MAX_DAYS = 365


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(
    session: Session,
    company_id: int,
    name: str,
    created_by: str | None = None,
    scopes: list[str] | None = None,
    role: str = DEFAULT_ROLE,
    expires_in_days: int | None = DEFAULT_DAYS,
) -> tuple[ApiToken, str]:
    if role not in TOKEN_ROLES:
        raise ValueError(f"role must be one of {', '.join(TOKEN_ROLES)}")
    raw = "fab_" + secrets.token_urlsafe(32)
    token = ApiToken(
        company_id=company_id,
        name=name,
        token_hash=_hash(raw),
        created_by=created_by,
        scopes=list(scopes or []),
        role=role,
        expires_at=(
            datetime.now(UTC) + timedelta(days=expires_in_days)
            if expires_in_days
            else None
        ),
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    return token, raw


def list_tokens(session: Session, company_id: int) -> list[ApiToken]:
    return list(
        session.exec(
            select(ApiToken)
            .where(ApiToken.company_id == company_id)
            .order_by(ApiToken.id.desc())
        ).all()
    )


def revoke(session: Session, token_id: int, company_id: int) -> bool:
    token = session.get(ApiToken, token_id)
    if token is None or token.company_id != company_id:
        return False
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        session.add(token)
        session.commit()
    return True


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def expired(token: ApiToken, now: datetime | None = None) -> bool:
    if token.expires_at is None:
        return False
    return _aware(token.expires_at) <= (now or datetime.now(UTC))


def verify(session: Session, raw: str) -> ApiToken | None:
    token = session.exec(
        select(ApiToken).where(ApiToken.token_hash == _hash(raw))
    ).first()
    if token is None or token.revoked_at is not None:
        return None
    if expired(token):
        return None
    token.last_used_at = datetime.now(UTC)
    session.add(token)
    session.commit()
    return token

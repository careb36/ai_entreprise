"""API tokens for external callers."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from models import ApiToken
from sqlmodel import Session, select


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(
    session: Session,
    company_id: int,
    name: str,
    created_by: str | None = None,
    scopes: list[str] | None = None,
) -> tuple[ApiToken, str]:
    raw = "fab_" + secrets.token_urlsafe(32)
    token = ApiToken(
        company_id=company_id,
        name=name,
        token_hash=_hash(raw),
        created_by=created_by,
        scopes=list(scopes or []),
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


def verify(session: Session, raw: str) -> ApiToken | None:
    token = session.exec(
        select(ApiToken).where(ApiToken.token_hash == _hash(raw))
    ).first()
    if token is None or token.revoked_at is not None:
        return None
    token.last_used_at = datetime.now(UTC)
    session.add(token)
    session.commit()
    return token

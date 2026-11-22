"""Linking a chat account to a La Fabrique account (``docs/archive/design/CHAT_APPROVALS.md``, C4, C5).

A person signed in to La Fabrique asks for a one-time code (``issue_code``)
and types it in chat; the platform's signed request names the chat user, and
``claim`` binds the two. The code proves possession of both accounts at once:
it was shown only in the person's own session, and only the platform can
name the chat user who typed it. Emails are never compared: a chat profile's
address is set by whoever administers that workspace.

The code is ten characters of an unambiguous alphabet (about 50 bits), kept
as sha256, valid for ``CODE_TTL_MINUTES`` and used once; asking again
replaces the outstanding one. A chat user is linked to at most one account
per company, and an account to at most one chat user per provider.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from models import ChatIdentity, ChatLinkCode, CompanyMember
from sqlalchemy import delete
from sqlmodel import Session, select

CODE_TTL_MINUTES = 10
CODE_LENGTH = 10
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class LinkRefused(Exception):
    """The code cannot link this chat user; the message is safe to show in chat."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def issue_code(
    session: Session, company_id: int, user_id: int, provider: str
) -> dict[str, Any]:
    """A new one-time code for *user_id* in *company_id*; the previous one is void."""
    session.exec(
        delete(ChatLinkCode).where(
            ChatLinkCode.company_id == company_id,
            ChatLinkCode.user_id == user_id,
            ChatLinkCode.provider == provider,
        )
    )
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    expires = _now() + timedelta(minutes=CODE_TTL_MINUTES)
    session.add(
        ChatLinkCode(
            company_id=company_id,
            user_id=user_id,
            provider=provider,
            code_hash=_hash(code),
            expires_at=expires,
        )
    )
    session.commit()
    return {"code": code, "expires_at": expires.isoformat() + "Z"}


def claim(
    session: Session,
    *,
    company_id: int,
    provider: str,
    workspace_id: str,
    external_user_id: str,
    display_name: str | None,
    code: str,
) -> ChatIdentity:
    """Link the chat user who typed *code* to the account that asked for it; the caller commits."""
    if not code or len(code) > 64 or not external_user_id or not workspace_id:
        raise LinkRefused(
            "That link code is not valid. Ask for a new one in La Fabrique."
        )
    row = session.exec(
        select(ChatLinkCode).where(
            ChatLinkCode.code_hash == _hash(code),
            ChatLinkCode.company_id == company_id,
            ChatLinkCode.provider == provider,
        )
    ).first()
    if row is None:
        raise LinkRefused(
            "That link code is not valid. Ask for a new one in La Fabrique."
        )
    # Used once, whatever follows: the row count says whether this claim won.
    consumed = session.exec(delete(ChatLinkCode).where(ChatLinkCode.id == row.id))
    if consumed.rowcount != 1:
        raise LinkRefused("That link code was already used.")
    if row.expires_at < _now():
        raise LinkRefused(
            "That link code has expired. Ask for a new one in La Fabrique."
        )
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id,
            CompanyMember.user_id == row.user_id,
        )
    ).first()
    if member is None:
        raise LinkRefused("The account that asked for this code is no longer a member.")
    taken = identity_of(session, company_id, provider, workspace_id, external_user_id)
    if taken is not None and taken.user_id != row.user_id:
        raise LinkRefused(
            "This chat account is already linked to another La Fabrique account. "
            "Unlink it there first."
        )
    session.exec(
        delete(ChatIdentity).where(
            ChatIdentity.company_id == company_id,
            ChatIdentity.provider == provider,
            ChatIdentity.user_id == row.user_id,
        )
    )
    identity = ChatIdentity(
        company_id=company_id,
        user_id=row.user_id,
        provider=provider,
        workspace_id=workspace_id,
        external_user_id=external_user_id,
        display_name=(display_name or "")[:120] or None,
    )
    session.add(identity)
    session.flush()
    return identity


def identity_of(
    session: Session,
    company_id: int,
    provider: str,
    workspace_id: str,
    external_user_id: str,
) -> ChatIdentity | None:
    return session.exec(
        select(ChatIdentity).where(
            ChatIdentity.company_id == company_id,
            ChatIdentity.provider == provider,
            ChatIdentity.workspace_id == workspace_id,
            ChatIdentity.external_user_id == external_user_id,
        )
    ).first()


def links_of(session: Session, company_id: int, user_id: int) -> list[dict[str, Any]]:
    """The person's own links in the company, for their settings page."""
    rows = session.exec(
        select(ChatIdentity).where(
            ChatIdentity.company_id == company_id, ChatIdentity.user_id == user_id
        )
    ).all()
    return [
        {
            "provider": r.provider,
            "workspace_id": r.workspace_id,
            "external_user_id": r.external_user_id,
            "display_name": r.display_name,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def unlink(session: Session, company_id: int, user_id: int, provider: str) -> bool:
    result = session.exec(
        delete(ChatIdentity).where(
            ChatIdentity.company_id == company_id,
            ChatIdentity.user_id == user_id,
            ChatIdentity.provider == provider,
        )
    )
    return bool(result.rowcount)

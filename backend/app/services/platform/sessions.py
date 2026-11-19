"""Browser sessions: refresh-token families (issue #524, ``docs/design/SESSIONS.md``).

A sign-in (password or SSO) starts a **family**: a random public id the
access tokens carry as ``sid``, the digest of a CSRF token, and an absolute
expiry (``REFRESH_TOKEN_ABSOLUTE_DAYS``). Its first refresh token is issued
with it. ``rotate`` trades a refresh token for the next one, exactly once:
the old row is marked used by a conditional update, so of two concurrent
uses only one wins. Presenting a used token again is **reuse** and revokes
the whole family, the thief's new token included (RFC 9700 §4.14) — except
within ``REFRESH_REUSE_GRACE_SECONDS`` of its rotation while its successor
is still unused, which is two tabs of the same browser racing: that answers
``race`` and issues nothing.

Only digests of the values the browser holds are stored: HMAC-SHA256 keyed
with ``JWT_SECRET_KEY``, so a copy of the table is useless without the key
and rotating the key ends every family at once, as it ends every access
token (``docs/OPERATIONS.md``, Rotate keys). The access token itself is
minted by the caller (``session_cookies.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from models import RefreshToken, SessionFamily
from sqlalchemy import delete, update
from sqlmodel import Session, select

METHODS = ("password", "sso")


class SessionError(Exception):
    """A refresh token that cannot be used; ``code`` says why.

    ``invalid`` (unknown, revoked or expired), ``reuse`` (the family was
    revoked just now) or ``race`` (rotated a moment ago by another tab).
    """

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Issued:
    family: SessionFamily
    refresh_token: str
    csrf_token: str | None = None  # only when the family starts


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def digest(value: str) -> str:
    """HMAC-SHA256 of *value* under the signing key (empty without one, when
    no session can start anyway)."""
    import auth_tokens

    key = (auth_tokens._configured_key() or "").encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _settings():
    from config import settings

    return settings


def csrf_matches(family_or_hash: SessionFamily | str, presented: str | None) -> bool:
    expected = (
        family_or_hash if isinstance(family_or_hash, str) else family_or_hash.csrf_hash
    )
    if not presented or not expected:
        return False
    return hmac.compare_digest(digest(presented), expected)


def _new_refresh(session: Session, family: SessionFamily, now: datetime) -> str:
    raw = secrets.token_urlsafe(32)
    session.add(
        RefreshToken(family_id=family.id, token_hash=digest(raw), created_at=now)
    )
    return raw


def purge_expired(session: Session, now: datetime | None = None) -> None:
    """Drop families past their absolute expiry, and their tokens: nothing
    can use them any more."""
    now = now or _now()
    dead = select(SessionFamily.id).where(SessionFamily.expires_at < now)
    session.exec(delete(RefreshToken).where(RefreshToken.family_id.in_(dead)))
    session.exec(delete(SessionFamily).where(SessionFamily.expires_at < now))


def start(session: Session, user_id: int, method: str) -> Issued:
    """A new family for *user_id*, with its first refresh token and its CSRF
    token. Nothing is adopted from cookies the browser already had."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {', '.join(METHODS)}")
    now = _now()
    purge_expired(session, now)
    csrf = secrets.token_urlsafe(32)
    family = SessionFamily(
        public_id=secrets.token_urlsafe(24),
        user_id=user_id,
        method=method,
        csrf_hash=digest(csrf),
        created_at=now,
        expires_at=now + timedelta(days=_settings().REFRESH_TOKEN_ABSOLUTE_DAYS),
        last_used_at=now,
    )
    session.add(family)
    session.flush()
    raw = _new_refresh(session, family, now)
    session.commit()
    session.refresh(family)
    return Issued(family=family, refresh_token=raw, csrf_token=csrf)


def active(session: Session, public_id: str | None) -> SessionFamily | None:
    """The family *public_id* names, while it is neither revoked nor expired."""
    if not public_id:
        return None
    family = session.exec(
        select(SessionFamily).where(SessionFamily.public_id == public_id)
    ).first()
    if family is None or family.revoked_at is not None:
        return None
    if family.expires_at <= _now():
        return None
    return family


def _token(session: Session, raw: str | None) -> RefreshToken | None:
    if not raw or len(raw) > 256:
        return None
    return session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == digest(raw))
    ).first()


def revoke(session: Session, family: SessionFamily, reason: str) -> None:
    if family.revoked_at is None:
        family.revoked_at = _now()
        family.revoked_reason = reason
        session.add(family)
        session.commit()


def rotate(session: Session, raw: str | None) -> Issued:
    """The next refresh token of *raw*'s family; raises ``SessionError``."""
    now = _now()
    row = _token(session, raw)
    family = session.get(SessionFamily, row.family_id) if row else None
    if row is None or family is None or family.revoked_at is not None:
        raise SessionError("invalid")
    if family.expires_at <= now:
        raise SessionError("invalid")
    if row.used_at is None:
        claimed = session.exec(
            update(RefreshToken)
            .where(RefreshToken.id == row.id, RefreshToken.used_at.is_(None))
            .values(used_at=now)
        )
        if claimed.rowcount == 1:
            family.last_used_at = now
            session.add(family)
            new_raw = _new_refresh(session, family, now)
            session.commit()
            session.refresh(family)
            return Issued(family=family, refresh_token=new_raw)
        session.rollback()
        session.refresh(row)
    # Used before: a race between two tabs, or a replay.
    successor = session.exec(
        select(RefreshToken)
        .where(RefreshToken.family_id == family.id, RefreshToken.id > row.id)
        .order_by(RefreshToken.id)
    ).first()
    grace = timedelta(seconds=_settings().REFRESH_REUSE_GRACE_SECONDS)
    if (
        row.used_at is not None
        and now - row.used_at <= grace
        and successor is not None
        and successor.used_at is None
    ):
        raise SessionError("race")
    revoke(session, family, "reuse")
    raise SessionError("reuse")


def family_of(session: Session, raw: str | None) -> SessionFamily | None:
    """The family a refresh token, current or already rotated, belongs to."""
    row = _token(session, raw)
    return session.get(SessionFamily, row.family_id) if row else None


def revoke_users(
    session: Session,
    user_ids: list[int],
    reason: str,
    method: str | None = None,
    commit: bool = True,
) -> int:
    """Revoke the live families of *user_ids* (only those started by
    *method*, when given); the number revoked. With *commit* false the caller
    commits, so the revocation joins its transaction (an audited unlink)."""
    if not user_ids:
        return 0
    query = select(SessionFamily).where(
        SessionFamily.user_id.in_(user_ids),
        SessionFamily.revoked_at.is_(None),
    )
    if method is not None:
        query = query.where(SessionFamily.method == method)
    now = _now()
    families = session.exec(query).all()
    for family in families:
        family.revoked_at = now
        family.revoked_reason = reason
        session.add(family)
    if commit:
        session.commit()
    return len(families)

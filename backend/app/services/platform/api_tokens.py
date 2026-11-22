"""API tokens for external callers (issue #327).

A Manager creates a token for one company, with a role (``Viewer`` or
``Manager``, never above the creator's own) and an expiry. Only its sha256 is
stored; the raw token is shown once. A revoked or expired token is refused.
A caller holding a token acts as ``api-token:<id>``, so the audit rows its
requests write name the token.

A token never outranks its creator's **current** effective role in its company
(issue #512, ``docs/design/TEAMS_AND_USAGE.md`` T5). On every use, a token
whose creator was demoted below it, left the company or no longer has an
account is refused, not quietly lowered: the role on the token is what its
holder was promised, and a request that would act below it fails loudly
rather than half-working. The refusal is an ``api_token_refused`` audit row
with the reason (at most one per token, reason and hour, so a script retrying
cannot flood the chain). The token is not revoked: promoted back, its creator
carries it again. A token with no recorded creator (minted while
authentication was off) is not capped; the access review lists it with no
owner (``docs/design/SEGREGATION_OF_DUTIES.md``, residual risks).

The creator is found by account id (``created_by_user_id``), never by email
when the id is there: a creator who changes address keeps the token's
standing, and whoever takes the old address does not inherit it. An id whose
account is gone is refused; it does not fall back to the email. A token from
before the id was recorded that migration 0048 could not match to an account
(no account held the address then) has no id and is judged by its email, as
before.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from models import ApiToken, AuditLog, User
from sqlmodel import Session, select

from services.contracts.emails import normalize_email
from services.platform import teams
from services.platform.agent_service import AgentService

TOKEN_ROLES = ("Viewer", "Manager")
DEFAULT_ROLE = "Viewer"
DEFAULT_DAYS = 90
MAX_DAYS = 365
REFUSED_ACTION = "api_token_refused"
NOT_A_MEMBER = "creator_not_a_member"
BELOW_TOKEN = "creator_role_below_token"
REFUSAL_AUDIT_EVERY = timedelta(hours=1)


# Every raw token starts with this, so a request can be told to carry one.
PREFIX = "fab_"


def bearer_is_token(authorization: str | None) -> bool:
    """Whether an ``Authorization`` header carries an API token (valid or not)."""
    scheme, _, value = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and value.strip().startswith(PREFIX)


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
    created_by_user_id: int | None = None,
) -> tuple[ApiToken, str]:
    if role not in TOKEN_ROLES:
        raise ValueError(f"role must be one of {', '.join(TOKEN_ROLES)}")
    raw = PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        company_id=company_id,
        name=name,
        token_hash=_hash(raw),
        created_by=created_by,
        created_by_user_id=created_by_user_id,
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


def refusal(token_role: str | None, creator_role: str | None) -> str | None:
    """Pure: why a token of *token_role* is refused for a creator now holding
    *creator_role* (``None``: not a member), or ``None`` when it may act."""
    if creator_role not in teams.ROLE_RANK:
        return NOT_A_MEMBER
    # A token from before roles acts as a Manager (auth_deps), so it is held
    # to a Manager's rank.
    rank = teams.ROLE_RANK.get(token_role or "Manager", teams.ROLE_RANK["Manager"])
    if rank > teams.ROLE_RANK[creator_role]:
        return BELOW_TOKEN
    return None


def creator_role(session: Session, token: ApiToken) -> str | None:
    """The effective role, teams included, the token's creator holds now in
    the token's company; ``None`` when the account is gone or not a member.
    By id when the token has one; by email only on a row without it."""
    if token.created_by_user_id is not None:
        creator = session.get(User, token.created_by_user_id)
    else:
        creator = session.exec(
            select(User).where(User.email == normalize_email(token.created_by or ""))
        ).first()
    if creator is None or creator.id is None:
        return None
    return teams.member_role(session, token.company_id, creator.id)


def _audit_refusal(
    session: Session, token: ApiToken, reason: str, role: str | None
) -> None:
    actor = f"api-token:{token.id}"
    since = (datetime.now(UTC) - REFUSAL_AUDIT_EVERY).replace(tzinfo=None)
    recent = session.exec(
        select(AuditLog).where(
            AuditLog.company_id == token.company_id,
            AuditLog.action == REFUSED_ACTION,
            AuditLog.interaction_id == actor,
            AuditLog.created_at >= since,
        )
    ).all()
    if any((row.result or {}).get("reason") == reason for row in recent):
        return
    AgentService().audit_interaction(
        session=session,
        company_id=token.company_id,
        interaction_id=actor,
        agent_id=actor,
        action=REFUSED_ACTION,
        details={
            "token_id": token.id,
            "token_name": token.name,
            "token_role": token.role,
            "creator": token.created_by,
            "creator_user_id": token.created_by_user_id,
            "creator_role": role,
        },
        result={"outcome": "refused", "reason": reason},
        decision_signature=None,
        cost_tokens=0,
    )


def verify(session: Session, raw: str) -> ApiToken | None:
    token = session.exec(
        select(ApiToken).where(ApiToken.token_hash == _hash(raw))
    ).first()
    if token is None or token.revoked_at is not None:
        return None
    if expired(token):
        return None
    if token.created_by or token.created_by_user_id is not None:
        role = creator_role(session, token)
        reason = refusal(token.role, role)
        if reason is not None:
            _audit_refusal(session, token, reason, role)
            return None
    token.last_used_at = datetime.now(UTC)
    session.add(token)
    session.commit()
    return token

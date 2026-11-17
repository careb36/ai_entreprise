"""Pending invitations to a company (WSTG-v42-IDNT-04; ``models/invitation.py``,
``routers/invitations.py``).

Inviting used to add an existing account straight away, and answered 404 for
an unknown address, so anyone who created a company could test which
addresses had accounts, and a person became a member without being asked.
Now an invitation is a record: the company, the normalised address, the role,
who invited, an expiry (``INVITATION_TTL_HOURS``) and a single-use token kept
only as its sha256. The answer to the inviter is the same whether or not the
address has an account. The invitee sees their pending invitations once
signed in (or follows the token's link while signed in as that address) and
accepts or declines; only an acceptance creates the membership.

- An inviter invites up to their own effective role: a Manager invites
  Viewers and Managers, only an Owner invites an Owner (``can_invite``).
- Accepting, declining and revoking are conditional updates on
  ``status = 'pending'``, so an invitation is used once even under a race.
- A new invitation of the same address to the same company revokes the
  pending one, so one link works at a time.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from models import Company, CompanyInvitation, CompanyMember, User
from sqlalchemy import update
from sqlmodel import Session, select

from services.contracts.emails import normalize_email
from services.platform.agent_service import AgentService
from services.platform.teams import ROLE_RANK

PENDING, ACCEPTED, DECLINED, REVOKED = "pending", "accepted", "declined", "revoked"
TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _settings():
    from config import settings

    return settings


def digest(token: str) -> str:
    """The sha256 of a token, the only form stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def can_invite(inviter_role: str | None, role: str) -> bool:
    """Whether someone whose effective role is *inviter_role* may invite as
    *role*: Manager or above, and never above their own role."""
    rank = ROLE_RANK.get(inviter_role or "", 0)
    return rank >= ROLE_RANK["Manager"] and ROLE_RANK.get(role, 99) <= rank


def is_expired(invitation: CompanyInvitation, now: datetime | None = None) -> bool:
    return invitation.expires_at <= (now or _now())


def create(
    session: Session, company_id: int, email: str, role: str, inviter: User
) -> tuple[CompanyInvitation | None, str]:
    """A pending invitation and its token (shown once). For an address that
    is already a member nothing is recorded and the token names nothing, so
    the inviter's answer looks the same. Does not commit."""
    email = normalize_email(email)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    member = session.exec(
        select(CompanyMember)
        .join(User, User.id == CompanyMember.user_id)
        .where(CompanyMember.company_id == company_id, User.email == email)
    ).first()
    if member is not None:
        return None, token
    now = _now()
    session.execute(
        update(CompanyInvitation)
        .where(
            CompanyInvitation.company_id == company_id,
            CompanyInvitation.email == email,
            CompanyInvitation.status == PENDING,
        )
        .values(status=REVOKED, responded_at=now)
    )
    invitation = CompanyInvitation(
        company_id=company_id,
        email=email,
        human_role=role,
        invited_by_user_id=inviter.id,
        invited_by=inviter.email,
        token_sha256=digest(token),
        status=PENDING,
        created_at=now,
        expires_at=now + timedelta(hours=int(_settings().INVITATION_TTL_HOURS)),
    )
    session.add(invitation)
    session.flush()
    return invitation, token


def pending_for(session: Session, email: str) -> list[tuple[CompanyInvitation, str]]:
    """The invitations waiting for *email* that have not expired, newest
    first, with their company's name."""
    rows = session.exec(
        select(CompanyInvitation, Company.company_name)
        .join(Company, Company.id == CompanyInvitation.company_id)
        .where(
            CompanyInvitation.email == normalize_email(email),
            CompanyInvitation.status == PENDING,
            CompanyInvitation.expires_at > _now(),
        )
        .order_by(CompanyInvitation.created_at.desc(), CompanyInvitation.id.desc())
    ).all()
    return [(invitation, name) for invitation, name in rows]


def pending_in(session: Session, company_id: int) -> list[CompanyInvitation]:
    """The company's pending invitations that have not expired."""
    return list(
        session.exec(
            select(CompanyInvitation)
            .where(
                CompanyInvitation.company_id == company_id,
                CompanyInvitation.status == PENDING,
                CompanyInvitation.expires_at > _now(),
            )
            .order_by(CompanyInvitation.created_at.desc(), CompanyInvitation.id.desc())
        ).all()
    )


def addressed_to(invitation: CompanyInvitation | None, user: User) -> bool:
    return invitation is not None and invitation.email == normalize_email(
        user.email or ""
    )


def by_token(session: Session, token: str) -> CompanyInvitation | None:
    return session.exec(
        select(CompanyInvitation).where(CompanyInvitation.token_sha256 == digest(token))
    ).first()


def close(
    session: Session, invitation: CompanyInvitation, status: str, by: User | None
) -> bool:
    """Move a pending invitation to *status*; ``False`` when it was no longer
    pending (used already, or by a concurrent request). Does not commit."""
    result = session.execute(
        update(CompanyInvitation)
        .where(
            CompanyInvitation.id == invitation.id,
            CompanyInvitation.status == PENDING,
        )
        .values(
            status=status,
            responded_at=_now(),
            responded_by_user_id=by.id if by is not None else None,
        )
    )
    if result.rowcount != 1:
        return False
    session.refresh(invitation)
    return True


def join(session: Session, invitation: CompanyInvitation, user: User) -> str:
    """The membership an accepted invitation grants; an existing membership
    keeps its role. Returns the member's role. Does not commit."""
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == invitation.company_id,
            CompanyMember.user_id == user.id,
        )
    ).first()
    if member is not None:
        return member.human_role
    session.add(
        CompanyMember(
            company_id=invitation.company_id,
            user_id=user.id,
            human_role=invitation.human_role,
        )
    )
    return invitation.human_role


def audit(
    session: Session, invitation: CompanyInvitation, actor: User, action: str, **result
) -> None:
    """``invitation_created``, ``_accepted``, ``_declined`` or ``_revoked``,
    with who did it; never the token or its digest."""
    AgentService().audit_interaction(
        session=session,
        company_id=invitation.company_id,
        interaction_id=f"invitations:{invitation.company_id}",
        agent_id=f"human:{actor.email}",
        action=action,
        details={
            "company_id": invitation.company_id,
            "invitation_id": invitation.id,
            "email": invitation.email,
            "human_role": invitation.human_role,
        },
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def serialize(invitation: CompanyInvitation, company_name: str | None = None) -> dict:
    """An invitation for its invitee or its company; never the token digest."""
    out = {
        "id": invitation.id,
        "company_id": invitation.company_id,
        "email": invitation.email,
        "human_role": invitation.human_role,
        "invited_by": invitation.invited_by,
        "status": invitation.status,
        "created_at": invitation.created_at.isoformat(),
        "expires_at": invitation.expires_at.isoformat(),
    }
    if company_name is not None:
        out["company_name"] = company_name
    return out

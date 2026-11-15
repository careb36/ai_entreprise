"""Opt-in authentication and company membership policy."""

from auth_deps import bearer, get_current_user, has_credentials
from config import settings
from database import get_session
from fastapi import Depends, HTTPException, Request
from models import CompanyMember, User
from services.platform import teams
from services.platform.teams import ROLE_RANK
from sqlmodel import Session, select


async def current_user_if_required(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
) -> User | None:
    if not settings.AUTH_REQUIRED:
        # Even when auth is not required, resolve the user from the token or
        # the session cookie if one is sent, so ownership and memberships work.
        try:
            if has_credentials(request):
                return get_current_user(
                    request=request, credentials=await bearer(request), session=session
                )
        except Exception:  # noqa: BLE001, S110 - Optional identity is ignored when auth is disabled.
            pass
        return None
    return get_current_user(
        request=request, credentials=await bearer(request), session=session
    )


def require_company_role(
    session: Session, user: User | None, company_id: int, minimum: str
) -> None:
    if user is None:
        return
    # API token callers carry their company and role on the synthetic user.
    token_company = getattr(user, "_api_token_company_id", None)
    if token_company is not None:
        if token_company != company_id:
            raise HTTPException(status_code=403, detail="Not a member of this company")
        token_role = getattr(user, "_api_token_role", "Manager")
        if ROLE_RANK.get(token_role, 0) < ROLE_RANK[minimum]:
            raise HTTPException(status_code=403, detail=f"Requires {minimum} role")
        return
    # The effective role: the member's own role raised by their teams' roles
    # (Viewer or Manager, never Owner); a team never makes anyone a member.
    role = teams.member_role(session, company_id, user.id)
    if role is None:
        raise HTTPException(status_code=403, detail="Not a member of this company")
    if ROLE_RANK.get(role, 0) < ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail=f"Requires {minimum} role")


def require_authenticated(user: User | None) -> None:
    if settings.AUTH_REQUIRED and user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )


def viewer_company_ids(session: Session, user: User) -> list[int]:
    token_company = getattr(user, "_api_token_company_id", None)
    if token_company is not None:
        return [token_company]
    return list(
        session.exec(
            select(CompanyMember.company_id).where(
                CompanyMember.user_id == user.id,
                CompanyMember.human_role.in_(ROLE_RANK),
            )
        ).all()
    )


def is_support_admin(user: User | None) -> bool:
    """Whether *user* is listed in ``SUPPORT_ADMIN_EMAILS``.

    With authentication disabled and no token, every caller acts as an admin,
    as on the other routes of a local demo.
    """
    if user is None:
        return not settings.AUTH_REQUIRED
    admins = {
        email.strip().lower()
        for email in settings.SUPPORT_ADMIN_EMAILS.split(",")
        if email.strip()
    }
    return (user.email or "").lower() in admins


def require_support_admin(user: User | None) -> None:
    require_authenticated(user)
    if not is_support_admin(user):
        raise HTTPException(status_code=403, detail="Requires support admin")

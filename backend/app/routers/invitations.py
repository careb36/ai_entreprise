"""Invitations to a company (WSTG-v42-IDNT-04; ``services/platform/invitations.py``).

- ``POST   /v1/company/invite``                             invite an address (Manager; up to the inviter's role)
- ``GET    /v1/invitations``                                the signed-in person's pending invitations
- ``POST   /v1/invitations/accept``                         accept by the token of a link
- ``POST   /v1/invitations/{id}/accept``                    accept one of mine
- ``POST   /v1/invitations/{id}/decline``                   decline one of mine
- ``GET    /v1/companies/{id}/invitations``                 the company's pending invitations (Manager)
- ``DELETE /v1/companies/{id}/invitations/{invitation_id}`` revoke one (Manager; up to their role)

The invite answers 202 with the same body whether or not the address has an
account, and adds nobody: the invitee accepts, and only then is a member.
Every route needs a signed-in person; an API token is refused, since an
invitation grants a role. An invitation that is not the caller's, or no
longer pending, is a 404 that says nothing more.
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Response
from models import CompanyInvitation, User
from schemas import InvitationTokenRequest, InviteMemberRequest
from services.platform import invitations, teams
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])

INVITED = (
    "If this address can be invited, an invitation is waiting for it: the person "
    "accepts it after signing in with that address."
)
NOT_FOUND = "No pending invitation"


def _person(user: User | None) -> User:
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="Invitations are handled by a person, not a token"
        )
    return user


def _inviter(session: Session, user: User | None, company_id: int, role: str) -> User:
    person = _person(user)
    require_company_role(session, person, company_id, "Manager")
    own = teams.member_role(session, company_id, person.id)
    if not invitations.can_invite(own, role):
        raise HTTPException(
            status_code=403,
            detail=f"Your role here ({own}) cannot invite or revoke a {role}",
        )
    return person


@router.post("/company/invite", status_code=202)
async def invite_member(
    req: InviteMemberRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Invite an address to the company with a role.

    The answer is the same whether or not the address has an account, or is
    already a member (WSTG-v42-IDNT-04). ``token`` is shown once, for a link
    the inviter may send; it accepts only for the signed-in invited address.
    """
    inviter = _inviter(session, user, req.company_id, req.human_role)
    invitation, token = invitations.create(
        session, req.company_id, req.email, req.human_role, inviter
    )
    if invitation is not None:
        invitations.audit(
            session,
            invitation,
            inviter,
            "invitation_created",
            expires_at=invitation.expires_at.isoformat(),
        )
    session.commit()
    return {"status": "pending", "detail": INVITED, "token": token}


@router.get("/invitations")
async def my_invitations(
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The pending invitations addressed to the signed-in person's email."""
    person = _person(user)
    return {
        "invitations": [
            invitations.serialize(invitation, name)
            for invitation, name in invitations.pending_for(session, person.email)
        ]
    }


def _respond(
    session: Session, invitation: CompanyInvitation | None, person: User, accept: bool
) -> dict:
    if not invitations.addressed_to(invitation, person):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if invitation.status != invitations.PENDING:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if invitations.is_expired(invitation):
        raise HTTPException(status_code=410, detail="This invitation has expired")
    status = invitations.ACCEPTED if accept else invitations.DECLINED
    if not invitations.close(session, invitation, status, person):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if accept:
        role = invitations.join(session, invitation, person)
        invitations.audit(
            session, invitation, person, "invitation_accepted", member_role=role
        )
    else:
        role = None
        invitations.audit(session, invitation, person, "invitation_declined")
    session.commit()
    return {
        "status": status,
        "company_id": invitation.company_id,
        "human_role": role,
    }


@router.post("/invitations/accept")
async def accept_by_token(
    req: InvitationTokenRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Accept the invitation a link's token names, as its invited address."""
    person = _person(user)
    return _respond(session, invitations.by_token(session, req.token), person, True)


@router.post("/invitations/{invitation_id}/accept")
async def accept_invitation(
    invitation_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    invitation = session.get(CompanyInvitation, invitation_id)
    return _respond(session, invitation, person, True)


@router.post("/invitations/{invitation_id}/decline")
async def decline_invitation(
    invitation_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    invitation = session.get(CompanyInvitation, invitation_id)
    return _respond(session, invitation, person, False)


@router.get("/companies/{company_id}/invitations")
async def company_invitations(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's pending invitations, for its Managers and Owners."""
    person = _person(user)
    require_company_role(session, person, company_id, "Manager")
    return {
        "invitations": [
            invitations.serialize(row)
            for row in invitations.pending_in(session, company_id)
        ]
    }


@router.delete("/companies/{company_id}/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    company_id: int,
    invitation_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Revoke a pending invitation; a Manager revokes what a Manager may invite."""
    person = _person(user)
    require_company_role(session, person, company_id, "Manager")
    invitation = session.get(CompanyInvitation, invitation_id)
    if invitation is None or invitation.company_id != company_id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    _inviter(session, person, company_id, invitation.human_role)
    if not invitations.close(session, invitation, invitations.REVOKED, person):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    invitations.audit(session, invitation, person, "invitation_revoked")
    session.commit()
    return Response(status_code=204)

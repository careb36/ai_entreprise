"""SSO group mapping router — identity-provider groups to teams, decided by
the company's Owners (issue #703; ``docs/archive/design/SSO_GROUP_MAPPING.md``).

A signed-in human Owner only; an API token is refused whatever its role, and
the acting Owner is read from the session, never from the body (R15, G8, G19).

- ``GET  /v1/companies/{id}/sso/group-mappings``                     the mapping, proposals, last overage
- ``PUT  /v1/companies/{id}/sso/group-mappings``                     on/off, claim, cap and the pairs
- ``POST …/sso/group-mappings/approvals/{approval_id}/approve``      a second Owner approves a Manager pair
- ``POST …/sso/group-mappings/approvals/{approval_id}/reject``       any Owner rejects it
- ``POST …/sso/group-mappings/approvals/{approval_id}/withdraw``     its proposer withdraws it
- ``POST …/sso/group-mappings/approvals/{approval_id}/confirm-single-owner``  the only Owner confirms it
"""

from __future__ import annotations

from datetime import UTC, datetime

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from models import SsoConnection, User
from pydantic import Field
from schemas import RequestModel
from services.platform import sso_group_approvals as approvals
from services.platform import sso_group_config as config
from services.platform import sso_groups as rules
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["SSO"])

NO_STORE = {"Cache-Control": "no-store"}


class GroupPair(RequestModel):
    group_value: str = Field(min_length=1, max_length=rules.MAX_VALUE_CHARS)
    team_id: int


class GroupMappingIn(RequestModel):
    groups_enabled: bool = False
    groups_claim: str = Field(default=rules.DEFAULT_CLAIM, max_length=64)
    group_role_cap: str = Field(default="Viewer", pattern="^(Viewer|Manager)$")
    mappings: list[GroupPair] = Field(default_factory=list, max_length=rules.MAX_PAIRS)


class RejectIn(RequestModel):
    reason: str | None = Field(default=None, max_length=approvals.REASON_RANGE[1])


class SingleOwnerIn(RequestModel):
    confirmation: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


def _owner(session: Session, user: User | None, company_id: int) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="The group mapping is changed by a person, not a token",
        )
    require_company_role(session, user, company_id, "Owner")
    return user


def _connection(session: Session, company_id: int) -> SsoConnection:
    row = session.exec(
        select(SsoConnection).where(SsoConnection.company_id == company_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="no SSO connection")
    return row


def _answer(session: Session, conn: SsoConnection, **extra) -> JSONResponse:
    session.refresh(conn)
    return JSONResponse({**config.view(session, conn), **extra}, headers=NO_STORE)


@router.get("/companies/{company_id}/sso/group-mappings")
async def get_group_mappings(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    _owner(session, user, company_id)
    return _answer(session, _connection(session, company_id))


@router.put("/companies/{company_id}/sso/group-mappings")
async def put_group_mappings(
    company_id: int,
    body: GroupMappingIn,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    conn = _connection(session, company_id)
    try:
        summary = config.save(
            session,
            conn,
            actor,
            enabled=body.groups_enabled,
            claim=body.groups_claim,
            cap=body.group_role_cap,
            pairs=[p.model_dump() for p in body.mappings],
        )
    except rules.GroupConfigError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return _answer(session, conn, saved=summary)


def _decided(session: Session, company_id: int, fn, *args) -> JSONResponse:
    conn = _connection(session, company_id)
    try:
        row = fn(session, conn, *args)
    except approvals.ApprovalRefused as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    now = datetime.now(UTC).replace(tzinfo=None)
    return _answer(session, conn, decided=config.serialize_approval(row, now))


@router.post(
    "/companies/{company_id}/sso/group-mappings/approvals/{approval_id}/approve"
)
async def approve_pair(
    company_id: int,
    approval_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    return _decided(session, company_id, approvals.approve, actor, approval_id)


@router.post(
    "/companies/{company_id}/sso/group-mappings/approvals/{approval_id}/reject"
)
async def reject_pair(
    company_id: int,
    approval_id: int,
    body: RejectIn,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    return _decided(
        session, company_id, approvals.reject, actor, approval_id, body.reason
    )


@router.post(
    "/companies/{company_id}/sso/group-mappings/approvals/{approval_id}/withdraw"
)
async def withdraw_pair(
    company_id: int,
    approval_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    return _decided(session, company_id, approvals.withdraw, actor, approval_id)


@router.post(
    "/companies/{company_id}/sso/group-mappings/approvals/{approval_id}/confirm-single-owner"
)
async def confirm_pair_single_owner(
    company_id: int,
    approval_id: int,
    body: SingleOwnerIn,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    return _decided(
        session,
        company_id,
        approvals.confirm_single_owner,
        actor,
        approval_id,
        body.confirmation,
        body.reason,
    )

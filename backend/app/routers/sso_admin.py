"""
SSO settings router — a company's identity provider, managed by its Owners (issue #512).

Endpoints (a signed-in Owner only; API tokens are refused even with a role,
since who may sign in is a person's decision):
- ``GET    /v1/companies/{id}/sso``      — the connection (never the secret), the presets and the redirect URI.
- ``PUT    /v1/companies/{id}/sso``      — create or change it; an omitted secret keeps the stored one.
- ``POST   /v1/companies/{id}/sso/test`` — fetch discovery and the JWKS; report what was observed.
- ``DELETE /v1/companies/{id}/sso``      — remove it (password sign-in applies again).

Each allowed domain gets a DNS verification record (``sso_domains.sync``);
its check is ``routers/sso_domains.py``. A domain another company verified
cannot be listed.

Requiring SSO is refused until the connection is enabled and an Owner of the
company has signed in through it, so a company cannot lock itself out with a
connection that never worked. Every change writes an audit row naming the
fields changed, never a secret's value.
"""

import asyncio
from datetime import UTC, datetime

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import SsoConnection, SsoGroupMapping, User
from pydantic import Field
from schemas import RequestModel
from services.platform import sessions, sso_domains, sso_flow
from services.platform import sso_connections as conns
from services.platform.agent_service import AgentService
from sqlmodel import Session, delete, select

router = APIRouter(prefix="/v1", tags=["SSO"])

AUDITED_FIELDS = (
    "slug",
    "preset",
    "tenant",
    "issuer",
    "client_id",
    "allowed_domains",
    "default_role",
    "auto_join",
    "enabled",
    "require_sso",
)


class SsoConnectionIn(RequestModel):
    slug: str = Field(max_length=40)
    preset: str = Field(default="generic", max_length=16)
    tenant: str | None = Field(default=None, max_length=64)
    issuer: str | None = Field(default=None, max_length=2000)
    client_id: str = Field(min_length=1, max_length=256)
    client_secret: str | None = Field(default=None, max_length=2048)
    allowed_domains: list[str] = Field(default_factory=list, max_length=20)
    default_role: str = "Viewer"
    auto_join: bool = False
    enabled: bool = False
    require_sso: bool = False


def _owner(session: Session, user: User | None, company_id: int) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="SSO settings are changed by a person, not a token"
        )
    require_company_role(session, user, company_id, "Owner")
    return user


def _row(session: Session, company_id: int) -> SsoConnection | None:
    return session.exec(
        select(SsoConnection).where(SsoConnection.company_id == company_id)
    ).first()


def _audit(session: Session, company_id: int, actor: User, action: str, **result):
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"sso-settings:{company_id}",
        agent_id=f"human:{actor.email}",
        action=action,
        details={"company_id": company_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


@router.get("/companies/{company_id}/sso")
async def get_sso(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    _owner(session, user, company_id)
    row = _row(session, company_id)
    return {
        "connection": conns.serialize(row) if row else None,
        "presets": list(conns.PRESETS),
        "redirect_uri": conns.redirect_uri(),
        "owner_signed_in": bool(row) and sso_flow.owner_has_signed_in(session, row),
        "domains": [
            sso_domains.serialize(session, d)
            for d in sso_domains.rows(session, company_id)
        ],
    }


@router.put("/companies/{company_id}/sso")
async def put_sso(
    company_id: int,
    req: SsoConnectionIn,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    row = _row(session, company_id)
    created = row is None
    before = {f: getattr(row, f) for f in AUDITED_FIELDS} if row else {}
    try:
        values = {
            "slug": conns.clean_slug(req.slug),
            "preset": req.preset,
            "tenant": (req.tenant or "").strip().lower() or None,
            "issuer": conns.issuer_for(req.preset, req.issuer, req.tenant),
            "client_id": req.client_id.strip(),
            "allowed_domains": conns.clean_domains(req.allowed_domains),
            "default_role": conns.clean_role(req.default_role),
            "auto_join": req.auto_join,
            "enabled": req.enabled,
            "require_sso": req.require_sso,
        }
    except conns.SsoConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not values["client_id"]:
        raise HTTPException(status_code=422, detail="client_id: must not be empty")
    taken = session.exec(
        select(SsoConnection).where(SsoConnection.slug == values["slug"])
    ).first()
    if taken is not None and taken.company_id != company_id:
        raise HTTPException(status_code=409, detail="slug: already in use")
    # A domain another company verified by DNS is held by it. An unverified
    # listing holds nothing, so a squatter cannot keep the real company out
    # (docs/design/SSO.md, T19).
    for domain in values["allowed_domains"]:
        if sso_domains.verified_elsewhere(session, company_id, domain) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"allowed_domains: {domain} is verified by another company",
            )
    if row is None:
        row = SsoConnection(company_id=company_id, created_by=actor.email, **values)
    else:
        for field, value in values.items():
            setattr(row, field, value)
    if before.get("issuer") != row.issuer or before.get("client_id") != row.client_id:
        row.tested_at = row.test_ok = row.test_detail = None
    secret_changed = req.client_secret is not None
    conns.set_secret(row, req.client_secret)
    sso_domains.sync(session, company_id, values["allowed_domains"])
    session.add(row)
    session.flush()
    if row.require_sso and not (
        row.enabled and sso_flow.owner_has_signed_in(session, row)
    ):
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "require_sso: enable the connection and sign in through it as an "
                "Owner of this company first, so the company cannot be locked out"
            ),
        )
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(row)
    changed = [f for f in AUDITED_FIELDS if before.get(f) != getattr(row, f)]
    if secret_changed:
        changed.append("client_secret")
    # The requirement takes effect now: end the password-started sessions it
    # would refuse today (issue #524; docs/design/SESSIONS.md, S5).
    ended = 0
    if (row.require_sso and row.enabled) and not (
        before.get("require_sso") and before.get("enabled")
    ):
        ended = sessions.revoke_users(
            session,
            sso_flow.members_refused_password(session, company_id),
            "sso_required",
            method="password",
        )
    _audit(
        session,
        company_id,
        actor,
        "sso_connection_created" if created else "sso_connection_updated",
        changed=changed,
        sessions_ended=ended,
        **{f: getattr(row, f) for f in AUDITED_FIELDS},
    )
    session.refresh(row)
    return conns.serialize(row)


@router.post("/companies/{company_id}/sso/test")
async def test_sso(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """An observation of the provider: whether discovery and the JWKS answer now."""
    _owner(session, user, company_id)
    row = _row(session, company_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no SSO connection")
    result = await asyncio.to_thread(conns.test, row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**result, "connection": conns.serialize(row)}


@router.delete("/companies/{company_id}/sso", status_code=204)
async def delete_sso(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    row = _row(session, company_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no SSO connection")
    issuer = row.issuer
    # The group pairs go with it (their foreign key cascades on Postgres).
    session.exec(delete(SsoGroupMapping).where(SsoGroupMapping.connection_id == row.id))
    session.delete(row)
    sso_domains.drop_all(session, company_id)
    _audit(session, company_id, actor, "sso_connection_deleted", issuer=issuer)

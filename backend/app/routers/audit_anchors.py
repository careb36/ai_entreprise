"""Anchors of a company's audit chain outside the factory (issue #542).

``docs/design/AUDIT_ANCHORS.md``.

- ``GET /v1/audit/anchors`` (Viewer): which anchors cover which range of the
  chain, the tail none covers, and every anchor that no longer matches.
- ``POST /v1/audit/anchors`` (Manager): anchor the head now, with each kind
  the Owner configured.
- ``GET /v1/audit/anchors/settings`` (Manager) and ``PUT`` (Owner, a person,
  not a token; audited): cadence, webhook, timestamping authority, customer keys.
- ``GET /v1/audit/anchors/statement`` (Viewer): the head's statement, for the
  customer's own job to sign with its private key.
- ``POST /v1/audit/anchors/signature`` (Viewer): that signature, stored once
  it verifies with an active customer key (audited).
- ``GET /v1/audit/anchors/{id}/export`` (Viewer): the statement and its proof
  (token, certificate or signature), to verify outside the factory.
"""

from datetime import UTC, datetime

from auth_policy import (
    current_user_if_required,
    require_authenticated,
    require_company_role,
)
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from models import AuditAnchor, User
from pydantic import BaseModel
from services.integrations import anchor_settings, anchoring
from services.platform import audit_anchors
from services.platform.agent_service import AgentService
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Agents"])


class AnchorSettingsRequest(BaseModel):
    enabled: bool | None = None
    cadence_minutes: int | None = None
    publish_webhook: bool | None = None
    tsa_url: str | None = None
    tsa_cert_pem: str | None = None
    add_public_key: str | None = None
    retire_fingerprint: str | None = None


class CustomerSignatureRequest(BaseModel):
    seq: int
    chain_hash: str
    anchored_at: datetime
    signature: str


def _actor(user: User | None) -> str:
    if user is None:
        return "anonymous"
    # An API token's caller already reads ``api-token:<id>``.
    return user.email if user.email.startswith("api-token:") else f"human:{user.email}"


def _audit(session: Session, company_id: int, user: User | None, action: str, **result):
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"audit-anchors:{company_id}",
        agent_id=_actor(user),
        action=action,
        details={"company_id": company_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


@router.get("/audit/anchors")
async def get_anchor_coverage(
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_authenticated(user)
    require_company_role(session, user, company_id, "Viewer")
    return audit_anchors.coverage(session, company_id)


@router.post("/audit/anchors")
async def take_anchor(
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_authenticated(user)
    require_company_role(session, user, company_id, "Manager")
    created = anchoring.take(
        session, company_id, "on_demand", requested_by=_actor(user)
    )
    session.commit()
    return {
        "company_id": company_id,
        "queued": [{"id": a.id, "kind": a.kind, "seq": a.chain_seq} for a in created],
        "coverage": audit_anchors.coverage(session, company_id),
    }


@router.get("/audit/anchors/settings")
async def get_anchor_settings(
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_authenticated(user)
    require_company_role(session, user, company_id, "Manager")
    return anchor_settings.describe(session, company_id)


@router.put("/audit/anchors/settings")
async def put_anchor_settings(
    req: AnchorSettingsRequest,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    # The settings choose who the factory's record is checked by: an Owner, in person.
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="Anchor settings are changed by a person, not a token",
        )
    require_company_role(session, user, company_id, "Owner")
    changes = req.model_dump(exclude_unset=True)
    try:
        anchor_settings.save(session, company_id, changes, updated_by=_actor(user))
    except anchor_settings.InvalidAnchorSettings as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    described = anchor_settings.describe(session, company_id)
    _audit(
        session,
        company_id,
        user,
        "audit_anchor_settings_changed",
        changed=sorted(changes),
        enabled=described["enabled"],
        cadence_minutes=described["cadence_minutes"],
        publish_webhook=described["publish_webhook"],
        tsa_url=described["tsa_url"],
        tsa_cert_sha256=described["tsa_cert_sha256"],
        customer_keys=[
            {"fingerprint": k["fingerprint"], "retired_at": k["retired_at"]}
            for k in described["customer_keys"]
        ],
    )
    return described


@router.get("/audit/anchors/statement")
async def get_head_statement(
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_authenticated(user)
    require_company_role(session, user, company_id, "Viewer")
    at = audit_anchors.head(session, company_id)
    if at is None:
        raise HTTPException(status_code=404, detail="The company's chain is empty.")
    anchored_at = audit_anchors.as_utc(datetime.now(UTC))
    data = audit_anchors.statement(company_id, at[0], at[1], anchored_at)
    return {
        "company_id": company_id,
        "seq": at[0],
        "chain_hash": at[1],
        "anchored_at": anchored_at.isoformat(),
        "statement": data.decode("utf-8"),
    }


@router.post("/audit/anchors/signature")
async def post_customer_signature(
    req: CustomerSignatureRequest,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_authenticated(user)
    require_company_role(session, user, company_id, "Viewer")
    try:
        anchor = audit_anchors.record_customer_signature(
            session,
            company_id,
            seq=req.seq,
            chain_hash=req.chain_hash,
            anchored_at=req.anchored_at,
            signature=req.signature,
            requested_by=_actor(user),
        )
    except audit_anchors.InvalidAnchor as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.flush()
    anchor_id, seq, fingerprint = anchor.id, anchor.chain_seq, anchor.key_fingerprint
    _audit(
        session,
        company_id,
        user,
        "audit_anchor_customer_signature",
        anchor_id=anchor_id,
        seq=seq,
        key_fingerprint=fingerprint,
    )
    return {"id": anchor_id, "kind": "customer_key", "seq": seq, "status": "verified"}


@router.get("/audit/anchors/{anchor_id}/export")
async def export_anchor(
    anchor_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_authenticated(user)
    require_company_role(session, user, company_id, "Viewer")
    anchor = session.get(AuditAnchor, anchor_id)
    if anchor is None or anchor.company_id != company_id:
        raise HTTPException(status_code=404, detail="No such anchor.")
    receipt = anchor.receipt or {}
    public_key = None
    settings = audit_anchors.settings_for(session, company_id)
    for entry in (settings.customer_keys if settings else None) or []:
        if entry.get("fingerprint") == (
            anchor.key_fingerprint or receipt.get("verified_by")
        ):
            public_key = entry.get("public_key")
    return {
        "id": anchor.id,
        "kind": anchor.kind,
        "seq": anchor.chain_seq,
        "chain_hash": anchor.chain_hash,
        "status": anchor.status,
        "statement": audit_anchors.anchor_statement(anchor).decode("utf-8"),
        "token": receipt.get("token"),
        "tsa_cert_pem": receipt.get("tsa_cert_pem"),
        "signature": receipt.get("signature"),
        "public_key": public_key,
        "receipt_body": receipt.get("body"),
    }

"""The company's notification webhook (issue #357, part 4).

- ``GET /v1/companies/{id}/webhook`` (Viewer): URL, events, state; never the secret.
- ``PUT /v1/companies/{id}/webhook`` (Manager): create or replace; an omitted
  secret keeps the stored one.
- ``DELETE /v1/companies/{id}/webhook`` (Manager).
- ``POST /v1/companies/{id}/webhook/ping`` (Manager): queue a ``ping`` delivery.

How deliveries are queued, signed and sent: ``services/integrations/webhooks.py``.
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from schemas import RequestModel
from services.integrations import webhooks
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Enterprise"])


class WebhookRequest(RequestModel):
    url: str
    secret: str | None = None
    events: list[str] | None = None
    enabled: bool = True


@router.get("/companies/{company_id}/webhook")
async def get_webhook(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    return {
        "company_id": company_id,
        "events": list(webhooks.EVENTS),
        "webhook": webhooks.describe(webhooks.get(session, company_id)),
    }


@router.put("/companies/{company_id}/webhook")
async def put_webhook(
    company_id: int,
    req: WebhookRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Manager")
    try:
        hook = webhooks.save(
            session,
            company_id,
            url=req.url,
            secret=req.secret,
            events=req.events,
            enabled=req.enabled,
        )
    except webhooks.InvalidWebhook as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"company_id": company_id, "webhook": webhooks.describe(hook)}


@router.delete("/companies/{company_id}/webhook")
async def delete_webhook(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Manager")
    hook = webhooks.get(session, company_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="No webhook configured")
    session.delete(hook)
    session.commit()
    return {"company_id": company_id, "webhook": None}


@router.post("/companies/{company_id}/webhook/ping")
async def ping_webhook(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Manager")
    hook = webhooks.get(session, company_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="No webhook configured")
    webhooks.enqueue(session, hook, webhooks.PING, {})
    session.commit()
    return {"company_id": company_id, "queued": webhooks.PING}

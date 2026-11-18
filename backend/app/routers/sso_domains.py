"""
SSO domain verification router — proving a company holds its allowed domains (issue #512).

Endpoints (a signed-in Owner only; API tokens are refused, as for the rest of
the SSO settings):
- ``GET  /v1/companies/{id}/sso/domains``                 — each allowed domain:
  its status, the TXT record to publish (name and value) and the last check.
- ``POST /v1/companies/{id}/sso/domains/{domain}/check``  — look the record up
  now; the observation is recorded with the time and the strings seen.

Only the company's own listed domains are looked up, so the route cannot be
used to query arbitrary names. The check is rate-limited per client address
(``SSO_RATE_LIMIT``). Design and threat model: ``docs/design/SSO.md``.
"""

from auth_policy import current_user_if_required
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from models import User
from security import limiter
from services.platform import sso_domains
from sqlmodel import Session

from routers.sso_admin import _owner

router = APIRouter(prefix="/v1", tags=["SSO"])

MAX_DOMAIN_CHARS = 253


def _rate_limit() -> str:
    return settings.SSO_RATE_LIMIT


@router.get("/companies/{company_id}/sso/domains")
async def list_domains(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    _owner(session, user, company_id)
    return {
        "domains": [
            sso_domains.serialize(session, row)
            for row in sso_domains.rows(session, company_id)
        ]
    }


@router.post("/companies/{company_id}/sso/domains/{domain}/check")
@limiter.limit(_rate_limit)
def check_domain(
    request: Request,
    company_id: int,
    domain: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """An observation of DNS: whether the domain's TXT record holds the token now."""
    actor = _owner(session, user, company_id)
    name = domain.strip().lower()
    row = (
        sso_domains.find(session, company_id, name)
        if len(name) <= MAX_DOMAIN_CHARS
        else None
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail="not an allowed domain of this company"
        )
    # A plain ``def``: FastAPI runs it in its thread pool, so the lookup
    # (up to SSO_DNS_TIMEOUT_SECONDS) never blocks the event loop.
    result = sso_domains.check(session, row, trigger="owner", requested_by=actor.email)
    session.refresh(row)
    return {**result, "domain": sso_domains.serialize(session, row)}

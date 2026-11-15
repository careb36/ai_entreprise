"""
Chat approvals settings — a company's Slack app or Teams webhooks, and each person's link (issue #512).

Connections (a signed-in Owner only; API tokens are refused even with a role,
since where approvals are posted and which key admits a decision is a
person's choice):
- ``GET    /v1/companies/{id}/chat``                 — both connections (never a credential) and their inbound URLs.
- ``PUT    /v1/companies/{id}/chat/{provider}``      — create or change one; an omitted credential keeps the stored one.
- ``POST   /v1/companies/{id}/chat/{provider}/test`` — call the platform with the stored credential; record what was observed.
- ``DELETE /v1/companies/{id}/chat/{provider}``      — remove it and its posted messages.

Links (any signed-in member, for themselves only):
- ``GET    /v1/companies/{id}/chat/links``                 — the person's links and which providers are connected.
- ``POST   /v1/companies/{id}/chat/links/{provider}/code`` — a one-time code to type in chat.
- ``DELETE /v1/companies/{id}/chat/links/{provider}``      — unlink.

Every change writes an audit row naming the fields changed, never a value.
How a decision arrives from chat: ``routers/chat_interactions.py``; the design
and its threat model: ``docs/design/CHAT_APPROVALS.md``.
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import ChatApprovalMessage, User
from pydantic import BaseModel, Field
from services.integrations import chat_connections as conns
from services.integrations import chat_links
from services.platform.agent_service import AgentService
from sqlalchemy import delete
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Chat approvals"])

INSTRUCTIONS = {
    "slack": "In Slack, type: /fabrique link {code}",
    "teams": "In Teams, mention the La Fabrique webhook: link {code}",
}


class ChatConnectionIn(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    channel: str | None = Field(default=None, max_length=32)
    post_credential: str | None = Field(default=None, max_length=2048)
    signing_secret: str | None = Field(default=None, max_length=256)
    enabled: bool = False


def _person(user: User | None) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="Chat settings are changed by a person, not a token"
        )
    return user


def _owner(session: Session, user: User | None, company_id: int) -> User:
    person = _person(user)
    require_company_role(session, person, company_id, "Owner")
    return person


def _provider(provider: str) -> str:
    try:
        return conns.check_provider(provider)
    except conns.InvalidConnection as exc:
        raise HTTPException(status_code=404, detail="Unknown chat provider") from exc


def _audit(session: Session, company_id: int, actor: User, action: str, **result):
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"chat-settings:{company_id}",
        agent_id=f"human:{actor.email}",
        action=action,
        details={"company_id": company_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


@router.get("/companies/{company_id}/chat")
async def get_chat(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    _owner(session, user, company_id)
    return {
        "company_id": company_id,
        "providers": list(conns.PROVIDERS),
        "connections": {
            p: conns.describe(conns.get(session, company_id, p))
            for p in conns.PROVIDERS
        },
    }


@router.put("/companies/{company_id}/chat/{provider}")
async def put_chat(
    company_id: int,
    provider: str,
    req: ChatConnectionIn,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    provider = _provider(provider)
    try:
        row, changed = conns.save(
            session,
            company_id,
            provider,
            workspace_id=req.workspace_id,
            channel=req.channel,
            post_credential=req.post_credential,
            signing_secret=req.signing_secret,
            enabled=req.enabled,
            actor=actor.email,
        )
    except conns.InvalidConnection as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(
        session,
        company_id,
        actor,
        "chat_connection_saved",
        provider=provider,
        fields_changed=changed,
        enabled=row.enabled,
    )
    session.commit()
    return conns.describe(row)


@router.post("/companies/{company_id}/chat/{provider}/test")
async def test_chat(
    company_id: int,
    provider: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    provider = _provider(provider)
    row = conns.get(session, company_id, provider)
    if row is None:
        raise HTTPException(status_code=404, detail="No connection configured")
    result = await conns.test(session, row)
    _audit(
        session,
        company_id,
        actor,
        "chat_connection_tested",
        provider=provider,
        ok=result["ok"],
        detail=result["detail"],
    )
    session.commit()
    return {**result, "connection": conns.describe(row)}


@router.delete("/companies/{company_id}/chat/{provider}")
async def delete_chat(
    company_id: int,
    provider: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    provider = _provider(provider)
    row = conns.get(session, company_id, provider)
    if row is None:
        raise HTTPException(status_code=404, detail="No connection configured")
    session.exec(
        delete(ChatApprovalMessage).where(ChatApprovalMessage.connection_id == row.id)
    )
    session.delete(row)
    _audit(session, company_id, actor, "chat_connection_deleted", provider=provider)
    session.commit()
    return {"company_id": company_id, "provider": provider, "connection": None}


@router.get("/companies/{company_id}/chat/links")
async def get_links(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    require_company_role(session, person, company_id, "Viewer")
    return {
        "company_id": company_id,
        "connected": {
            p: bool((row := conns.get(session, company_id, p)) and row.enabled)
            for p in conns.PROVIDERS
        },
        "links": chat_links.links_of(session, company_id, person.id),
    }


@router.post("/companies/{company_id}/chat/links/{provider}/code")
async def link_code(
    company_id: int,
    provider: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    require_company_role(session, person, company_id, "Viewer")
    provider = _provider(provider)
    row = conns.get(session, company_id, provider)
    if row is None or not row.enabled:
        raise HTTPException(
            status_code=409, detail=f"The company has not connected {provider}"
        )
    issued = chat_links.issue_code(session, company_id, person.id, provider)
    return {
        **issued,
        "provider": provider,
        "instruction": INSTRUCTIONS[provider].format(code=issued["code"]),
    }


@router.delete("/companies/{company_id}/chat/links/{provider}")
async def unlink(
    company_id: int,
    provider: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    person = _person(user)
    require_company_role(session, person, company_id, "Viewer")
    provider = _provider(provider)
    removed = chat_links.unlink(session, company_id, person.id, provider)
    if removed:
        _audit(session, company_id, person, "chat_account_unlinked", provider=provider)
    session.commit()
    return {"company_id": company_id, "provider": provider, "unlinked": removed}

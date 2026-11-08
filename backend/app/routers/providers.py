"""
Providers router — where a company's agents' models come from (issue #356).

Endpoints:
- ``GET    /v1/companies/{id}/providers``              — the company's connections (Viewer; never the key).
- ``POST   /v1/companies/{id}/providers``              — connect a provider (Manager).
- ``PATCH  /v1/companies/{id}/providers/{pid}``        — change one; an omitted key keeps the stored one, an empty one clears it (Manager).
- ``DELETE /v1/companies/{id}/providers/{pid}``        — remove one (Manager).
- ``POST   /v1/companies/{id}/providers/{pid}/validate`` — list the provider's models with the key, and record the outcome (Manager).

Only a Manager changes what the company's agents can reach, as with the
agents themselves and the MCP servers. The key is encrypted at rest and is
never returned.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import ProviderConnection, User
from pydantic import BaseModel, Field
from services import model_catalog, model_suggestions
from services import provider_connections as connections
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Providers"])


class ProviderCreate(BaseModel):
    provider: str
    name: str | None = Field(default=None, max_length=64)
    base_url: str | None = None
    api_key: str | None = None
    headers: dict[str, Any] | None = None
    enabled: bool = True


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    base_url: str | None = None
    api_key: str | None = None
    headers: dict[str, Any] | None = None
    enabled: bool | None = None


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _load(
    session: Session, company_id: int, provider_id: int, user: User | None, minimum: str
) -> ProviderConnection:
    require_company_role(session, user, company_id, minimum)
    row = session.get(ProviderConnection, provider_id)
    if row is None or row.company_id != company_id:
        raise HTTPException(status_code=404, detail="provider connection not found")
    return row


def _name_taken(
    session: Session, company_id: int, name: str, own_id: int | None
) -> bool:
    other = session.exec(
        select(ProviderConnection).where(
            ProviderConnection.company_id == company_id,
            ProviderConnection.name == name,
        )
    ).first()
    return other is not None and other.id != own_id


def _clean_name(name: str | None, fallback: str) -> str:
    value = (name or "").strip() or fallback
    if not value:
        raise HTTPException(status_code=422, detail="name must not be empty")
    return value


@router.get("/companies/{company_id}/providers")
async def list_providers(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    rows = session.exec(
        select(ProviderConnection)
        .where(ProviderConnection.company_id == company_id)
        .order_by(ProviderConnection.name)
    ).all()
    return {
        "providers": [connections.serialize(row) for row in rows],
        "known_providers": [p["id"] for p in model_catalog.providers_summary()],
    }


@router.post("/companies/{company_id}/providers", status_code=201)
async def create_provider(
    company_id: int,
    req: ProviderCreate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Manager")
    try:
        provider = connections.normalize_provider(req.provider)
        base_url = connections.normalize_base_url(provider, req.base_url)
        name = _clean_name(req.name, provider)
        if _name_taken(session, company_id, name, None):
            raise HTTPException(
                status_code=409, detail=f"a connection named {name!r} exists"
            )
        row = ProviderConnection(
            company_id=company_id,
            provider=provider,
            name=name,
            base_url=base_url,
            enabled=req.enabled,
            created_by=_actor(user),
        )
        connections.set_secrets(row, req.api_key, req.headers)
    except connections.ProviderConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.add(row)
    session.commit()
    session.refresh(row)
    return connections.serialize(row)


@router.patch("/companies/{company_id}/providers/{provider_id}")
async def patch_provider(
    company_id: int,
    provider_id: int,
    req: ProviderPatch,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    row = _load(session, company_id, provider_id, user, "Manager")
    try:
        if req.name is not None:
            name = _clean_name(req.name, "")
            if _name_taken(session, company_id, name, row.id):
                raise HTTPException(
                    status_code=409, detail=f"a connection named {name!r} exists"
                )
            row.name = name
        if req.base_url is not None:
            row.base_url = connections.normalize_base_url(row.provider, req.base_url)
        connections.set_secrets(row, req.api_key, req.headers)
    except connections.ProviderConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.enabled is not None:
        row.enabled = req.enabled
    if req.base_url is not None or req.api_key is not None or req.headers is not None:
        # What was checked is no longer what is stored.
        row.validated_at = row.validation_ok = row.validation_detail = None
    row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return connections.serialize(row)


@router.delete("/companies/{company_id}/providers/{provider_id}", status_code=204)
async def delete_provider(
    company_id: int,
    provider_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    row = _load(session, company_id, provider_id, user, "Manager")
    session.delete(row)
    session.commit()


@router.post("/companies/{company_id}/providers/{provider_id}/validate")
async def validate_provider(
    company_id: int,
    provider_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """An observation of the provider: whether it accepts the key now."""
    row = _load(session, company_id, provider_id, user, "Manager")
    result = await asyncio.to_thread(connections.validate, row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**result, "provider": connections.serialize(row)}


@router.get("/models/catalog")
async def model_catalog_providers():
    """The providers the catalogue knows (models.dev; public, no company data)."""
    return {
        "origin": model_catalog.origin(),
        "providers": model_catalog.providers_summary(),
    }


@router.get("/models/catalog/{provider_id}")
async def model_catalog_models(provider_id: str):
    """One provider's models and what each can do (public)."""
    entry = model_catalog.provider(provider_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="provider not in the catalogue")
    return {
        "provider": provider_id,
        "name": entry["name"],
        "base_url": entry["base_url"],
        "models": model_catalog.models_for(provider_id),
    }


@router.get("/companies/{company_id}/models")
async def company_models(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The models the company can use: per enabled connection, the
    catalogue's models for its provider merged with what the provider itself
    listed at its last validation. ``listed`` says the provider served the id
    when asked; ``in_catalog`` says the catalogue describes it."""
    require_company_role(session, user, company_id, "Viewer")
    rows = session.exec(
        select(ProviderConnection)
        .where(
            ProviderConnection.company_id == company_id,
            ProviderConnection.enabled.is_(True),
        )
        .order_by(ProviderConnection.name)
    ).all()
    return {
        "origin": model_catalog.origin(),
        "connections": [model_catalog.connection_models(row) for row in rows],
    }


@router.get("/companies/{company_id}/model_suggestions")
async def company_model_suggestions(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Suggested model per role, based on the company's enabled connections."""
    require_company_role(session, user, company_id, "Viewer")
    rows = session.exec(
        select(ProviderConnection)
        .where(
            ProviderConnection.company_id == company_id,
            ProviderConnection.enabled.is_(True),
        )
        .order_by(ProviderConnection.name)
    ).all()
    models: list[dict[str, Any]] = []
    for row in rows:
        for m in model_catalog.connection_models(row).get("models", []):
            models.append(dict(m))
    return {"suggestions": model_suggestions.suggest(models)}

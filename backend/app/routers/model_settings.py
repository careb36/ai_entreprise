"""
Model settings router — the models a company's agents run (issue #356, step 4).

Endpoints:
- ``GET /v1/companies/{id}/model_settings`` — the company's default model, its
  model per role, and what each role runs with them (Viewer).
- ``PUT /v1/companies/{id}/model_settings`` — replace them; an empty value
  clears one (Manager).
- ``GET /v1/companies/{id}/model_suggestions`` — the model the rule suggests
  for each role among the company's enabled connections (Viewer); never
  applied on its own (``services/model_suggestions.py``).
- ``GET /v1/companies/{id}/routing_record`` — done, rework and reported tokens
  per difficulty and model over the completed sprints (Viewer;
  ``services/routing_record.py``, issue #319).

An agent's own model still wins (the Agents page); without one, the company's
model for its role, then the company's default, then the deployment's
``AGENT_MODEL`` settings (``services/company_models.py``).
"""

import logging
from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Company, ProviderConnection, User
from pydantic import BaseModel
from services.models import (
    company_models,
    model_record,
    model_suggestions,
    routing_record,
)
from sqlmodel import Session, select

logger = logging.getLogger("fabrique")
router = APIRouter(prefix="/v1", tags=["Providers"])


class ModelSettings(BaseModel):
    default_model: str | None = None
    role_models: dict[str, Any] | None = None


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/companies/{company_id}/model_settings")
def get_model_settings(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
) -> dict[str, Any]:
    require_company_role(session, user, company_id, "Viewer")
    return company_models.serialize(_company(session, company_id))


@router.put("/companies/{company_id}/model_settings")
def put_model_settings(
    company_id: int,
    body: ModelSettings,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
) -> dict[str, Any]:
    require_company_role(session, user, company_id, "Manager")
    company = _company(session, company_id)
    try:
        company_models.apply(session, company, body.default_model, body.role_models)
    except company_models.InvalidModelSettings as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.add(company)
    session.commit()
    session.refresh(company)
    logger.info(
        "Company %s model settings changed by %s",
        company_id,
        getattr(user, "email", None) or "anonymous",
    )
    return company_models.serialize(company)


@router.get("/companies/{company_id}/model_suggestions")
def get_model_suggestions(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
) -> dict[str, Any]:
    require_company_role(session, user, company_id, "Viewer")
    _company(session, company_id)
    rows = session.exec(
        select(ProviderConnection)
        .where(
            ProviderConnection.company_id == company_id,
            ProviderConnection.enabled.is_(True),
        )
        .order_by(ProviderConnection.name)
    ).all()
    models = model_suggestions.candidates(list(rows))
    return {
        "suggestions": model_suggestions.suggest(
            models, model_record.record(session, company_id)
        ),
        "candidates": len(models),
    }


@router.get("/companies/{company_id}/routing_record")
def get_routing_record(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
) -> dict[str, Any]:
    require_company_role(session, user, company_id, "Viewer")
    _company(session, company_id)
    return routing_record.record(session, company_id)

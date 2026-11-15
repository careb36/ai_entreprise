"""Enterprise agent management: a company's agent configurations.

Endpoints (split out of ``routers/enterprise.py``, which re-exports them):
- ``GET    /v1/agents``                 — a company's agents (Viewer).
- ``POST   /v1/agents``                 — create an agent configuration (Manager).
- ``POST   /v1/agents/generate_prompt`` — a template system prompt for an agent.
- ``PATCH  /v1/agents/{agent_id}``      — update an agent (Manager of its company).
- ``DELETE /v1/agents/{agent_id}``      — delete an agent (Manager of its company).
"""

from auth_policy import (
    current_user_if_required,
    require_company_role,
)
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Agent, User
from schemas import (
    AgentCreateRequest,
    AgentPromptRequest,
    AgentUpdateRequest,
)
from services.loop.company_service import CompanyService
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Enterprise"])


_ROLE_RESPONSIBILITIES: dict[str, str] = {
    "PO": "Define and prioritize the product backlog.",
    "Architect": "Design the overall system architecture.",
    "ScrumMaster": "Facilitate sprint ceremonies and remove impediments.",
    "Dev": "Implement features according to acceptance criteria.",
    "BackendDev": "Design and implement server-side logic, APIs, and databases.",
    "FrontendDev": "Build responsive, accessible user interfaces.",
    "FullstackDev": "Deliver features end-to-end across frontend and backend.",
    "QA": "Design and execute test strategies.",
    "DevOps": "Own CI/CD pipelines and infrastructure.",
}


def _build_template_prompt(
    role: str, seniority: str, specialty: str, persona: str
) -> str:
    responsibilities = _ROLE_RESPONSIBILITIES.get(
        role, f"Deliver high-quality work for the {role} discipline."
    )
    return (
        f"You are a {seniority} {role}. {responsibilities} "
        f"Communication style: {persona or 'professional, precise, collaborative'}."
    ).strip()


def _agent_company(session: Session, agent_id: int) -> int:
    """The company an agent belongs to; 404 when there is no such agent."""
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return agent.company_id


@router.get("/agents", tags=["Enterprise"])
async def list_agents(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """List all agents for a company (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    service = CompanyService(session)
    try:
        return {"agents": service.list_agents(company_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/agents", tags=["Enterprise"])
async def create_agent(
    req: AgentCreateRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """Create a new agent configuration (Manager)."""
    require_company_role(session, user, req.company_id, "Manager")
    service = CompanyService(session)
    try:
        return service.create_agent(
            req.company_id, req.model_dump(exclude={"company_id"})
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/agents/generate_prompt", tags=["Enterprise"])
async def generate_agent_prompt(req: AgentPromptRequest):
    """Auto-generate a system prompt for an agent."""
    return {
        "system_prompt": _build_template_prompt(
            req.role, req.seniority, req.specialty, req.persona
        ),
        "source": "template",
    }


@router.patch("/agents/{agent_id}", tags=["Enterprise"])
async def update_agent(
    agent_id: int,
    req: AgentUpdateRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """Update an existing agent configuration (Manager of its company).

    An agent's prompt, model and budget decide what it does for the company,
    so only its Managers change them.
    """
    require_company_role(session, user, _agent_company(session, agent_id), "Manager")
    service = CompanyService(session)
    try:
        return service.update_agent(agent_id, req.model_dump(exclude_unset=True))
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail=str(exc))


@router.delete("/agents/{agent_id}", tags=["Enterprise"])
async def delete_agent(
    agent_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """Delete an existing agent configuration (Manager of its company)."""
    require_company_role(session, user, _agent_company(session, agent_id), "Manager")
    service = CompanyService(session)
    try:
        return service.delete_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

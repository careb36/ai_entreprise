"""Whether a company hosts the factory, so its protected paths hold
(``docs/design/rsi-loop.md``, sections 3.2 and 10, decision 3).

``companies.protected_paths_enabled`` is false for every company until an
Owner turns it on. With it on, the Verifier matches the paths the platform
observed a delivery change against ``services/contracts/protected_paths.py``
(the running backend's copy, never the company's project) and a match holds
the task for a person. The flag is never inferred from the project: the
factory cannot avoid being recognised.

Only a signed-in human Owner changes it (the router refuses an API token),
the Manager agent never does (``change_protected_paths`` is in
``ALWAYS_ESCALATE``), and every change is audited as
``protected_paths_setting_changed`` with the account and both values.
"""

from __future__ import annotations

from typing import Any

from models.company import Company
from sqlmodel import Session

from services.platform.agent_service import AgentService

AUDIT_ACTION = "protected_paths_setting_changed"


class SettingRefused(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def enabled(session: Session, company_id: int | None) -> bool:
    """The company's flag; an unknown company is not one that hosts the factory."""
    if company_id is None:
        return False
    company = session.get(Company, company_id)
    return bool(company and company.protected_paths_enabled)


def set_enabled(
    session: Session, company_id: int, value: bool, *, by: str, user_id: int | None
) -> dict[str, Any]:
    """Turn the protected paths on or off for the company; a change is audited."""
    company = session.get(Company, company_id)
    if company is None:
        raise SettingRefused(404, "Company not found")
    before = bool(company.protected_paths_enabled)
    if before != value:
        company.protected_paths_enabled = value
        session.add(company)
        AgentService().audit_interaction(
            session=session,
            company_id=company_id,
            interaction_id=f"company:{company_id}:protected_paths",
            agent_id=f"human:{by}",
            action=AUDIT_ACTION,
            details={"company_id": str(company_id), "user_id": user_id},
            result={"from": before, "to": value},
            decision_signature=None,
            cost_tokens=0,
        )
        session.commit()
    return {"company_id": company_id, "enabled": value}

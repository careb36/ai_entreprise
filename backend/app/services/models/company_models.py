"""A company's choice of models: one default and one per role (issue #356, step 4).

Which model an agent runs, most specific first:

1. the agent's own ``model`` (the Agents page);
2. the company's model for the role (``Company.role_models``);
3. the company's default model (``Company.default_model``);
4. the deployment's: ``AGENT_MODEL_<ROLE>``, ``NEMOTRON_ROLES``,
   ``AGENT_MODEL``, then the code's role map (``default_model_for_role``).

A Manager writes the company's choice (``routers/model_settings.py``); a
Viewer reads it. The difficulty routing of ``model_routing`` sits between 3
and 4: it refines the deployment's default, never a model the company chose.
A model may be a plain id or one of the company's own provider connections
(``conn:<id>:<model>``), which must exist in the company.
"""

from __future__ import annotations

from typing import Any

from models import Company, ProviderConnection
from sqlmodel import Session

# The roles a company can give a model to. The Reviewer agent judges other
# producers' runs and gets its own entry.
ROLES: tuple[str, ...] = (
    "PO",
    "Architect",
    "ScrumMaster",
    "Developer",
    "QA",
    "SecOps",
    "TechWriter",
    "BackendDeveloper",
    "FrontendDeveloper",
    "DevOps",
    "DataEngineer",
    "Designer",
    "Reviewer",
)
# Without an entry of its own, the Reviewer follows QA and the specialised
# coding roles follow the Developer (services/roles.py), as they always did.
_FALLBACK = {
    "Reviewer": "QA",
    "BackendDeveloper": "Developer",
    "FrontendDeveloper": "Developer",
    "DevOps": "Developer",
    "DataEngineer": "Developer",
}
MAX_MODEL_CHARS = 200


class InvalidModelSettings(ValueError):
    """A choice the company cannot make; the message says why."""


def role_key(role: str | None) -> str:
    """The ``ROLES`` entry a stored role name stands for (aliases included)."""
    from services.models.model_factory import _normalise_role, role_for_name

    if _normalise_role(role or "") == "reviewer":
        return "Reviewer"
    return role_for_name(role or "").value


def company_choice(company: Company | None, role: str | None) -> str | None:
    """The model the company chose for *role*, else its default, else ``None``."""
    if company is None:
        return None
    roles = company.role_models if isinstance(company.role_models, dict) else {}
    key = role_key(role)
    for candidate in (key, _FALLBACK.get(key)):
        value = str(roles.get(candidate) or "").strip() if candidate else ""
        if value:
            return value
    return (company.default_model or "").strip() or None


def model_for(session: Session, company_id: int | None, role: str | None) -> str | None:
    """``company_choice`` for the company with *company_id*; ``None`` without one."""
    if company_id is None:
        return None
    return company_choice(session.get(Company, company_id), role)


def resolve(
    session: Session,
    company_id: int | None,
    role: str,
    configured: str | None = None,
) -> str:
    """The model an agent of *role* runs: its own, the company's, the deployment's."""
    from services.models.model_factory import default_model_for_role

    return (
        (configured or "").strip()
        or model_for(session, company_id, role)
        or default_model_for_role(role)
    )


def clean_model(
    session: Session, company_id: int, value: Any, field: str
) -> str | None:
    """*value* as a model id the company may use; ``None`` when empty."""
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > MAX_MODEL_CHARS or any(ch in text for ch in "\r\n\t"):
        raise InvalidModelSettings(f"{field}: not a model id")
    from services.models import provider_connections

    try:
        ref = provider_connections.parse_model_ref(text)
    except provider_connections.ProviderConfigError as exc:
        raise InvalidModelSettings(
            f"{field}: a connection model is conn:<id>:<model>"
        ) from exc
    if ref is not None:
        conn = session.get(ProviderConnection, ref[0])
        if conn is None or conn.company_id != company_id:
            raise InvalidModelSettings(
                f"{field}: connection {ref[0]} is not one of the company's"
            )
    return text


def apply(
    session: Session,
    company: Company,
    default_model: Any = None,
    role_models: dict[str, Any] | None = None,
) -> None:
    """Replace the company's choice; an empty value clears it. Raises on a bad one."""
    roles: dict[str, str] = {}
    for name, value in (role_models or {}).items():
        if name not in ROLES:
            raise InvalidModelSettings(
                f"unknown role {name!r}; one of {', '.join(ROLES)}"
            )
        model = clean_model(session, company.id, value, name)
        if model:
            roles[name] = model
    company.default_model = clean_model(session, company.id, default_model, "default")
    company.role_models = roles or None


def serialize(company: Company) -> dict[str, Any]:
    """The choice as stored, and what each role runs with it (no agent override)."""
    from services.models.model_factory import default_model_for_role

    roles = company.role_models if isinstance(company.role_models, dict) else {}
    effective = {}
    for role in ROLES:
        chosen = company_choice(company, role)
        effective[role] = {
            "model": chosen or default_model_for_role(role),
            "source": (
                "role"
                if str(
                    roles.get(role) or roles.get(_FALLBACK.get(role, "")) or ""
                ).strip()
                else "company"
                if chosen
                else "deployment"
            ),
        }
    return {
        "default_model": company.default_model or None,
        "role_models": dict(roles),
        "roles": list(ROLES),
        "effective": effective,
    }

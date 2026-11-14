"""Where agent-written code runs: the sandbox the backend asks the daemon for.

The delivery test run (``delivery_checks.run_delivery_tests``) and the
Developer's own test runs (``developer_tools``) execute code a model wrote.
Both send ``request_for(company)`` with their ``workspace.execute`` request;
the daemon (``vscode-daemon/sandbox.py``) runs the command that way and
replies with what it applied, which ``record_test_run`` keeps on the run's
evidence and ``check_sandbox`` reports. The applied sandbox is an observation
of the daemon; the backend's request is not evidence of anything.

``host`` is a finding a Manager sees, never a refutation: the tests still ran
and their exit status still decides.
"""

from __future__ import annotations

from typing import Any

from config import settings

from services.contracts.sandbox_report import check_sandbox  # noqa: F401 - re-exported

MODES = ("host", "container", "bwrap")


class PolicyUnavailable(RuntimeError):
    """The company's sandbox could not be read. Agent code is then not run at
    all: the deployment's default (often ``host``) must never stand in for a
    company that asked for isolation."""


def mode_for(company: Any) -> str:
    """The company's mode, else the deployment's; ``host`` when unknown."""
    own = str(getattr(company, "delivery_sandbox", None) or "").strip().lower()
    if own in MODES:
        return own
    default = str(settings.DELIVERY_SANDBOX or "host").strip().lower()
    return default if default in MODES else "host"


def request_for(company: Any) -> dict[str, Any]:
    """The ``sandbox`` field of a ``workspace.execute`` request for agent code."""
    names = getattr(company, "sandbox_env", None) or []
    return {
        "mode": mode_for(company),
        "image": settings.DELIVERY_SANDBOX_IMAGE,
        "network": settings.DELIVERY_SANDBOX_NETWORK,
        "cpus": settings.DELIVERY_SANDBOX_CPUS,
        "memory_mb": settings.DELIVERY_SANDBOX_MEMORY_MB,
        "pids_limit": settings.DELIVERY_SANDBOX_PIDS,
        "env": [str(n) for n in names if isinstance(n, str) and n.strip()],
        # Container mode: the project's dependency cache volume (the daemon's
        # ``sandbox_cache.py``); its report comes back as ``sandbox.cache``.
        "cache": bool(settings.DELIVERY_SANDBOX_CACHE),
    }


def request_for_company_id(
    company_id: int | None, engine: Any = None
) -> dict[str, Any]:
    """``request_for`` with the company read from *engine* (the deployment's
    default when there is no company). Raises ``PolicyUnavailable`` when the
    company cannot be read."""
    company = None
    if company_id is not None:
        try:
            from database import engine as default_engine
            from models.company import Company
            from sqlmodel import Session

            with Session(engine or default_engine) as session:
                company = session.get(Company, company_id)
        except Exception as e:  # Fail closed, with the reason.
            raise PolicyUnavailable(
                f"the company's sandbox could not be read ({type(e).__name__})"
            ) from e
    return request_for(company)


def request_for_sprint(
    sprint: dict[str, Any] | None, engine: Any = None
) -> dict[str, Any]:
    """``request_for`` for the company of *sprint* (its ``company_id``, else
    the sprint row's). Raises ``PolicyUnavailable`` when it cannot be read."""
    sprint = sprint or {}
    company_id = sprint.get("company_id")
    if company_id is None and sprint.get("id") is not None:
        try:
            from database import engine as default_engine
            from models.sprint import Sprint
            from sqlmodel import Session

            with Session(engine or default_engine) as session:
                row = session.get(Sprint, sprint["id"])
                company_id = row.company_id if row is not None else None
        except Exception as e:  # Fail closed, with the reason.
            raise PolicyUnavailable(
                f"the sprint's company could not be read ({type(e).__name__})"
            ) from e
    return request_for_company_id(company_id, engine)


CACHE_RESET_ACTION = "sandbox_cache_reset"
# The daemon's volume inspect and removal, each bounded at 60 s there.
CACHE_RESET_TIMEOUT_SECONDS = 150.0


class CacheResetRefused(RuntimeError):
    """The reset could not be asked for; ``status`` is the HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def project_path(session: Any, company_id: int) -> str | None:
    """The company's project folder as recorded at discovery, latest first."""
    from models.company import ProjectDiscovery
    from sqlmodel import select

    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == company_id)
        .order_by(ProjectDiscovery.created_at.desc(), ProjectDiscovery.id.desc())
    ).first()
    return discovery.project_path if discovery and discovery.project_path else None


async def reset_cache(
    nats_client: Any, session: Any, company_id: int, actor: str
) -> dict[str, Any]:
    """A person's reset of the project's dependency cache volume, through the
    daemon (``workspace.sandbox.reset``): the daemon removes only its own
    volume (name and label) and, once it is gone, clears the failed installs
    and the quarantine. Its answer is audited and returned as it came."""
    from services.platform.agent_service import AgentService
    from services.workspace.workspace_daemon import DaemonError, daemon_request

    path = project_path(session, company_id)
    if not path:
        raise CacheResetRefused(409, "The company has no project folder")
    try:
        reply = await daemon_request(
            nats_client,
            "workspace.sandbox.reset",
            {"workspace_root": path},
            CACHE_RESET_TIMEOUT_SECONDS,
        )
    except DaemonError as e:
        raise CacheResetRefused(e.status, e.message) from e
    volume = reply.get("volume_reset")
    if not isinstance(volume, dict):
        raise CacheResetRefused(502, "The workspace daemon sent an unreadable answer")
    result = {
        "volume": reply.get("volume"),
        "volume_reset": volume,
        "quarantine_cleared": bool(reply.get("quarantine_cleared")),
        "quarantined": reply.get("quarantined"),
        "failed_installs_before": reply.get("failed_installs_before"),
    }
    audit = AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}:sandbox-cache",
        agent_id=f"human:{actor}",
        action=CACHE_RESET_ACTION,
        details={"company_id": str(company_id), "path": path},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )
    session.commit()
    return {"company_id": company_id, "path": path, **result, "audit_log_id": audit.id}


def valid_env_name(name: str) -> bool:
    """An environment variable name: letters, digits and underscores."""
    return bool(name) and name.replace("_", "").isalnum() and not name[0].isdigit()

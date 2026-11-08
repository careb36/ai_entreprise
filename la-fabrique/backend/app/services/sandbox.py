"""Where agent-written code runs: the sandbox the backend asks the daemon for.

The delivery test run (``workspace_daemon.run_delivery_tests``) and the
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


def valid_env_name(name: str) -> bool:
    """An environment variable name: letters, digits and underscores."""
    return bool(name) and name.replace("_", "").isalnum() and not name[0].isdigit()


def check_sandbox(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's ``sandbox`` check: where the tests ran, as the daemon
    reported it. A record, never the verdict; absent without a test run."""
    run = (evidence or {}).get("test_run") or {}
    if not run.get("command"):
        return None
    applied = run.get("sandbox")
    if (
        run.get("exit_code") is None
        and not run.get("timed_out")
        and not isinstance(applied, dict)
    ):
        return None  # Nothing ran: tests_run says why, there is nowhere to report.
    if not isinstance(applied, dict):
        return {
            "name": "sandbox",
            "outcome": "pass",
            "isolated": None,
            "finding": True,
            "observation": (
                "the daemon reported no sandbox, so where the tests ran and "
                "whether they were isolated is unknown"
            ),
        }
    mode = applied.get("mode")
    forced = " (the daemon's own floor)" if applied.get("forced_by_daemon") else ""
    if mode == "host" or not applied.get("isolated"):
        return {
            "name": "sandbox",
            "outcome": "pass",
            "isolated": False,
            "finding": True,
            "sandbox": applied,
            "observation": (
                "ran on the host, unisolated: the agent-written code had the "
                "daemon's user, files, network and environment"
            ),
        }
    parts = [f"ran in {mode}{forced}"]
    if mode == "container":
        parts.append(
            f"{applied.get('runtime') or 'container'} image {applied.get('image')}"
        )
    parts.append(f"network {applied.get('network')}")
    if applied.get("cpus"):
        parts.append(f"{applied['cpus']:g} CPUs")
    if applied.get("memory_mb"):
        parts.append(f"{applied['memory_mb']} MB")
    if applied.get("pids_limit"):
        parts.append(f"{applied['pids_limit']} processes")
    if applied.get("capabilities"):
        parts.append(f"capabilities {applied['capabilities']}")
    if applied.get("limits"):
        parts.append(str(applied["limits"]))
    env = applied.get("env") or []
    parts.append(f"environment: {', '.join(env)}" if env else "no environment passed")
    return {
        "name": "sandbox",
        "outcome": "pass",
        "isolated": True,
        "finding": False,
        "sandbox": applied,
        "observation": "; ".join(parts),
    }

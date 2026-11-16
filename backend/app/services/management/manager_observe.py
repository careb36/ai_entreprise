"""What the Manager agent asks the platform to observe right before acting
(issue #394), and how it reaches the workspace daemon from its thread.

Two rules read a fact nothing stored: ``resume_after_credit`` needs a short
call to the provider through the proxy that does not come back refused for
lack of credit, and ``publish`` needs the repository to have a remote, which
the daemon reads (``workspace.inspect``). Both are probed only when the
company's policy names the rule and the agent's suggestion is that rule's
decision, so a company that does not use them pays nothing. A probe that
could not run (the daemon unreachable, a credit call made too recently)
leaves the fact unknown, and the agent waits instead of deciding.

The agent's loop runs in a worker thread; ``bind`` gives this module the
backend's event loop and a getter for its NATS client, so daemon requests
(``workspace.git.merge``, ``workspace.undo``, ``workspace.git.publish``) are
made on that loop, as the routes make them. Tests replace the requester.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

from logging_config import logger
from models import Agent, BacklogItem, Company
from models.manager import ManagerDecision
from sqlmodel import Session, select

from services.loop.sprint_control import credit_failure
from services.models import company_models
from services.workspace.workspace_daemon import DaemonUnavailable, daemon_request

# One credit call per paused sprint at most this often (seconds).
CREDIT_PROBE_INTERVAL = 900.0
INSPECT_TIMEOUT_SECONDS = 20.0
CREDIT_PROBE_TIMEOUT_SECONDS = 30.0
_bridge: dict[str, Any] = {}
_last_credit_probe: dict[int, float] = {}
# A fact a probe leaves unknown, and when its value means "wait, look again".
WAIT_FACTS: dict[str, tuple[str, Callable[[Any], bool]]] = {
    "resume_after_credit": ("balance_ok", lambda value: value is not True),
    "publish": ("has_remote", lambda value: value is None),
}


def bind(nats_client: Callable[[], Any], loop: asyncio.AbstractEventLoop) -> None:
    """Give the module the backend's NATS client getter and event loop."""
    _bridge.update({"nats": nats_client, "loop": loop})


def set_requester(requester: Callable[[str, dict, float], dict] | None) -> None:
    """Replace the daemon requester (tests); ``None`` restores the default."""
    if requester is None:
        _bridge.pop("request", None)
    else:
        _bridge["request"] = requester


def request(subject: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """A daemon request made from the agent's thread; raises
    ``DaemonUnavailable`` or ``DaemonError`` as the routes' requests do."""
    custom = _bridge.get("request")
    if custom is not None:
        return custom(subject, payload, timeout)
    get_nats, loop = _bridge.get("nats"), _bridge.get("loop")
    nats_client = get_nats() if callable(get_nats) else None
    if nats_client is None or loop is None:
        raise DaemonUnavailable("NATS is not connected to the Manager agent")
    future = asyncio.run_coroutine_threadsafe(
        daemon_request(nats_client, subject, payload, timeout), loop
    )
    return future.result(timeout + 5)


def has_remote(repository: str | None) -> bool | None:
    """Whether the repository has a remote (``origin``), as the daemon reads
    it; ``None`` when no daemon answered."""
    if not repository:
        return False
    try:
        reply = request(
            "workspace.inspect", {"path": repository}, INSPECT_TIMEOUT_SECONDS
        )
    except DaemonUnavailable:
        return None
    except Exception as exc:  # noqa: BLE001 - A refusal is no remote observed.
        logger.info("Manager agent could not inspect %s: %s", repository, exc)
        return False
    git = reply.get("git") if isinstance(reply, dict) else None
    return bool(isinstance(git, dict) and git.get("remote_url"))


def probe_call(model: str, role: str, company_id: int, bu_id: int | None) -> dict:
    """The smallest call through the proxy: one token, billed to the company."""
    from llm_proxy_client import LLMProxyClient

    client = LLMProxyClient(
        base_url=os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100")
    )
    client.max_tokens, client.company_id = 1, company_id
    client.timeout = CREDIT_PROBE_TIMEOUT_SECONDS
    return client.chat(
        messages=[{"role": "user", "content": "Reply with OK."}],
        model=model,
        role=role,
        bu_id=bu_id,
    )


def _paused_role(session: Session, company_id: int, sprint_id: int) -> tuple[str, str]:
    """The role and model of a task the pause sent back to ``todo``."""
    item = session.exec(
        select(BacklogItem)
        .where(BacklogItem.sprint_id == sprint_id, BacklogItem.status == "todo")
        .order_by(BacklogItem.id)
    ).first()
    role = (item.assignee if item else None) or "Developer"
    agent = session.exec(
        select(Agent).where(Agent.company_id == company_id, Agent.role == role)
    ).first()
    configured = agent.model if agent is not None and agent.model else None
    return role, company_models.resolve(session, company_id, role, configured)


def credit_ok(
    session: Session, company: Company, sprint_id: int, now: float | None = None
) -> bool | None:
    """Whether a one-token call for the paused sprint's work passes; ``None``
    when one was made less than ``CREDIT_PROBE_INTERVAL`` ago."""
    from services.models.company_models import business_unit_id

    moment = time.monotonic() if now is None else now
    last = _last_credit_probe.get(sprint_id)
    if last is not None and moment - last < CREDIT_PROBE_INTERVAL:
        return None
    _last_credit_probe[sprint_id] = moment
    role, model = _paused_role(session, company.id, sprint_id)
    try:
        result = probe_call(
            model, role, company.id, business_unit_id(session, company.id)
        )
    except Exception as exc:  # noqa: BLE001 - No answer: the credit is not shown.
        logger.info("Credit check for sprint %s failed: %s", sprint_id, exc)
        return False
    choices = (result or {}).get("choices")
    return bool(choices) and not result.get("error") and not credit_failure(result)


def _repository(session: Session, row: ManagerDecision) -> str | None:
    from models import AgentRun

    from services.management.manager_inbox_extra import _git

    run = session.get(AgentRun, row.item_id)
    return str(_git(session, run).get("repository") or "") if run else None


def observe(
    session: Session,
    company: Company,
    row: ManagerDecision,
    facts: dict[str, Any],
    named: bool,
) -> dict[str, Any]:
    """*facts* with what the rule of *row*'s category still needs observed.

    Probes only when the policy names the rule (*named*), the suggestion is
    its decision, and the company is within its budget.
    """
    if not named or facts.get("over_budget") or facts.get("overridden_before"):
        return facts
    if row.category == "resume_after_credit" and row.suggested_decision == "resume":
        return {**facts, "balance_ok": credit_ok(session, company, row.item_id)}
    if (
        row.category == "publish"
        and row.suggested_decision == "publish"
        and facts.get("publishing_on")
    ):
        return {**facts, "has_remote": has_remote(_repository(session, row))}
    return facts

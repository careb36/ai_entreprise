"""A sprint's integration opened as a draft pull request on its own.

When a closed sprint's integration passes its suite, and only then, the
branch goes out the way a Manager publishes it
(``integration_publish.publish_spec``, the daemon's ``workspace.git.publish``):
pushed with the credentials of the person whose computer runs the daemon, the
request opened as a draft. Nothing changes about what may leave the computer:
the company's forge policy must be ``publish`` (``require_forge``), the base
must be current, and a person still merges, since whoever started the sprint
does not approve its work (``sod``).

The description is the platform's record of the integration (observed). When
the team has a DevOps agent it adds its own account for reviewers, what
changes, how to test it and the risks, under a heading that names it a claim:
it read the same record and observed nothing itself. Without a DevOps agent,
or when its call fails, the request carries the record alone.

Why it did not open is kept on the integration (``details["auto_publish"]``)
so the Review page can say so; it never holds the integration back.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config import settings
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from models import Agent
from models.integration import SprintIntegration
from sqlmodel import Session, select

from services.contracts import roles
from services.models import company_models
from services.release.git_publish import MAX_BODY_CHARS, PublishRefused
from services.release.integration import latest
from services.release.integration_publish import publish_spec, record_publication
from services.workspace import workspace_daemon

logger = setup_logger(__name__)

PUBLISHED_BY = "La Fabrique, after the sprint closed"
AGENT_ID = "system:auto_publish"
PUBLISH_TIMEOUT_SECONDS = 150.0
ACCOUNT_HEADING = "## DevOps's account (claimed, not observed)"
ACCOUNT_MAX_CHARS = 6000
RECORD_MAX_CHARS = 12000
DEVOPS_TIMEOUT_SECONDS = 120.0
DEVOPS_MAX_TOKENS = 1500

PROMPT = """You are the team's DevOps engineer. The sprint below is integrated on
one branch and its suite passed. Write the description reviewers read on its
pull request, in Markdown, under three headings: "What changes", "How to test
it" and "Risks". Use only what the record says: name the files and tasks it
lists, and claim no test, check or result it does not show. At most 400 words.

The platform's record of the integration:

{record}
"""


def devops_account(session: Session, company_id: int, record: str) -> str | None:
    """The DevOps agent's description of the integration, or ``None``."""
    agent = session.exec(
        select(Agent).where(Agent.company_id == company_id, Agent.role == roles.DEVOPS)
    ).first()
    if agent is None:
        return None
    model = company_models.resolve(session, company_id, roles.DEVOPS, agent.model or "")
    client = LLMProxyClient(settings.LLM_PROXY_URL)
    client.timeout = DEVOPS_TIMEOUT_SECONDS
    client.company_id = company_id
    messages = [
        {"role": "user", "content": PROMPT.format(record=record[:RECORD_MAX_CHARS])}
    ]
    if agent.system_prompt:
        messages.insert(0, {"role": "system", "content": agent.system_prompt})
    try:
        result = client.chat(
            messages=messages,
            model=model,
            role=roles.DEVOPS,
            max_tokens=DEVOPS_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 - The request goes out with the record alone.
        logger.warning("DevOps account failed for company %s: %s", company_id, exc)
        return None
    if not isinstance(result, dict) or result.get("error"):
        logger.warning("DevOps account failed for company %s: %r", company_id, result)
        return None
    choices = result.get("choices") or [{}]
    text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
    return text[:ACCOUNT_MAX_CHARS] or None


def with_account(body: str, account: str | None) -> str:
    """*body* with the DevOps account before its ``Fabrique-`` trailer."""
    if not account:
        return body
    head, sep, trailer = body.rpartition("\n\nFabrique-Sprint:")
    if not sep:
        head, trailer = body, ""
    section = f"\n\n{ACCOUNT_HEADING}\n\n{account}"
    tail = f"{sep}{trailer}" if sep else ""
    return (head + section)[: MAX_BODY_CHARS - len(tail)] + tail


def _note(engine: Any, row_id: int, status: str, reason: str) -> dict[str, str]:
    note = {"status": status, "reason": reason[:300]}
    with Session(engine) as session:
        row = session.get(SprintIntegration, row_id)
        if row is not None:
            row.details = {**(row.details or {}), "auto_publish": note}
            session.add(row)
            session.commit()
    return note


def _prepare(engine: Any, sprint_id: int) -> tuple[int | None, dict[str, Any] | str]:
    """The integration's id and its publish request, or why it is not published."""
    with Session(engine) as session:
        row = latest(session, sprint_id)
        if row is None:
            return None, "no integration"
        if row.publication:
            return row.id, "already published"
        if row.status != "tests_passed":
            return row.id, f"the integration ended {row.status}, not tests_passed"
        try:
            spec = publish_spec(session, row)
        except PublishRefused as refused:
            return row.id, str(refused.detail or refused)
        spec["body"] = with_account(
            spec["body"], devops_account(session, row.company_id, spec["body"])
        )
        return row.id, spec


async def after_integration(
    nats_client: Any, engine: Any, sprint_id: int
) -> dict[str, str]:
    """Open the sprint's draft request when its integration passed; what happened."""
    row_id, spec = await asyncio.to_thread(_prepare, engine, sprint_id)
    if row_id is None:
        return {"status": "skipped", "reason": "no integration"}
    if spec == "already published":
        return {"status": "skipped", "reason": spec}
    if isinstance(spec, str):
        return _note(engine, row_id, "not_published", spec)
    try:
        reply = await workspace_daemon.daemon_request(
            nats_client, "workspace.git.publish", spec, PUBLISH_TIMEOUT_SECONDS
        )
        with Session(engine) as session:
            row = session.get(SprintIntegration, row_id)
            record_publication(
                session, row, reply, published_by=PUBLISHED_BY, agent_id=AGENT_ID
            )
    except (
        PublishRefused,
        workspace_daemon.DaemonUnavailable,
        workspace_daemon.DaemonError,
    ) as exc:
        return _note(engine, row_id, "failed", str(getattr(exc, "detail", "") or exc))
    return _note(
        engine, row_id, "published", str(reply.get("url") or reply.get("how") or "")
    )

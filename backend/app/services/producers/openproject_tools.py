"""OpenProject helpers for sprint orchestration with graceful local fallback."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from config import settings

from services.producers import openproject_client

logger = logging.getLogger("fabrique-openproject-tools")

PublishEvent = Callable[[dict[str, Any]], Awaitable[None]]


async def _emit_chat(
    publish_event: PublishEvent | None,
    agent: str,
    task: str,
    message: str,
) -> None:
    if not publish_event:
        return
    await publish_event(
        {
            "type": "message",
            "agent": agent,
            "role": agent,
            "task": task,
            "message": message,
        }
    )


def _simulated_ticket(subject: str, interaction_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "simulated": True,
        "work_package_id": f"SIM-{uuid4().hex[:8].upper()}",
        "openproject_url": None,
        "interaction_id": interaction_id,
        "subject": subject,
    }


async def create_work_package(
    payload: dict[str, Any],
    publish_event: PublishEvent | None = None,
) -> dict[str, Any]:
    """
    Create an OpenProject work package, or fall back to a simulated local ticket.

    When OpenProject is unreachable, the sprint conversation must continue.
    """
    project_id = payload.get("project_id", 1)
    subject = str(payload.get("subject") or "").strip()
    description = str(payload.get("description") or "").strip()
    interaction_id = str(payload.get("interaction_id") or uuid4())
    sprint_id = payload.get("sprint_id")

    if not subject:
        return {
            "ok": False,
            "error": "subject is required",
            "interaction_id": interaction_id,
        }

    if not settings.OPENPROJECT_TOKEN_PO:
        await _emit_chat(
            publish_event,
            "PO",
            subject,
            "OpenProject token missing, I will simulate the backlog locally.",
        )
        return _simulated_ticket(subject, interaction_id)

    headers = {
        "Authorization": f"Bearer {settings.OPENPROJECT_TOKEN_PO}",
        "Content-Type": "application/json",
    }
    body = {
        "subject": subject,
        "description": {"raw": description},
        "projectId": project_id,
        "type": 1,
    }
    if sprint_id:
        body["versionId"] = sprint_id

    try:
        data = await openproject_client.post_work_package(
            settings.OPENPROJECT_URL, headers, body
        )
        work_package_id = data.get("id")
        return {
            "ok": True,
            "simulated": False,
            "work_package_id": work_package_id,
            "openproject_url": f"{settings.OPENPROJECT_URL}/work_packages/{work_package_id}",
            "interaction_id": interaction_id,
            "subject": subject,
        }
    except openproject_client.OpenProjectUnreachable:
        logger.warning(
            "OpenProject unreachable for subject=%s; using local simulation", subject
        )
        await _emit_chat(
            publish_event,
            "PO",
            subject,
            "OpenProject unreachable, I will simulate the backlog locally.",
        )
        return _simulated_ticket(subject, interaction_id)
    except openproject_client.OpenProjectRequestError as exc:
        logger.warning("OpenProject request failed for subject=%s: %s", subject, exc)
        await _emit_chat(
            publish_event,
            "PO",
            subject,
            "OpenProject unreachable, I will simulate the backlog locally.",
        )
        return _simulated_ticket(subject, interaction_id)
    except openproject_client.OpenProjectStatusError as exc:
        logger.warning(
            "OpenProject returned HTTP error for subject=%s: %s", subject, exc
        )
        await _emit_chat(
            publish_event,
            "PO",
            subject,
            "OpenProject returned an error, I will simulate the backlog locally.",
        )
        return _simulated_ticket(subject, interaction_id)

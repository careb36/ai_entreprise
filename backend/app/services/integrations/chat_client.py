"""The chat platforms' HTTP adapter: Slack's Web API and a Teams incoming webhook.

``slack_call`` posts one Slack Web API method to ``https://slack.com/api/``
(a fixed host: the method name is checked, never a URL from data) with the
bot token, and returns Slack's JSON answer; Slack reports an application
error as ``{"ok": false, "error": ...}`` with HTTP 200, which is raised here
as ``ChatPostError`` with that error code. ``teams_post`` posts a card to an
incoming webhook URL the caller has already checked, without following
redirects, and returns the status code. A transport failure is raised as
``ChatPostError`` naming the transport's exception; no transport exception
leaves this module, and the Teams URL (a bearer credential) is never put in
an error.
"""

from __future__ import annotations

import json
import re
from typing import Any

SLACK_API = "https://slack.com/api/"
SLACK_METHODS = frozenset(
    {"auth.test", "chat.postMessage", "chat.update", "chat.postEphemeral"}
)
MAX_ANSWER_BYTES = 256 * 1024


class ChatPostError(RuntimeError):
    """The call could not be made, or the platform refused it; the message says why."""


async def slack_call(
    method: str, token: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """Call Slack's *method* with *payload*; Slack's answer when ``ok``."""
    import httpx

    if method not in SLACK_METHODS:
        raise ChatPostError(f"unsupported Slack method {method}")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "La-Fabrique-Chat",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                SLACK_API + method, json=payload, headers=headers
            )
            content = response.content[:MAX_ANSWER_BYTES]
    except Exception as exc:  # Every transport failure, named and re-raised.
        raise ChatPostError(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise ChatPostError(f"Slack answered HTTP {response.status_code}")
    try:
        answer = json.loads(content)
    except ValueError as exc:
        raise ChatPostError("Slack's answer is not JSON") from exc
    if not isinstance(answer, dict):
        raise ChatPostError("Slack's answer is not an object")
    if not answer.get("ok"):
        error = str(answer.get("error") or "unknown_error")
        raise ChatPostError(f"Slack refused: {re.sub(r'[^a-z_]', '', error)[:60]}")
    return answer


async def teams_post(url: str, card: dict[str, Any], timeout: float) -> int:
    """POST *card* to the Teams incoming webhook *url*; the status code."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                url, json=card, headers={"User-Agent": "La-Fabrique-Chat"}
            )
    except Exception as exc:  # Every transport failure, named and re-raised.
        # Only the exception's type: its text can carry the URL, a credential.
        raise ChatPostError(f"{type(exc).__name__} posting to Teams") from exc
    return response.status_code

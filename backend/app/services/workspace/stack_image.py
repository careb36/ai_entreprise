"""The project's sandbox image, built by the daemon ahead of the commands
that need it (issue #766; the daemon's side is ``vscode-daemon/stack_image.py``).

In container mode with no ``DELIVERY_SANDBOX_IMAGE`` the sandbox request
asks for the project's own image (``stack_image: true``,
``sandbox.request_for``): Python with Poetry, Node with npm, Go, as the
project's files call for. Building it the first time takes minutes, longer
than a test run's own timeout, so the loop asks for it first
(``workspace.sandbox.image``, with its own timeout) and records the reply on
the evidence as ``stack_image``; the commands then find it on the machine.
A daemon that cannot build it says why, and the commands that need it fail
with that reason: the tests stay unverified, never run in another image.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from config import settings

SUBJECT = "workspace.sandbox.image"


def wanted(box: dict[str, Any] | None) -> bool:
    """Whether *box* (a sandbox request) runs in the project's stack image."""
    return (
        bool(box) and box.get("mode") == "container" and box.get("stack_image") is True
    )


def _reply(raw: Any) -> dict[str, Any]:
    reply = raw if isinstance(raw, dict) else {}
    error = reply.get("error")
    if isinstance(error, dict):
        return {"image": None, "error": str(error.get("message") or "refused")}
    return reply or {
        "image": None,
        "error": "the workspace daemon sent an unreadable reply",
    }


def _silent(exc: Exception) -> dict[str, Any]:
    return {
        "image": None,
        "error": f"the workspace daemon did not build the stack image ({type(exc).__name__})",
    }


async def prepare(
    nats_client: Any, workspace_root: str, box: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Ask the daemon for the image; its reply, ``None`` when none is wanted."""
    if not wanted(box):
        return None
    try:
        raw = await nats_client.request(
            SUBJECT,
            {"workspace_root": workspace_root},
            timeout=settings.STACK_IMAGE_BUILD_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - recorded; the commands then say why.
        return _silent(exc)
    return _reply(raw)


def prepare_from_thread(
    request: Callable[[str, dict[str, Any], float], dict[str, Any]],
    workspace_root: str,
    box: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """``prepare`` for a worker thread, through its *request* bridge."""
    if not wanted(box):
        return None
    try:
        raw = request(
            SUBJECT,
            {"workspace_root": workspace_root},
            settings.STACK_IMAGE_BUILD_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - the tool's own run then says why.
        return _silent(exc)
    return _reply(raw)

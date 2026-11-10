"""In-process events between contexts (ADR 0012, 0016, issue #469 step 11).

A context that may not import another tells it something happened by an
event: the one that knows emits it by name, the one that cares registers a
handler at startup (``on``). ``emit`` calls each handler in the order they
were registered, synchronously, in the emitter's thread and event loop; a
handler that needs to run long starts its own background task. A handler
that raises is logged and never stops the emitter or the other handlers.

Events:

- ``sprint.closed`` (``SPRINT_CLOSED``): the loop closed a sprint, its first
  close or one after a reopening, with ``nats_client``, ``engine`` and
  ``sprint_id``. ``release`` starts the sprint's integration from it
  (``integration_jobs.install``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

SPRINT_CLOSED = "sprint.closed"

_HANDLERS: dict[str, list[Callable[..., Any]]] = {}


def on(event: str, handler: Callable[..., Any]) -> None:
    """Register *handler* for *event*; registering the same one again does nothing."""
    handlers = _HANDLERS.setdefault(event, [])
    if handler not in handlers:
        handlers.append(handler)


def off(event: str, handler: Callable[..., Any]) -> None:
    """Remove *handler* from *event*, if it was registered."""
    handlers = _HANDLERS.get(event, [])
    if handler in handlers:
        handlers.remove(handler)


def handlers(event: str) -> list[Callable[..., Any]]:
    """The handlers registered for *event*, in order."""
    return list(_HANDLERS.get(event, []))


def emit(event: str, **payload: Any) -> None:
    """Call every handler of *event* with *payload* as keyword arguments."""
    for handler in handlers(event):
        try:
            handler(**payload)
        except Exception as exc:  # noqa: BLE001 - A listener never holds the emitter back.
            logger.warning("Handler %r of %s failed: %s", handler, event, exc)

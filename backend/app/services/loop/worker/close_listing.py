"""The project's listing for the follow-up plan, read through the daemon from
the closing thread (issue #766).

The close runs in a worker thread (``asyncio.to_thread``), but the daemon is
reached over NATS on the event loop. ``bind`` puts a reader on the current
context before the close starts; ``asyncio.to_thread`` copies the context, so
``close._plan_follow_up`` finds it with ``current`` and waits at most
``LISTING_WAIT_SECONDS``. Any failure returns ``None``: the plan then uses
the backend's own listing (``planning.sprint_context.project_listing``).
"""

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from services.planning.sprint_planner import project_listing

# How long the closing thread waits for the daemon's listing of the project.
LISTING_WAIT_SECONDS = 15.0

ListingOf = Callable[[str | None], str | None]
_LISTING_OF: ContextVar[ListingOf | None] = ContextVar("listing_of", default=None)


def bind(nats_client: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Make the daemon's listing readable from threads started from here on."""

    def listing_of(path: str | None) -> str | None:
        """The project's listing through the daemon, from the closing thread."""
        future = asyncio.run_coroutine_threadsafe(
            project_listing(nats_client, path), loop
        )
        try:
            return future.result(LISTING_WAIT_SECONDS)
        except Exception:  # noqa: BLE001 - The plan uses the backend's own listing.
            future.cancel()
            return None

    _LISTING_OF.set(listing_of)


def current() -> ListingOf | None:
    """The reader ``bind`` set for this context, or ``None``."""
    return _LISTING_OF.get()

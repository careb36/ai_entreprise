"""Who an in-process API call acts for (``docs/design/owner-actions.md``, B15).

An owner action (``services/management/owner_actions.py``) drives the
platform's own API from inside the backend, as the Owner who started it,
without minting a token: each call is an ASGI request to the running
application whose scope carries an ``InProcessPrincipal`` under
``SCOPE_KEY``. ``auth_deps`` resolves the caller from it, and only from an
instance of this class, so an HTTP client cannot forge it: uvicorn builds the
scope from the socket, and a header is bytes, never a Python object.

The principal is honoured only while ``live()`` says so: the run it belongs
to is still running and nobody asked it to stop. The routes check the
person's current roles as they would for her browser.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

SCOPE_KEY = "fabrique.in_process_principal"


@dataclass(frozen=True)
class InProcessPrincipal:
    """The person an in-process call acts for, and the run it belongs to."""

    user_id: int
    run_id: int
    live: Callable[[], bool] = field(default=lambda: False, compare=False)


def from_scope(scope: dict) -> InProcessPrincipal | None:
    """The principal in an ASGI scope, or ``None``; never built from headers."""
    principal = scope.get(SCOPE_KEY)
    return principal if isinstance(principal, InProcessPrincipal) else None

"""Whether this daemon may contact a forge for a request (issue #727).

The backend sends the company's forge policy with every ``workspace.git.*``
request and with the ``git`` part of a delivery, as ``forge_policy``:

- ``none``: no remote is contacted. The base is not fetched before a new
  branch; publishing and reading a request's comments are refused.
- ``fetch``: the base is fetched from its remote; publishing is refused.
- ``publish``: fetch, push, and the request opened or read with ``gh``/``glab``.

This daemon is the last word, not the backend (defence in depth): a request
without a policy, or with one it does not know, is ``none``, so a backend
that predates the policy, or a compromised one that leaves it out, gets no
remote contact. ``FORGE_POLICY_MAX`` in this daemon's environment caps what
any request may ask for (``none`` on an air-gapped computer), whatever the
backend sends. What was applied goes on the answer: ``forge_policy`` and
``remote_contacted``.

The values match ``backend/app/services/contracts/forge_policy.py``; the
daemon imports nothing from the backend.
"""

from __future__ import annotations

import os
from typing import Any

NONE = "none"
FETCH = "fetch"
PUBLISH = "publish"
POLICIES = (NONE, FETCH, PUBLISH)
FORGE_DISABLED = "forge_disabled"


def _parse(value: Any) -> str:
    return value if isinstance(value, str) and value in POLICIES else NONE


def ceiling() -> str:
    """This computer's ceiling: ``FORGE_POLICY_MAX``, ``publish`` when unset, ``none`` when unreadable."""
    raw = os.environ.get("FORGE_POLICY_MAX")
    return PUBLISH if raw is None or raw.strip() == "" else _parse(raw.strip().lower())


def effective(requested: Any) -> str:
    """The policy applied to a request: what it asked for, capped by this computer's ceiling."""
    wanted, limit = _parse(requested), ceiling()
    return wanted if POLICIES.index(wanted) <= POLICIES.index(limit) else limit


def of(spec: Any) -> str:
    """The policy applied to *spec* (a request's body, or its ``git`` part)."""
    return effective(spec.get("forge_policy") if isinstance(spec, dict) else None)


def allows_fetch(policy: str) -> bool:
    return policy in (FETCH, PUBLISH)


def allows_publish(policy: str) -> bool:
    return policy == PUBLISH


def refusal_message(policy: str, action: str) -> str:
    return (
        f"{FORGE_DISABLED}: the forge policy for this request is {policy!r}, "
        f"so this daemon does not {action}"
    )

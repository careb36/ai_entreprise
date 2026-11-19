"""Whether a company's git may contact a forge, as plain data (issue #727).

A company's projects run git locally: task branches, the sprint's
integration, its tests and the local merge all happen on the computer that
holds the project. Contact with an outside forge (GitHub, GitLab,
Bitbucket...) is a recorded decision, ``companies.forge_policy``:

- ``none``: never contacts a remote. The base is not fetched before a new
  branch, and publishing is refused (409 ``forge_disabled``).
- ``fetch``: reads the base from its remote before a new branch. Publishing
  is refused.
- ``publish``: fetch, plus the Manager's publish actions (push, open the
  request, read its comments back).

The values are ordered; a deployment may set a ceiling (``FORGE_POLICY_MAX``)
the company's value never exceeds, and the workspace daemon applies its own
(``vscode-daemon/forge_gate.py``). A value that is not one of the three reads
as ``none``: an unknown or missing policy never widens contact.

Pure: no I/O, nothing decided here beyond the order of the values.
"""

from __future__ import annotations

from typing import Any

NONE = "none"
FETCH = "fetch"
PUBLISH = "publish"
POLICIES: tuple[str, ...] = (NONE, FETCH, PUBLISH)
# The value a new company starts at: privacy by default (GDPR art. 25).
DEFAULT = NONE
# The refusal's code, in the 409 body and on the daemon's error.
FORGE_DISABLED = "forge_disabled"


def parse(value: Any) -> str:
    """*value* as a policy; anything else is ``none``."""
    return value if isinstance(value, str) and value in POLICIES else NONE


def rank(policy: str) -> int:
    return POLICIES.index(parse(policy))


def cap(policy: Any, ceiling: Any) -> str:
    """The lower of *policy* and *ceiling*, each parsed."""
    wanted, limit = parse(policy), parse(ceiling)
    return wanted if rank(wanted) <= rank(limit) else limit


def allows_fetch(policy: Any) -> bool:
    return rank(parse(policy)) >= rank(FETCH)


def allows_publish(policy: Any) -> bool:
    return parse(policy) == PUBLISH


def refusal(policy: Any, action: str = "publish to a remote") -> dict[str, Any]:
    """The 409 body of an action the policy does not allow."""
    current = parse(policy)
    return {
        "code": FORGE_DISABLED,
        "forge_policy": current,
        "message": (
            f"This company's forge policy is {current!r}: it does not {action}. "
            "An Owner can set it to 'publish' in the company's settings."
        ),
    }

"""Where a console assistant's consultation runs: the company's sandbox (issue #525).

A console assistant (``console_assistants``) is a CLI with the agent's prompt
in its arguments, run by the workspace daemon in the project folder. It is
sent with the company's sandbox (``services/workspace/sandbox.py``) under the
purpose ``assistant``, so the daemon runs it where the company's other
commands run and its own floor, ``FABRIQUE_SANDBOX_REQUIRED``, applies.

An assistant needs its provider: the network (the company's sandbox network,
``DELIVERY_SANDBOX_NETWORK``, off by default) and the provider's key, which
reaches the sandbox only by name, from the company's ``console_env`` (the
values stay in the daemon's environment). It never touches the dependency
cache.

When it cannot run where it would be sent (a contained mode with the network
off, a company whose sandbox cannot be read) or the daemon's reply shows the
sandbox stopped it (the floor's runtime missing, the CLI not in the image, no
network to its provider), the consultation is skipped with the reason: the
advice is optional, the task goes on. Each reason is said once, in the
Glassroom, with the daemon's sandbox report when there is one.

Design taken (no code) from the OpenHands runtime, where every action an
agent's tool runs goes into the session's sandbox and nothing runs on the
host by default, and from the Codex cloud sandbox, where the network is off
unless the environment opens it and secrets reach the run only as named
variables.
"""

from __future__ import annotations

from typing import Any

PURPOSE = "assistant"
CONTAINED = ("container", "bwrap")
NETWORK_OFF = (
    "the company's sandbox keeps the network off (DELIVERY_SANDBOX_NETWORK), "
    "and an assistant needs it to reach its provider"
)
MISSING_EXIT_CODES = {126, 127}


def request_for(sprint: dict[str, Any], engine: Any = None) -> dict[str, Any]:
    """The ``sandbox`` field of an assistant's ``workspace.execute``: the
    company's sandbox with purpose ``assistant``, no cache, and the names in
    ``sandbox_env`` and ``console_env``. Raises ``PolicyUnavailable`` when the
    company cannot be read: an assistant is then not asked at all."""
    from services.workspace import sandbox

    company = None
    company_id = (sprint or {}).get("company_id")
    if company_id is not None:
        try:
            from database import engine as default_engine
            from models.company import Company
            from sqlmodel import Session

            with Session(engine or default_engine) as session:
                company = session.get(Company, company_id)
        except Exception as e:  # Fail closed, with the reason.
            raise sandbox.PolicyUnavailable(
                f"the company's sandbox could not be read ({type(e).__name__})"
            ) from e
    box = sandbox.request_for(company)
    extra = [
        str(n)
        for n in (getattr(company, "console_env", None) or [])
        if isinstance(n, str) and sandbox.valid_env_name(n.strip())
    ]
    return {
        **box,
        "purpose": PURPOSE,
        "cache": False,
        "env": list(dict.fromkeys([*box["env"], *(n.strip() for n in extra)])),
    }


def refusal(box: dict[str, Any]) -> str | None:
    """Why an assistant is not sent in *box*, else ``None``. Only a contained
    mode can refuse: on ``host`` the daemon decides (its floor)."""
    if box.get("mode") in CONTAINED and not box.get("network"):
        return NETWORK_OFF
    return None


def stopped_by_sandbox(result: dict[str, Any]) -> str | None:
    """Why the daemon's reply shows the sandbox kept the assistant from
    running, else ``None`` (then the reply is read as before). Read from what
    the daemon observed: its ``error``, and the ``sandbox`` it applied."""
    if result.get("success"):
        return None
    error = str(result.get("error") or "")
    if error.startswith("sandbox unavailable"):
        return f"the daemon could not apply its sandbox: {error[len('sandbox unavailable: ') :] or error}"
    applied = result.get("sandbox")
    if not isinstance(applied, dict) or not applied.get("isolated"):
        return None
    where = f"{applied.get('mode')} sandbox" + (
        " the daemon requires" if applied.get("forced_by_daemon") else ""
    )
    if result.get("exit_code") in MISSING_EXIT_CODES:
        return f"the CLI is not installed in the {where}"
    if applied.get("network") == "off":
        return f"the {where} has the network off, and an assistant needs it to reach its provider"
    return None

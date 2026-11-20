"""Bring a company's local copy up to date with its remote (issue #745).

A company created from a folder whose clone is weeks behind its remote would
plan its first sprint, and its agents would read the specification,
architecture and repository map, from old files. So the New company form,
and later the company's settings, can ask the workspace daemon to
fast-forward the local base branch to its remote's tip
(``workspace.git.sync_base``, ``vscode-daemon/git_sync.py``): no merge
commit, no rebase, no reset; a diverged, dirty, mid-rebase or unreachable
repository is refused, changing nothing, with the reason.

What the form shows before the company exists, and who may ask for it, is
``folder_check.py``.

- ``request_sync``: the daemon's fast-forward under a forge policy. Under
  ``none`` it answers ``forge_disabled`` without sending anything, and the
  daemon refuses it again on its side (``forge_gate``, issue #727).
- ``record``: what the daemon observed goes on the company's record as
  ``base_sync_observed`` (the remote, the base, the old and new commit, the
  counts and the outcome), beside the account that asked. These are the
  daemon's observations, never the request's claims.
- ``sync_company``: the settings page's action (Manager), held to the
  company's forge policy in force.
"""

from __future__ import annotations

from typing import Any

from models import Company
from sqlmodel import Session

from services.contracts import forge_policy
from services.platform import forge_policy_setting
from services.platform.agent_service import AgentService
from services.release.integration import _project_path
from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)

AUDIT_ACTION = "base_sync_observed"
# The daemon's fetch of one branch (20 s there), the fast-forward and the reply.
SYNC_TIMEOUT_SECONDS = 35.0
# What the record keeps of the daemon's answer.
_KEPT = (
    "ok",
    "outcome",
    "reason",
    "repository",
    "base",
    "remote",
    "remote_url",
    "before",
    "after",
    "remote_commit",
    "behind",
    "ahead",
    "forge_policy",
    "remote_contacted",
)


async def request_sync(
    nats_client: Any, path: str, base: str | None, policy: str
) -> dict[str, Any]:
    """The daemon's answer to ``workspace.git.sync_base``, or why it was not asked or did not answer."""
    if not forge_policy.allows_fetch(policy):
        return {
            "ok": False,
            "outcome": forge_policy.FORGE_DISABLED,
            "reason": forge_policy.refusal(policy, "read the remote")["message"],
            "forge_policy": policy,
            "remote_contacted": False,
        }
    try:
        observed = await daemon_request(
            nats_client,
            "workspace.git.sync_base",
            {"workspace_root": path, "base": base or "", "forge_policy": policy},
            SYNC_TIMEOUT_SECONDS,
        )
    except DaemonUnavailable:
        return {
            "ok": False,
            "outcome": "daemon_unavailable",
            "reason": "No workspace daemon answered",
            "forge_policy": policy,
            "remote_contacted": None,
        }
    except DaemonError as e:
        return {
            "ok": False,
            "outcome": "refused",
            "reason": e.message,
            "forge_policy": policy,
            "remote_contacted": None,
        }
    return {key: observed[key] for key in _KEPT if key in observed}


def record(
    session: Session,
    company_id: int,
    observed: dict[str, Any],
    *,
    at: str,
    by: str,
    user_id: int | None,
    token_id: int | None,
) -> None:
    """The daemon's observation on the company's audit record (``base_sync_observed``)."""
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}:base_sync",
        agent_id=f"human:{by}",
        action=AUDIT_ACTION,
        details={
            "company_id": str(company_id),
            "at": at,
            "user_id": user_id,
            "token_id": token_id,
            # Who observed what follows: the workspace daemon, not the request.
            "observed_by": "workspace_daemon",
        },
        result=observed,
        decision_signature=None,
        cost_tokens=0,
    )


async def sync_company(
    nats_client: Any,
    session: Session,
    company: Company,
    *,
    by: str,
    user_id: int | None,
    token_id: int | None,
) -> dict[str, Any]:
    """The settings page's "Bring the local copy up to date": asked, recorded, returned."""
    path = _project_path(session, company.id)
    if not path:
        return {
            "ok": False,
            "outcome": "no_folder",
            "reason": "The company has no project folder",
            "remote_contacted": False,
        }
    policy = forge_policy_setting.effective(session, company.id)
    observed = await request_sync(nats_client, path, company.git_base_branch, policy)
    record(
        session,
        company.id,
        observed,
        at="settings",
        by=by,
        user_id=user_id,
        token_id=token_id,
    )
    return observed

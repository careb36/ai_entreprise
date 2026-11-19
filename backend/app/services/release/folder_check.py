"""What the New company form may learn about a folder's git before the company exists (issue #745).

``POST /v1/workspace/git_check`` runs before the person belongs to the
company they are creating, so it cannot be held to a company role. It is
held instead to these rules (OWASP ASVS V4 access control, V11 business
logic; NIST SP 800-53 AC-6 least privilege, AU-2):

- **Who** (``may_check``): on a deployment with one account, that account;
  with more, only an account that already holds a role in some company (an
  API token always does), or the deployment's first account (the operator
  who installed it). A newly registered stranger on a shared deployment is
  refused (403): they would otherwise make the daemon read folders and
  fetch remotes on someone else's computer.
- **How often**: ``GIT_CHECK_RATE_LIMIT`` per account (the router's limiter).
- **What is answered** (``form_answer``): only what the form shows, the
  branch names, the clean and in-progress flags, the remote's URL as the
  folder's config names it (credentials stripped by the daemon), and how
  many commits each side is ahead. No commit ids, no repository path, no
  text from the remote: an unreadable remote is answered with its code
  (``remote_needs_interaction`` or ``remote_unreachable``), never git's
  words, which may carry the server's own message.
- **What is recorded** (``record``): each "Check remote" as
  ``folder_remote_checked``, with the account, the folder and what was
  observed (counts and flags), never a URL.

``check_folder`` asks the daemon (``workspace.git.base_status``): ``fetch``
only on ``contact`` (the Owner pressed "Check remote"), capped by
``FORGE_POLICY_MAX``; without it, nothing contacts the remote.
"""

from __future__ import annotations

from typing import Any

from models import CompanyMember, User
from sqlalchemy import func
from sqlmodel import Session, select

from services.contracts import forge_policy
from services.platform import forge_policy_setting
from services.platform.agent_service import AgentService
from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)

AUDIT_ACTION = "folder_remote_checked"
# A fetch of one branch on the daemon (15 s there) and its reply.
CHECK_TIMEOUT_SECONDS = 25.0
# The fields of the daemon's observation the form receives, as they are.
_SHOWN = (
    "state",
    "is_repo",
    "base_ref",
    "current_branch",
    "clean",
    "operation_in_progress",
    "remote_url",
    "base_remote",
    "base_not_on_remote",
    "base_remote_not_contacted",
    "base_behind_remote",
    "base_ahead_of_remote",
    "remote_contacted",
    "remote_needs_interaction",
    "forge_policy",
    "forge_policy_max",
)
# What the record keeps: never the URL.
_RECORDED = tuple(f for f in _SHOWN if f != "remote_url") + ("remote_unreachable",)


def may_check(session: Session, user: Any) -> bool:
    """Whether *user* may have the daemon read a folder before belonging to its company."""
    if user is None:
        return True  # AUTH_REQUIRED off: a single-person install.
    if getattr(user, "_api_token_company_id", None) is not None:
        return True  # an API token is a company's
    accounts = session.exec(select(func.count()).select_from(User)).one()
    if accounts <= 1:
        return True
    first = session.exec(select(func.min(User.id))).one()
    if user.id == first:
        return True
    member = session.exec(
        select(CompanyMember.id).where(CompanyMember.user_id == user.id).limit(1)
    ).first()
    return member is not None


async def check_folder(
    nats_client: Any, path: str, base: str | None, contact: bool
) -> dict[str, Any]:
    """The folder's base as the daemon observed it now; the remote is read only when *contact*."""
    ceiling = forge_policy_setting.ceiling()
    policy = forge_policy.cap(
        forge_policy.FETCH if contact else forge_policy.NONE, ceiling
    )
    common = {"forge_policy_max": ceiling}
    try:
        observed = await daemon_request(
            nats_client,
            "workspace.git.base_status",
            {"workspace_root": path, "base": base or "", "forge_policy": policy},
            CHECK_TIMEOUT_SECONDS,
        )
    except DaemonUnavailable:
        return {**common, "state": "unknown", "reason": "daemon_unavailable"}
    except DaemonError:
        return {**common, "state": "unknown", "reason": "daemon_refused"}
    return {**common, "state": "observed", **observed}


def form_answer(observed: dict[str, Any]) -> dict[str, Any]:
    """Only what the form shows (see the module's docstring)."""
    answer = {key: observed[key] for key in _SHOWN if key in observed}
    if observed.get("state") == "unknown":
        answer["reason"] = observed.get("reason")
    if observed.get("remote_unreachable"):
        answer["remote_unreachable"] = (
            "remote_needs_interaction"
            if observed.get("remote_needs_interaction")
            else "remote_unreachable"
        )
    return answer


def record(
    session: Session,
    path: str,
    answer: dict[str, Any],
    *,
    by: str,
    user_id: int | None,
    token_id: int | None,
) -> None:
    """A "Check remote" on the deployment's audit record (no company yet)."""
    AgentService().audit_interaction(
        session=session,
        company_id=None,
        interaction_id=f"folder:{path}:remote_check",
        agent_id=f"human:{by}",
        action=AUDIT_ACTION,
        details={
            "folder": path,
            "user_id": user_id,
            "token_id": token_id,
            "observed_by": "workspace_daemon",
        },
        result={key: answer[key] for key in _RECORDED if key in answer},
        decision_signature=None,
        cost_tokens=0,
    )

"""Accept La Fabrique's work into the local repository, with no remote involved.

A company may keep its code in a local repository and never push it, or push
only when a person decides to. Publishing a branch (``services/release/git_publish.py``,
``services/release/integration_publish.py``) is therefore one way out of a task's or a
sprint's branch, never a step the loop requires: a Manager can instead merge
the branch into a base branch of the repository on the daemon's machine
(``vscode-daemon/local_merge.py``, ``workspace.git.merge``). The merge is
computed in a worktree of its own, a conflict changes nothing, and the base
moves only to a commit that contains exactly the branch at the commit the
evidence covers.

What the daemon answered (the base, its tip before and after, the branch's
commit, a fast-forward or a merge) is an observation: kept as a
``RunPublication`` with ``how == "local"`` for a run, under the integration's
``details["local_merge"]`` for a sprint, and audited either way.

A sprint's integration is merged only once a person approved it when the
company asks for that (``services/release/integration_review.py``); the
merge records the review it stood on. For a company that hosts the factory,
an integration whose diff (read from git by the daemon) touches a protected
path is merged only once a signed-in Owner acknowledged each such path in that
approval (``services/release/protected_merge.py``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from models.agent_run import AgentRun
from models.company import Company
from models.integration import SprintIntegration
from models.review import RunPublication
from sqlmodel import Session

from services.evidence.review_changes import run_changes
from services.platform.agent_service import AgentService
from services.release import integration_review, protected_merge, self_change
from services.release.git_publish import PublishRefused

LOCAL = "local"
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def _base(session: Session, company_id: int, base: str | None) -> str:
    """The branch to merge into: the branch's own base, else the company's, else the checkout's."""
    if base and base != "HEAD":
        return base
    company = session.get(Company, company_id)
    return (company.git_base_branch or "") if company else ""


def run_merge_spec(
    session: Session, run: AgentRun, *, merger: Any = None
) -> dict[str, Any]:
    """The ``workspace.git.merge`` request for *run*'s task branch, or why there is none.

    A task branch of a self-improvement cycle's sprint is merged only by a
    signed-in Owner (*merger*; ``self_change.require_owner``).
    """
    self_change.require_owner(
        session,
        run.company_id,
        sprint_id=self_change.sprint_of_run(session, run),
        merger=merger,
    )
    git = run_changes(session, run).get("git") or {}
    if not git.get("branch") or not git.get("repository"):
        raise PublishRefused(409, "This run was not delivered on a Git branch")
    commit = git.get("commit") or git.get("parent")
    if not commit:
        raise PublishRefused(409, "This run's delivery names no commit to merge")
    return {
        "workspace_root": git["repository"],
        "branch": git["branch"],
        "commit": commit,
        "base": _base(session, run.company_id, git.get("base")),
        "message": f"Merge {git['branch']}\n\nFabrique-Run: {run.id}\n",
    }


def needs_diff(session: Session, row: SprintIntegration) -> bool:
    """Whether the merge of *row* needs its diff read first (protected paths)."""
    return protected_merge.applies(session, row.company_id)


def protected_facts(session: Session, row: SprintIntegration) -> dict[str, Any] | None:
    """The protected-path facts of *row* for the Manager agent, or ``None``."""
    return protected_merge.facts(
        session, row, integration_review.approving_review(session, row)
    )


def integration_merge_spec(
    session: Session,
    row: SprintIntegration,
    diff: dict[str, Any] | None = None,
    *,
    merger: Any = None,
) -> dict[str, Any]:
    """The ``workspace.git.merge`` request for the sprint's integrated branch, or why not.

    *diff* is the daemon's diff of *row* (``integration_review.read_diff``),
    needed when ``needs_diff``: a protected path in it is merged only with an
    Owner's acknowledgement (``protected_merge.guard``). A self-improvement
    cycle's integration, or one of its sprint, is merged only by a signed-in
    Owner (*merger*; ``self_change.require_owner``).
    """
    self_change.require_owner(
        session,
        row.company_id,
        integration_id=row.id,
        sprint_id=row.sprint_id,
        merger=merger,
    )
    if row.status == "conflict":
        raise PublishRefused(
            409,
            "The integration stopped at a conflict: integrate again once it is resolved",
        )
    if row.status == "tests_failed":
        raise PublishRefused(
            409,
            "The suite failed on the integrated branch: fix it, or merge it yourself",
        )
    if not row.repository or not row.commit:
        raise PublishRefused(409, "This integration names no commit to merge")
    try:
        # Observed from git, never from what the tasks declared.
        protected_merge.guard(
            session, row, diff, integration_review.approving_review(session, row)
        )
    except protected_merge.ProtectedMergeRefused as refused:
        raise PublishRefused(refused.status, refused.detail) from refused
    try:
        # The company may ask for a person's approving review first.
        integration_review.require_approval(session, row)
    except integration_review.IntegrationReviewRefused as refused:
        raise PublishRefused(refused.status, refused.detail) from refused
    return {
        "workspace_root": row.repository,
        "branch": row.branch,
        "commit": row.commit,
        "base": _base(session, row.company_id, row.base),
        "message": (
            f"Merge {row.branch}\n\nFabrique-Sprint: {row.sprint_id}\n"
            f"Fabrique-Integration: {row.id}\n"
        ),
    }


def _observed(reply: dict[str, Any]) -> dict[str, Any]:
    """The daemon's answer, refused unless it names the commits it moved between."""
    facts = {
        "branch": str(reply.get("branch") or ""),
        "base": str(reply.get("base") or ""),
        "commit": str(reply.get("commit") or ""),
        "before": str(reply.get("before") or ""),
        "after": str(reply.get("after") or ""),
    }
    if (
        not reply.get("ok")
        or not all(_SHA.match(facts[k]) for k in ("commit", "before", "after"))
        or not facts["base"]
    ):
        raise PublishRefused(502, "The workspace daemon sent an unreadable answer")
    return {
        **facts,
        "already": bool(reply.get("already")),
        "fast_forward": bool(reply.get("fast_forward")),
    }


def _audit(
    session, company_id, interaction_id, who, action, details, result, agent_id=None
) -> int:
    row = AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=interaction_id,
        agent_id=agent_id or f"human:{who}",
        action=action,
        details={"company_id": str(company_id), **details},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )
    return row.id


def record_run_merge(
    session: Session,
    run: AgentRun,
    reply: dict[str, Any],
    *,
    merged_by: str,
    merged_by_user_id: int | None,
    agent_id: str | None = None,
) -> RunPublication:
    facts = _observed(reply)
    publication = RunPublication(
        company_id=run.company_id,
        agent_run_id=run.id,
        provider=None,
        branch=facts["branch"],
        base=facts["base"],
        commit=facts["commit"],
        url=None,
        how=LOCAL,
        updated=facts["already"],
        published_by=merged_by,
        published_by_user_id=merged_by_user_id,
    )
    session.add(publication)
    session.flush()
    publication.audit_log_id = _audit(
        session,
        run.company_id,
        f"agent-run:{run.external_ref}",
        merged_by,
        "run_merged_locally",
        {"agent_run_id": run.id, "publication_id": publication.id},
        facts,
        agent_id,
    )
    session.add(publication)
    session.commit()
    session.refresh(publication)
    return publication


def record_integration_merge(
    session: Session,
    row: SprintIntegration,
    reply: dict[str, Any],
    *,
    merged_by: str,
    merged_by_user_id: int | None = None,
    agent_id: str | None = None,
) -> SprintIntegration:
    facts = _observed(reply)
    approval = integration_review.approving_review(session, row)
    checked = protected_merge.observation(row) or {}
    protected = checked.get("matched") or []
    audit_id = _audit(
        session,
        row.company_id,
        f"sprint:{row.sprint_id}:integration",
        merged_by,
        "sprint_merged_locally",
        {
            "sprint_id": row.sprint_id,
            "integration_id": row.id,
            "user_id": merged_by_user_id,
            "review_id": approval.id if approval else None,
            "protected_paths": protected,
        },
        facts,
        agent_id,
    )
    merged = {
        **facts,
        "merged_by": merged_by,
        # The account, from the session (the segregation-of-duties report).
        "merged_by_user_id": merged_by_user_id,
        "merged_at": datetime.now(UTC).isoformat(),
        "audit_log_id": audit_id,
        # The person's approving review the merge stood on, when there was one.
        "review_id": approval.id if approval else None,
        # The protected paths the Owner acknowledged in that review, if any.
        "protected_paths": protected,
    }
    # A new dict, so the JSON column is seen as changed.
    row.details = {**(row.details or {}), "local_merge": merged}
    session.add(row)
    session.commit()
    session.refresh(row)
    return row

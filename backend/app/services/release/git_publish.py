"""Publishing a run's branch as a pull request (GitHub) or merge request (GitLab).

With the Git workflow on, a task's deliveries are commits on
``fabrique/task-<id>`` (``services/git_workflow.py``). A Manager publishes
that branch: the workspace daemon pushes it to the repository's ``origin``
with the person's own Git credentials and opens a draft request with ``gh``
or ``glab``, or answers with the page where a person opens one
(``vscode-daemon/git_workspace.py``, ``workspace.git.publish``).

The request's description is the run's evidence as the Verifier observed
it: the verdict, each check, the files the daemon saw written and the
commit, with a ``Fabrique-Run`` trailer that leads back to the run. The
producer's own account (its summary, its declared files) is labelled as a
claim, never mixed with what was observed. The daemon's answer is kept as a
``RunPublication`` and audited.
"""

from __future__ import annotations

from typing import Any

from models.agent_run import AgentRun, Verification
from models.review import RunPublication
from sqlmodel import Session, select

from services.evidence.review_changes import run_changes
from services.loop.run_review import task_of
from services.planning.spec_planning import acceptance_summary
from services.platform.agent_service import AgentService

PUBLISH_HOW = ("gh", "glab", "compare")
# GitHub refuses a description over 65,536 characters; GitLab's limit is higher.
MAX_BODY_CHARS = 60_000
MAX_TITLE_CHARS = 200


class PublishRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _cell(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text.replace("|", "\\|")[:300]


def _latest_verification(session: Session, run: AgentRun) -> Verification | None:
    return session.exec(
        select(Verification)
        .where(
            Verification.agent_run_id == run.id,
            Verification.company_id == run.company_id,
        )
        .order_by(Verification.observed_at.desc(), Verification.id.desc())
    ).first()


def request_title(session: Session, run: AgentRun) -> str:
    item = task_of(session, run)
    title = item.title if item is not None and item.title else run.external_ref
    return " ".join(str(title).split())[:MAX_TITLE_CHARS]


def request_body(session: Session, run: AgentRun, changes: dict[str, Any]) -> str:
    """The request's description: what was observed first, what was claimed apart."""
    git = changes.get("git") or {}
    verification = _latest_verification(session, run)
    lines = [
        f"Delivered through La Fabrique: run {run.id} ({_cell(run.producer)}"
        + (f", {_cell(run.agent_role)}" if run.agent_role else "")
        + ").",
        "",
        "## Observed",
        "",
    ]
    if verification is None:
        lines.append("Not verified yet.")
    else:
        lines.append(f"**Verdict:** {_cell(verification.verdict)}")
        checks = [c for c in verification.checks or [] if isinstance(c, dict)]
        if checks:
            lines += ["", "| Check | Outcome | Observation |", "| --- | --- | --- |"]
            lines += [
                f"| {_cell(c.get('name'))} | {_cell(c.get('outcome'))} | {_cell(c.get('observation'))} |"
                for c in checks
            ]
    acceptance = acceptance_summary(session, task_of(session, run))
    if acceptance:
        lines += ["", acceptance]
    files = changes.get("files") or []
    if files:
        totals = changes.get("totals") or {}
        lines += [
            "",
            (
                f"Files written, as the workspace daemon read them back: {totals.get('files', len(files))}"
                f" (+{totals.get('additions', 0)} −{totals.get('deletions', 0)})"
            ),
            "",
        ]
        lines += [
            f"- `{_cell(f.get('path'))}` {_cell(f.get('change') or 'changed')}"
            f" (+{f.get('additions', 0)} −{f.get('deletions', 0)})"
            for f in files[:100]
        ]
        more = max(0, len(files) - 100) + (changes.get("files_not_shown") or 0)
        if more:
            lines.append(f"- … and {more} more")
    if git.get("commit"):
        lines += [
            "",
            f"Commit `{_cell(git['commit'])}` on `{_cell(git.get('branch'))}`.",
        ]
    declared = changes.get("declared_not_observed") or []
    summary = (run.claims or {}).get("summary")
    if declared or summary:
        lines += ["", "## Claimed by the producer (not observed)", ""]
        if summary:
            lines.append(_cell(summary)[:300])
        if declared:
            lines.append(
                "Declared but not written: "
                + ", ".join(f"`{_cell(p)}`" for p in declared[:50])
            )
    trailer = f"\n\nFabrique-Run: {run.id}\n"
    body = "\n".join(lines)
    return body[: MAX_BODY_CHARS - len(trailer)] + trailer


def publish_spec(session: Session, run: AgentRun) -> dict[str, Any]:
    """The ``workspace.git.publish`` request for *run*, or why it cannot be published."""
    changes = run_changes(session, run)
    git = changes.get("git") or {}
    if not git.get("branch") or not git.get("repository"):
        raise PublishRefused(409, "This run was not delivered on a Git branch")
    base = git.get("base") or ""
    # The commit this run's evidence covers: its own, or the branch as it was
    # when a delivery changed nothing. The daemon refuses a branch that has
    # moved past it, so a later delivery is never published as this run.
    commit = git.get("commit") or git.get("parent")
    if not commit:
        raise PublishRefused(409, "This run's delivery names no commit to publish")
    return {
        "workspace_root": git["repository"],
        "branch": git["branch"],
        "commit": commit,
        # A branch started from the checked-out commit is proposed to the
        # remote's default branch, which the daemon reads.
        "base": "" if base == "HEAD" else base,
        "title": request_title(session, run),
        "body": request_body(session, run, changes),
    }


def record_publication(
    session: Session,
    run: AgentRun,
    reply: dict[str, Any],
    *,
    published_by: str,
    published_by_user_id: int | None,
    agent_id: str | None = None,
) -> RunPublication:
    """Keep what the daemon answered, audited; an answer missing its facts is refused."""
    how = reply.get("how")
    if not reply.get("ok") or how not in PUBLISH_HOW or not reply.get("commit"):
        raise PublishRefused(502, "The workspace daemon sent an unreadable answer")
    url = reply.get("url")
    publication = RunPublication(
        company_id=run.company_id,
        agent_run_id=run.id,
        provider=reply.get("provider"),
        branch=str(reply.get("branch") or ""),
        base=str(reply.get("base") or ""),
        commit=str(reply["commit"]),
        url=url if isinstance(url, str) and url.startswith("https://") else None,
        how=how,
        updated=bool(reply.get("updated")),
        published_by=published_by,
        published_by_user_id=published_by_user_id,
    )
    session.add(publication)
    session.flush()
    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=run.company_id,
        interaction_id=f"agent-run:{run.external_ref}",
        agent_id=agent_id or f"human:{published_by}",
        action="run_published",
        details={
            "company_id": str(run.company_id),
            "agent_run_id": run.id,
            "publication_id": publication.id,
        },
        result={
            "provider": publication.provider,
            "branch": publication.branch,
            "base": publication.base,
            "commit": publication.commit,
            "url": publication.url,
            "how": publication.how,
            "updated": publication.updated,
        },
        decision_signature=None,
        cost_tokens=0,
    )
    publication.audit_log_id = audit_log.id
    session.add(publication)
    session.commit()
    session.refresh(publication)
    return publication

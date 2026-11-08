"""Publishing a sprint's integration branch as one pull or merge request (issue #314).

``services/integration.py`` merged the sprint's done task branches into
``fabrique/sprint-<id>`` and ran the suite on the result. A Manager publishes
that branch the way a task's branch is published (``services/git_publish.py``,
the daemon's ``workspace.git.publish``): pushed to ``origin`` with the
person's own credentials, the request opened as a draft with ``gh`` or
``glab``, or the page where a person opens it.

The description is the record of what ran, as a pull request opened by a
coding agent carries it: what was observed first (the integration's merges,
the suite's result on the integrated branch, and each task's verdict,
checks, criteria verified and files written, as the Verifier observed
them), then each producer's own account, labelled as a claim. The per-task
publication stays available beside it.
"""

from __future__ import annotations

from typing import Any

from models import AgentRun, BacklogItem, Sprint
from models.integration import SprintIntegration
from sqlmodel import Session, select

from services.agent_service import AgentService
from services.git_publish import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    PUBLISH_HOW,
    PublishRefused,
    _cell,
    _latest_verification,
)
from services.review_changes import run_changes
from services.spec_planning import acceptance_summary

MAX_FILES_PER_TASK = 30


def _task_run(session: Session, item: BacklogItem, company_id: int) -> AgentRun | None:
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item.id}",
        )
        .order_by(AgentRun.id.desc())
    ).first()


def _integration_lines(row: SprintIntegration) -> list[str]:
    lines = [f"**Integration:** {_cell(row.status)}", ""]
    merged = row.merged or []
    lines.append(
        f"Merged into `{_cell(row.branch)}` from `{_cell(row.base)}`, in dependency order:"
    )
    lines += [
        f"- task {_cell(m.get('task_id'))}: `{_cell(m.get('branch'))}` → `{_cell(m.get('commit'))[:12]}`"
        for m in merged
    ]
    already = (row.details or {}).get("already_merged") or []
    lines += [f"- `{_cell(a.get('branch'))}` already in" for a in already]
    for key, label in (("missing", "No branch"), ("left_out", "Not done, left out")):
        entries = (row.details or {}).get(key) or []
        if entries:
            lines.append(
                f"{label}: "
                + ", ".join(f"task {_cell(e.get('task_id'))}" for e in entries)
            )
    lines += ["", f"Integrated commit `{_cell(row.commit)}`."]
    test_run = row.test_run or {}
    lines.append("")
    if test_run.get("command"):
        outcome = (
            "timed out"
            if test_run.get("timed_out")
            else f"exit code {_cell(test_run.get('exit_code'))}"
        )
        lines.append(
            f"**Test suite on the integrated branch:** `{_cell(test_run['command'])}`, {outcome}"
            + (
                " (in a sandbox)"
                if (test_run.get("sandbox") or {}).get("mode") not in (None, "host")
                else ""
            )
        )
        failing = test_run.get("failing_tests") or []
        if failing:
            lines.append("Failing: " + ", ".join(f"`{_cell(t)}`" for t in failing[:30]))
    else:
        lines.append(
            f"**Test suite on the integrated branch:** not run ({_cell(test_run.get('reason') or 'no test run')})"
        )
    return lines


def _task_lines(
    session: Session, item: BacklogItem, run: AgentRun | None
) -> tuple[list[str], list[str]]:
    """``(observed, claimed)`` for one task."""
    header = f"### Task {item.id}: {_cell(item.title)}"
    if run is None:
        return [header, "", "No run recorded."], []
    verification = _latest_verification(session, run)
    observed = [
        header,
        "",
        f"Run {run.id}. **Verdict:** {_cell(verification.verdict if verification else 'not verified')}",
    ]
    checks = [
        c
        for c in (verification.checks if verification else None) or []
        if isinstance(c, dict)
    ]
    if checks:
        observed.append(
            "Checks: "
            + ", ".join(
                f"{_cell(c.get('name'))} {_cell(c.get('outcome'))}" for c in checks
            )
        )
    acceptance = acceptance_summary(session, item)
    if acceptance:
        observed.append(acceptance)
    files = run_changes(session, run).get("files") or []
    observed += [
        f"- `{_cell(f.get('path'))}` {_cell(f.get('change') or 'changed')} (+{f.get('additions', 0)} −{f.get('deletions', 0)})"
        for f in files[:MAX_FILES_PER_TASK]
    ]
    if len(files) > MAX_FILES_PER_TASK:
        observed.append(f"- … and {len(files) - MAX_FILES_PER_TASK} more")
    summary = (run.claims or {}).get("summary")
    claimed = (
        [f"- Task {item.id} ({_cell(run.producer)}): {_cell(summary)}"]
        if summary
        else []
    )
    return observed, claimed


def request_body(session: Session, row: SprintIntegration) -> str:
    sprint = session.get(Sprint, row.sprint_id)
    lines = [
        f"Sprint {row.sprint_id} ({_cell(sprint.sprint_name if sprint else '')}) integrated through La Fabrique.",
        "",
        "## Observed",
        "",
        *_integration_lines(row),
    ]
    items = {
        i.id: i
        for i in session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == row.sprint_id)
        ).all()
    }
    claims: list[str] = []
    for merged in row.merged or []:
        item = items.get(merged.get("task_id"))
        if item is None:
            continue
        observed, claimed = _task_lines(
            session, item, _task_run(session, item, row.company_id)
        )
        lines += ["", *observed]
        claims += claimed
    if claims:
        lines += ["", "## Claimed by the producers (not observed)", "", *claims]
    trailer = f"\n\nFabrique-Sprint: {row.sprint_id}\nFabrique-Integration: {row.id}\n"
    body = "\n".join(lines)
    return body[: MAX_BODY_CHARS - len(trailer)] + trailer


def publish_spec(session: Session, row: SprintIntegration) -> dict[str, Any]:
    """The ``workspace.git.publish`` request for the sprint's branch, or why not."""
    if row.status == "conflict":
        raise PublishRefused(
            409,
            "The integration stopped at a conflict: integrate again once it is resolved",
        )
    if not row.repository or not row.commit:
        raise PublishRefused(409, "This integration names no commit to publish")
    sprint = session.get(Sprint, row.sprint_id)
    title = f"Sprint {row.sprint_id}: {sprint.sprint_name if sprint else ''}".strip(
        ": "
    )
    return {
        "workspace_root": row.repository,
        "branch": row.branch,
        # The commit this integration's evidence covers; a branch moved past it is refused.
        "commit": row.commit,
        "base": "" if row.base == "HEAD" else row.base,
        "title": " ".join(title.split())[:MAX_TITLE_CHARS],
        "body": request_body(session, row),
    }


def record_publication(
    session: Session,
    row: SprintIntegration,
    reply: dict[str, Any],
    *,
    published_by: str,
) -> SprintIntegration:
    how = reply.get("how")
    if not reply.get("ok") or how not in PUBLISH_HOW or not reply.get("commit"):
        raise PublishRefused(502, "The workspace daemon sent an unreadable answer")
    url = reply.get("url")
    publication = {
        "provider": reply.get("provider"),
        "branch": str(reply.get("branch") or ""),
        "base": str(reply.get("base") or ""),
        "commit": str(reply["commit"]),
        "url": url if isinstance(url, str) and url.startswith("https://") else None,
        "how": how,
        "updated": bool(reply.get("updated")),
        "published_by": published_by,
    }
    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"sprint:{row.sprint_id}:integration",
        agent_id=f"human:{published_by}",
        action="sprint_published",
        details={
            "company_id": str(row.company_id),
            "sprint_id": row.sprint_id,
            "integration_id": row.id,
        },
        result=publication,
        decision_signature=None,
        cost_tokens=0,
    )
    row.publication = {**publication, "audit_log_id": audit_log.id}
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
